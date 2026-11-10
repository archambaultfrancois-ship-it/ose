"""Recording replay preflight, exact virtual timing and CLI simulation."""
import asyncio
import hashlib
import json
import os
from pathlib import Path
import struct
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from ose.codec.sdl2 import SDL2
from ose.codec.catalogue import CatalogueTypes
from ose.delivery.policies import SubscriptionPolicy
from ose.recording.replay import recording_events, run
from sdl_recording import RecordingWriter

CAT = "SDL2|Value{1:uint64:sequence;2:fl64:value;}"
OTHER = "SDL2|Value{1:uint64:sequence;2:fl64:value;3:fl64:extra;}"
SINK = '''import os
from ose.execution.input_process import InputSession
from ose.execution.input_ipc import publication_data
from ose.codec.sdl2 import SDL2
s = InputSession()
codec = SDL2(os.environ["SDL_ROOT"])
for input in s.activations():
    _, payload = codec.api.unpack_envelope(input.record["body"])
    s.send("P", publication_data("out", payload, input.record.get("app_id")))
'''


class ReplayTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.codec = SDL2(os.environ["SDL_ROOT"])
        self.topics = {"external.signal": dict(root="Value", catalogue=CAT)}
        self.mapping = {"source.value": "external.signal"}
        script = self.root / "sink.py"
        script.write_text(SINK)
        desc = dict(root="Value", catalogue=CAT)
        self.plan = dict(version=2, sdl_root=str(self.codec.root), build_dir=str(self.root),
            external_topics=self.topics,
            components={"sink": dict(execution_model=2, inputs={"input": dict(desc, activates={"message": ["out"]})}, outputs={"out": desc},
                parameters={}, cwd=str(self.root), env={},
                command=[sys.executable, str(script)])},
            subscriptions=[dict(id="signal", from_topic="external.signal", **{"from": "external.signal", "to": "sink.input"},
                                policy=SubscriptionPolicy.resolve({}).to_dict())])
        self.plan_path = self.root / "plan.json"
        self.plan_path.write_text(json.dumps(self.plan))

    def capture(self, format="pcapng", *, catalogue=CAT, malformed=False, offsets=(7, 0, 7)):
        path = self.root / ("capture." + format)
        base = 1_800_000_000_000_000_000
        with RecordingWriter(path, format=format, validate_payload=not malformed) as writer:
            channel = writer.register_channel(topic="source.value", catalogue=catalogue, root_type="Value")
            ignored = writer.register_channel(topic="ignored", catalogue=CAT, root_type="Value")
            for sequence, offset in enumerate(offsets, 1):
                body = b"\x01" if malformed else b"\x01" + struct.pack(">Qd", sequence, float(sequence))
                if catalogue == OTHER:
                    body += struct.pack(">d", 3.)
                writer.write_encoded(channel, body, log_time_ns=base + offset)
            writer.write_encoded(ignored, b"\x01" + struct.pack(">Qd", 99, 99.), log_time_ns=base - 10)
        return path, base

    def evolved_capture(self, format='pcapng', catalogues=(CAT, OTHER)):
        path = self.root / ('evolved.' + format)
        with RecordingWriter(path, format=format) as writer:
            for index, catalogue in enumerate(catalogues):
                types = CatalogueTypes(self.codec.api, catalogue)
                channel = writer.register_channel(topic='source.value', catalogue=catalogue, root_type='Value')
                fields = types.messages['Value']
                message = types.types['Value'](**{fields[0]['name']: index + 1, fields[1]['name']: float(index + 1)})
                writer.write_encoded(channel, self.codec.api.encode(types.context, message), log_time_ns=100 + index)
        return path

    async def test_compatible_versions_preserve_timing_loops_and_parallel_traces(self):
        shifted = 'SDL2|Earlier{1:bool:unused;}|Value{1:uint64:sequence;2:fl64:old_value;3?:string:extra;}'
        for format in ('pcapng', 'mcap'):
            path = self.evolved_capture(format, (CAT, OTHER, shifted))
            reference = None
            for workers in (1, 4):
                trace = self.root / 'compatible-trace.jsonl'
                summary = await run(path, self.plan_path, 1, mappings=['source.value=external.signal'],
                                    workers=workers, trace=trace, loops=2, schema_policy='compatible')
                outputs = [json.loads(line) for line in trace.read_text().splitlines()
                           if json.loads(line).get('topic') == 'sink.out']
                expected = [self.codec.wrap(CAT, b'\x01' + struct.pack('>Qd', i, float(i))) for i in (1, 2, 3)]
                self.assertEqual([e['time_ns'] for e in outputs], [0, 1, 2, 3, 4, 5])
                self.assertEqual([e['sha256'] for e in outputs], [hashlib.sha256(b).hexdigest() for b in expected] * 2)
                selection = summary['replay']
                self.assertEqual(selection['schema_policy'], 'compatible')
                self.assertEqual(selection['schema_adaptation']['unchanged_messages'], 1)
                self.assertEqual(selection['schema_adaptation']['adapted_messages'], 2)
                self.assertEqual(len(selection['schema_adaptation']['catalogues']), 3)
                self.assertEqual(selection['schema_adaptation']['catalogues'][2]['source_sha256'],
                                 hashlib.sha256(shifted.encode()).hexdigest())
                self.assertEqual(summary['completed_loops'], 2)
                if reference is None:
                    reference = trace.read_bytes()
                self.assertEqual(trace.read_bytes(), reference)

    async def test_late_incompatibility_cleans_prepared_files_without_starting_components(self):
        incompatible = CAT.replace('fl64', 'fl32')
        path = self.evolved_capture(catalogues=(CAT, incompatible))
        from ose.execution.simulation.scenario import Event
        with patch('ose.recording.replay.Event', wraps=Event) as events, patch('ose.execution.simulation.run') as simulate:
            with self.assertRaisesRegex(ValueError, r'source.value -> external.signal.*selected message 2.*101.*FIELD_TYPE_MISMATCH.*Value.value'):
                await run(path, self.plan_path, 1, mappings=['source.value=external.signal'], schema_policy='compatible')
            simulate.assert_not_called()
            self.assertEqual(events.call_count, 1)
            self.assertFalse(events.call_args.args[3].exists())

    async def test_adapted_size_limit_is_checked_before_starting_components(self):
        path, _ = self.capture(offsets=(0,))
        self.topics['external.signal']['catalogue'] = CAT[:-1] + '3?:bool:flag;}'
        self.plan_path.write_text(json.dumps(self.plan))
        original_size = len(self.codec.wrap(CAT, b'\x01' + struct.pack('>Qd', 1, 1.)))
        with patch('ose.recording.replay.MAX_SIZE', original_size), patch('ose.execution.simulation.run') as simulate:
            with self.assertRaisesRegex(ValueError, 'adapted replay envelope exceeds'):
                await run(path, self.plan_path, 1, mappings=['source.value=external.signal'], schema_policy='compatible')
            simulate.assert_not_called()

    async def test_compatible_preflight_keeps_range_validation_and_integrity_checks(self):
        for format in ('pcapng', 'mcap'):
            path = self.evolved_capture(format)
            with recording_events(path, self.topics, self.mapping, self.codec,
                                  start_ns=101, end_ns=102, schema_policy='compatible') as (events, summary):
                self.assertEqual(len(events), 1)
                self.assertEqual(events[0].at, 0)
                self.assertEqual(summary['origin_log_time_ns'], 101)
                self.assertEqual(summary['schema_adaptation']['adapted_messages'], 1)
            path.write_bytes(path.read_bytes()[:-1])
            with patch('ose.execution.simulation.run') as simulate:
                with self.assertRaises(ValueError):
                    await run(path, self.plan_path, 1, mappings=['source.value=external.signal'],
                              schema_policy='compatible', start_ns=100, end_ns=101)
                simulate.assert_not_called()

    async def test_cli_selects_schema_policy_and_default_remains_exact(self):
        path, _ = self.capture(catalogue=OTHER)
        command = [sys.executable, '-m', 'ose.cli', 'replay', str(path), '--plan', str(self.plan_path),
                   '--map', 'source.value=external.signal', '--duration', '.001']
        for options, code in (([], 1), (['--schema-policy', 'exact'], 1), (['--schema-policy', 'compatible'], 0)):
            result = await asyncio.to_thread(subprocess.run, command + options, capture_output=True, text=True, timeout=30)
            self.assertEqual(result.returncode, code, result.stderr)
            if code == 0:
                selection = json.loads(result.stdout)['replay']
                self.assertEqual(selection['schema_adaptation']['adapted_messages'], 3)
            else:
                self.assertIn('schema policy exact', result.stderr)

    async def test_both_formats_preserve_payloads_nanoseconds_and_stable_ties(self):
        for format in ("pcapng", "mcap"):
            path, base = self.capture(format)
            trace = self.root / "trace.jsonl"
            reference = None
            for workers in (1, 4):
                summary = await run(path, self.plan_path, .001, mappings=["source.value=external.signal"],
                                    workers=workers, trace=trace)
                events = [json.loads(line) for line in trace.read_text().splitlines()]
                outputs = [event for event in events if event["event"] == "publication" and event["topic"] == "sink.out"]
                self.assertEqual([event["time_ns"] for event in outputs], [0, 7, 7])
                expected = [self.codec.wrap(CAT, b"\x01" + struct.pack(">Qd", n, float(n))) for n in (2, 1, 3)]
                self.assertEqual([event["sha256"] for event in outputs],
                                 [hashlib.sha256(body).hexdigest() for body in expected])
                self.assertEqual(summary["replay"]["origin_log_time_ns"], base)
                self.assertEqual(summary["replay"]["message_count_by_topic"], {"source.value": 3})
                if reference is None:
                    reference = trace.read_bytes()
                self.assertEqual(reference, trace.read_bytes())

    async def test_range_is_half_open_and_rebases_first_selected_message(self):
        path, base = self.capture()
        with recording_events(path, self.topics, self.mapping, self.codec,
                              start_ns=base, end_ns=base + 7) as (events, selection):
            self.assertEqual([(event.at, event.index) for event in events], [(0, 0)])
            self.assertEqual(selection["message_count"], 1)
        with recording_events(path, self.topics, self.mapping, self.codec,
                              start_ns=base + 7) as (events, _):
            self.assertEqual([event.at for event in events], [0, 0])

    async def test_execution_horizon_excludes_messages_at_its_boundary(self):
        path, _ = self.capture()
        summary = await run(path, self.plan_path, 7e-9, mappings=["source.value=external.signal"])
        self.assertEqual(summary["ticks"], 1)
        self.assertEqual(summary["time_ns"], 7)
        self.assertEqual(summary["replay"]["message_count"], 3)

    async def test_repeated_inputs_have_unique_ids_and_deterministic_traces(self):
        path, _ = self.capture()
        for loops in (2, 0):
            reference = None
            for workers in (1, 4):
                trace = self.root / "loop-trace.jsonl"
                summary = await run(path, self.plan_path, 16e-9, mappings=["source.value=external.signal"],
                                    workers=workers, trace=trace, loops=loops)
                events = [json.loads(line) for line in trace.read_text().splitlines()]
                inputs = [e for e in events if e["event"] == "publication" and e["topic"] == "external.signal"]
                self.assertEqual([e["time_ns"] for e in inputs], [0, 7, 7, 8, 15, 15])
                self.assertEqual(len({e["id"] for e in inputs}), 6)
                self.assertEqual([e["sha256"] for e in inputs[:3]], [e["sha256"] for e in inputs[3:]])
                self.assertEqual(summary["completed_loops"], 2)
                self.assertEqual(summary["replay"]["loop_period_ns"], 8)
                if loops == 0:
                    self.assertEqual(summary["stop_reason"], "duration")
                if reference is None:
                    reference = trace.read_bytes()
                self.assertEqual(reference, trace.read_bytes())

    async def test_single_message_infinite_replay_advances_to_simulation_horizon(self):
        path, _ = self.capture(offsets=(0,))
        summary = await run(path, self.plan_path, 3e-9, mappings=["source.value=external.signal"], loops=0)
        self.assertEqual(summary["external_events"], 3)
        self.assertEqual(summary["completed_loops"], 3)
        self.assertEqual(summary["time_ns"], 3)
        self.assertEqual(summary["stop_reason"], "duration")

    async def test_frame_limit_is_checked_before_starting_components(self):
        path, _ = self.capture()
        with patch("ose.recording.replay.MAX_SIZE", 16), patch("ose.execution.simulation.run") as simulate:
            with self.assertRaisesRegex(ValueError, "frame limit"):
                await run(path, self.plan_path, .001, mappings=["source.value=external.signal"])
            simulate.assert_not_called()

    async def test_invalid_recordings_fail_before_starting_simulation(self):
        for kind in ("catalogue", "malformed", "truncated", "empty", "missing", "range"):
            with self.subTest(kind=kind), tempfile.TemporaryDirectory() as directory:
                self.root = Path(directory)
                path, base = self.capture(catalogue=OTHER if kind == "catalogue" else CAT,
                                          malformed=kind == "malformed")
                if kind == "truncated":
                    path.write_bytes(path.read_bytes()[:-1])
                options = {"mappings": ["absent=external.signal" if kind == "missing" else "source.value=external.signal"]}
                if kind == "empty":
                    options["start_ns"] = base + 100
                if kind == "range":
                    options.update(start_ns=base + 8, end_ns=base)
                with patch("ose.execution.simulation.run") as simulate:
                    with self.assertRaises(ValueError):
                        await run(path, self.plan_path, .001, **options)
                    simulate.assert_not_called()

    async def test_capture_is_protected_from_diagnostic_outputs(self):
        path, _ = self.capture()
        original = path.read_bytes()
        for destination in (path, self.plan_path, self.root / "last-status.json"):
            with self.subTest(destination=destination), self.assertRaisesRegex(ValueError, "overwrite"):
                await run(path, self.plan_path, .001, mappings=["source.value=external.signal"], trace=destination)
        self.assertEqual(path.read_bytes(), original)

    async def test_cli_exposes_replay_and_reports_selection(self):
        path, _ = self.capture("mcap")
        result = await asyncio.to_thread(subprocess.run,
            [sys.executable, "-m", "ose.cli", "replay", str(path), "--plan", str(self.plan_path),
             "--map", "source.value=external.signal", "--duration", ".001"],
            capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)["replay"]["message_count"], 3)

    async def test_generated_python_c_and_rust_containers_decode_adapted_messages(self):
        from tests.replay_schema import check_generated_replay
        for language in ('python', 'c', 'rust'):
            with self.subTest(language=language):
                await check_generated_replay(self, self.root / language, self.codec.root, language)
