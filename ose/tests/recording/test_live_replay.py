"""Cadenced replay into live processes, distributed admission and TLS identity."""
import asyncio
import hashlib
import io
import json
import os
from pathlib import Path
import struct
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

from ose.codec.sdl2 import SDL2
from ose.delivery.policies import SubscriptionPolicy
from ose.execution.distributed import Coordinator, Worker, fingerprint
from ose.modeling.compiler import build
from ose.recording.live_replay import play, run
from ose.execution.simulation.scenario import Event
from ose.transport.secure import connect
from sdl_recording import RecordingWriter
from tests.integration import test_distributed as distributed_fixture

CAT = "SDL2|Value{1:uint64:sequence;2:fl64:value;}"
SINK = '''import json, os, struct
from ose.execution.input_process import InputSession
from ose.codec.sdl2 import SDL2
s = InputSession()
codec = SDL2(os.environ["SDL_ROOT"])
for input in s.activations():
    _, payload = codec.api.unpack_envelope(input.record["body"])
    with open(s.config["parameters"]["path"], "a") as stream:
        stream.write(json.dumps(struct.unpack(">Qd", payload[1:])) + "\\n")
'''


class LiveReplayTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.codec = SDL2(os.environ["SDL_ROOT"])
        self.path = self.root / "observations.jsonl"
        script = self.root / "sink.py"
        script.write_text(SINK)
        desc = dict(root="Value", catalogue=CAT)
        self.plan = dict(version=2, execution="local", sdl_root=str(self.codec.root), build_dir=str(self.root),
            max_frame=1024 * 1024, max_datagram=1200, external_topics={"external.signal": desc},
            components={"sink": dict(execution_model=2, inputs={"input": desc}, outputs={},
                parameters={"path": str(self.path)}, cwd=str(self.root), env={}, command=[sys.executable, str(script)])},
            subscriptions=[dict(id="signal", from_topic="external.signal", routing="broker",
                **{"from": "external.signal", "to": "sink.input"},
                policy=SubscriptionPolicy.resolve(dict(capacity=16, ack="received", retry={})).to_dict())])
        self.plan_path = self.root / "plan.json"
        self.tasks = []

    async def asyncTearDown(self):
        # Stop and reap components while their brokers are still available.
        # Async cleanups registered by each test close those brokers afterward.
        await self.shutdown()

    async def shutdown(self):
        for task in self.tasks:
            task.cancel()
        await asyncio.gather(*self.tasks, return_exceptions=True)

    def capture(self, format="pcapng", offsets=(0, 40_000_000, 80_000_000), *, catalogue=CAT, topic="source.value"):
        path = self.root / ("capture." + format)
        from ose.codec.catalogue import CatalogueTypes
        types = CatalogueTypes(self.codec.api, catalogue)
        with RecordingWriter(path, format=format, overwrite=True) as writer:
            channel = writer.register_channel(topic=topic, catalogue=catalogue, root_type="Value")
            for sequence, offset in enumerate(offsets, 1):
                message = types.types['Value'](sequence=sequence, value=float(sequence))
                writer.write_encoded(channel, self.codec.api.encode(types.context, message),
                                     log_time_ns=1_800_000_000_000_000_000 + offset)
        return path

    async def test_default_realtime_cli_replay_delivers_compatible_target_envelopes(self):
        self.plan_path.write_text(json.dumps(self.plan))
        from ose.execution.broker import Broker
        from ose.execution.adapter import Adapter
        broker = Broker(self.plan)
        await broker.start()
        self.addAsyncCleanup(broker.close)
        self.tasks.append(asyncio.create_task(Adapter(self.plan, 'sink').run('127.0.0.1', broker.port)))
        await self.until(lambda: 'sink' in broker.ready)
        broker.enabled = True
        source = 'SDL2|Earlier{1:bool:unused;}|Value{1:uint64:sequence;2:fl64:value;3?:string:extra;}'
        for format in ('pcapng', 'mcap'):
            process = await asyncio.create_subprocess_exec(sys.executable, '-m', 'ose.cli', 'replay',
                str(self.capture(format, catalogue=source)), '--plan', str(self.plan_path),
                '--host', '127.0.0.1', '--port', str(broker.port), '--speed', '0', '--schema-policy', 'compatible',
                '--map', 'source.value=external.signal', stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
            stdout, stderr = await asyncio.wait_for(process.communicate(), 20)
            self.assertEqual(process.returncode, 0, stderr.decode())
            self.assertEqual(json.loads(stdout)['replay']['schema_adaptation']['adapted_messages'], 3)
        await self.until(lambda: len(self.values()) == 6)
        self.assertEqual(self.values(), [[i, float(i)] for i in (1, 2, 3)] * 2)

    async def test_automatic_mapping_no_matches_never_opens_a_live_publisher(self):
        self.plan_path.write_text(json.dumps(self.plan))
        with patch('ose.recording.live_replay.publisher') as publisher:
            with self.assertRaisesRegex(ValueError, 'no recorded messages match.*--map'):
                await run(self.capture(), self.plan_path)
            publisher.assert_not_called()

    async def test_batch_capture_windows_follow_replay_speed_and_message_steps(self):
        from sdl_recording import RecordingReader
        coordinator, _ = await self.cluster()
        for index, (speed, commands) in enumerate(((0, None), (.5, None), (2, None),
                (1, ['step-messages', 'wait .03', 'step-messages', 'wait .03', 'step-messages']))):
            output = self.root / f'window-{index}.mcap'
            # Live windows select actual deposit times. Leave scheduling/RPC
            # margin on both sides; exact half-open boundaries are tested with
            # the virtual clock rather than a millisecond wall-clock race.
            result = await run(self.capture(offsets=(0, 200_000_000, 400_000_000)), self.plan_path,
                mappings=['source.value=external.signal'], host='127.0.0.1', port=coordinator.port,
                speed=speed, insecure=True, commands=commands, record=['external.signal'],
                record_file=output, record_from='150ms', record_until='300ms')
            self.assertEqual(result['published'], 3)
            records = list(RecordingReader(output).iter_records())
            self.assertEqual([r.decode().fields['sequence'] for r in records], [2], f'speed={speed}, commands={commands}')

    async def test_batch_capture_of_distributed_replay_unions_filters_and_overwrites(self):
        from sdl_recording import RecordingReader
        coordinator, _ = await self.cluster()
        for format in ('pcapng', 'mcap'):
            output = self.root / ('batch.' + format)
            output.write_bytes(b'old capture')
            result = await run(self.capture(format), self.plan_path,
                mappings=['source.value=external.signal'], host='127.0.0.1', port=coordinator.port,
                speed=1, insecure=True, record=['external.*, sequence == 1', 'external.signal, sequence == 3'],
                record_file=output)
            self.assertEqual(result['published'], 3)
            records = list(RecordingReader(output).iter_records())
            self.assertEqual([r.decode().fields['sequence'] for r in records], [1, 3])
            self.assertTrue(all(r.channel.topic == 'external.signal' for r in records))

    async def test_compatible_distributed_replay_and_preflight_rejection(self):
        coordinator, _ = await self.cluster()
        source = CAT[:-1] + '3?:string:extra;}'
        for index, format in enumerate(('pcapng', 'mcap'), 1):
            result = await run(self.capture(format, catalogue=source), self.plan_path,
                mappings=['source.value=external.signal'], host='127.0.0.1', port=coordinator.port,
                speed=0, insecure=True, schema_policy='compatible')
            self.assertEqual(result['refused'], 0)
            self.assertEqual(result['replay']['schema_adaptation']['adapted_messages'], 3)
            # A new publisher fences the previous generation. Drain this
            # session before opening the next one, even at unlimited speed.
            await self.until(lambda: len(self.values()) == 3 * index)
        self.assertEqual(self.values(), [[i, float(i)] for i in (1, 2, 3)] * 2)
        with patch('ose.recording.live_replay.publisher') as publisher:
            for policy, catalogue in (('exact', source), ('compatible', CAT.replace('fl64', 'fl32'))):
                with self.subTest(policy=policy), self.assertRaises(ValueError):
                    await run(self.capture(catalogue=catalogue), self.plan_path, mappings=['source.value=external.signal'],
                        host='127.0.0.1', port=coordinator.port, insecure=True, schema_policy=policy)
            publisher.assert_not_called()
        self.assertEqual(coordinator.metrics['external_publications'], 6)

    def values(self):
        return [json.loads(line) for line in self.path.read_text().splitlines()] if self.path.exists() else []

    async def until(self, condition, timeout=8):
        deadline = time.monotonic() + timeout
        while not condition():
            for task in self.tasks:
                if task.done():
                    task.result()
            if time.monotonic() > deadline:
                self.fail("live replay condition timed out")
            await asyncio.sleep(.01)

    async def cluster(self, context=None, client_context=None, worker_context=None):
        self.plan["execution"] = "distributed"
        self.plan_path.write_text(json.dumps(self.plan))
        coordinator = Coordinator(self.plan, context, lease_seconds=2)
        await coordinator.start()
        self.addAsyncCleanup(coordinator.close)
        worker = Worker(self.plan, "sink", "127.0.0.1", coordinator.port,
                        server_context=worker_context or context, client_context=client_context)
        self.tasks.append(asyncio.create_task(worker.run()))
        await self.until(lambda: coordinator.activated)
        return coordinator, worker

    async def test_local_cli_cadences_both_formats_and_releases_topics(self):
        self.plan_path.write_text(json.dumps(self.plan))
        process = await asyncio.create_subprocess_exec(sys.executable, "-m", "ose.cli", "run",
            "--plan", str(self.plan_path), stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        async def stop():
            if process.returncode is None:
                process.terminate()
            await asyncio.wait_for(process.communicate(), 10)
        self.addAsyncCleanup(stop)
        running = json.loads(await asyncio.wait_for(process.stdout.readline(), 20))
        self.assertTrue(running["running"])
        for format, speed in (("pcapng", .5), ("mcap", 2)):
            automatic = format == 'mcap'
            path = self.capture(format, topic='external.signal' if automatic else 'source.value')
            mapping_options = [] if automatic else ['--map', 'source.value=external.signal']
            client = await asyncio.create_subprocess_exec(sys.executable, "-m", "ose.cli", "replay", str(path),
                "--mode", "realtime", "--plan", str(self.plan_path), "--speed", str(speed),
                "--loops", "2",
                *mapping_options, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
            stdout, stderr = await asyncio.wait_for(client.communicate(), 20)
            self.assertEqual(client.returncode, 0, stderr.decode())
            result = json.loads(stdout)
            self.assertEqual(result['replay']['mapping'],
                             {'external.signal' if automatic else 'source.value': 'external.signal'})
            self.assertEqual(result["published"], 6)
            self.assertEqual(result["completed_loops"], 2)
            self.assertEqual(result["delivery_results"], {"accepted": 6})
            self.assertGreaterEqual(result["elapsed_seconds"], .16 / speed * .9)
        await self.until(lambda: len(self.values()) == 12)
        self.assertEqual(self.values(), [[1, 1.], [2, 2.], [3, 3.]] * 4)

    async def test_distributed_replay_drains_after_close_and_fences_replaced_publisher(self):
        coordinator, worker = await self.cluster()
        path = self.capture()
        result = await run(path, self.plan_path, mappings=["source.value=external.signal"],
                           host="127.0.0.1", port=coordinator.port, speed=0, insecure=True, loops=2)
        await self.until(lambda: len(self.values()) == 6)
        self.assertEqual(result["delivery_results"], {"accepted": 6})
        self.assertEqual(result["completed_loops"], 2)
        self.assertEqual(self.values(), [[1, 1.], [2, 2.], [3, 3.]] * 2)
        self.assertEqual(len(coordinator.external_peers), 0)
        old_token = next(reversed(coordinator.external_generations))
        peer = await connect("127.0.0.1", coordinator.port, self.plan["max_frame"])
        self.addAsyncCleanup(peer.close)
        await peer.request("external_open", topics=["external.signal"], plan_id=fingerprint(self.plan))
        # A new producer does not revoke already admitted data from an earlier one.
        coordinator.authorize(self.plan['subscriptions'][0], old_token, worker.token)

    async def test_infinite_cli_replay_stops_by_script_or_sigterm(self):
        coordinator, _ = await self.cluster()
        path = self.capture()
        for ending in ("script", "sigterm"):
            with self.subTest(ending=ending):
                before = coordinator.metrics["external_publications"]
                arguments = [sys.executable, "-m", "ose.cli", "replay", str(path), "--mode", "realtime",
                    "--plan", str(self.plan_path), "--host", "127.0.0.1", "--port", str(coordinator.port),
                    "--insecure", "--map", "source.value=external.signal", "--loops", "0"]
                if ending == "script":
                    arguments += ["--command", "resume", "--command", "wait .22", "--command", "status", "--command", "stop"]
                process = await asyncio.create_subprocess_exec(*arguments, stdin=asyncio.subprocess.DEVNULL,
                    stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
                try:
                    if ending == "sigterm":
                        await self.until(lambda: coordinator.metrics["external_publications"] >= before + 4)
                        process.terminate()
                    stdout, stderr = await asyncio.wait_for(process.communicate(), 8)
                    self.assertEqual(process.returncode, 130 if ending == "sigterm" else 0, stderr.decode())
                    if ending == "script":
                        summary = json.loads(stdout)
                        self.assertEqual(summary["loops"], 0)
                        self.assertGreaterEqual(summary["completed_loops"], 2)
                        self.assertEqual(summary["stop_reason"], "stopped")
                    await self.until(lambda: not coordinator.external_owners)
                    self.assertFalse(self.tasks[0].done())
                finally:
                    if process.returncode is None:
                        process.kill()
                        await process.communicate()

    async def test_single_message_unlimited_speed_loop_yields_to_commands(self):
        path = self.root / "body.s2ps"
        body = b"body"
        path.write_bytes(body)
        event = Event(0, 0, "external.signal", path, hashlib.sha256(body).hexdigest())
        async def publish(topic, envelope):
            self.assertEqual(envelope, body)
            return {"signal": "accepted"}
        result = await asyncio.wait_for(play([event], publish, speed=0, loops=0,
            commands=["resume", "wait .02", "stop"], output=io.StringIO()), 1)
        self.assertGreater(result["completed_loops"], 1)
        self.assertEqual(result["published"], result["completed_loops"])
        self.assertEqual(result["stop_reason"], "stopped")

    async def test_live_duration_limits_the_whole_repeated_timeline(self):
        path = self.root / "body.s2ps"
        body = b"body"
        path.write_bytes(body)
        events = [Event(at, index, "external.signal", path, hashlib.sha256(body).hexdigest())
                  for index, at in enumerate((0, 40_000_000, 80_000_000))]
        async def publish(topic, envelope):
            return {"signal": "accepted"}
        result = await play(events, publish, speed=0, loops=0, duration=.1)
        self.assertEqual(result["published"], 4)
        self.assertEqual(result["completed_loops"], 1)
        self.assertEqual(result["stop_reason"], "duration")

    async def test_coordinator_refuses_wrong_plan_topic_owner_and_disabled_publishers(self):
        coordinator, _ = await self.cluster()
        first = await connect("127.0.0.1", coordinator.port, self.plan["max_frame"])
        second = await connect("127.0.0.1", coordinator.port, self.plan["max_frame"])
        self.addAsyncCleanup(first.close)
        self.addAsyncCleanup(second.close)
        for topics, plan_id in ((["external.signal"], "wrong"), (["sink.internal"], fingerprint(self.plan))):
            with self.assertRaises(ValueError):
                await first.request("external_open", topics=topics, plan_id=plan_id)
        result, _ = await first.request("external_open", topics=["external.signal"], plan_id=fingerprint(self.plan))
        other, _ = await second.request("external_open", topics=["external.signal"], plan_id=fingerprint(self.plan))
        self.assertNotEqual(result['token'], other['token'])
        with self.assertRaisesRegex(ValueError, "declare publisher"):
            await first.request("external_publish", b"", token=result["token"], topic="sink.internal")
        coordinator.external_limits["enabled"] = False
        with self.assertRaisesRegex(ValueError, "disabled"):
            await first.request("external_publish", b"", token=result["token"], topic="external.signal")

    async def test_cli_message_and_temporal_steps_from_command_file(self):
        coordinator, _ = await self.cluster()
        path = self.capture(offsets=(0, 1_000_000_000, 2_000_000_000))
        script = self.root / 'commands.txt'
        script.write_text('step-messages\nstep-time 1s\nstep-messages\nstatus\nstop\n')
        process = await asyncio.create_subprocess_exec(sys.executable, '-m', 'ose.cli', 'replay', str(path),
            '--plan', str(self.plan_path), '--host', '127.0.0.1', '--port', str(coordinator.port),
            '--insecure', '--map', 'source.value=external.signal', '--speed', '0',
            '--command-file', str(script), stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        stdout, stderr = await asyncio.wait_for(process.communicate(), 8)
        self.assertEqual(process.returncode, 0, stderr.decode())
        self.assertEqual(json.loads(stdout)['published'], 2)
        statuses = [json.loads(line) for line in stderr.splitlines() if line.startswith(b'{')]
        self.assertEqual([s['position_ns'] for s in statuses[:3]], [0, 1_000_000_000, 1_000_000_000])
        self.assertEqual([s['last_command']['messages'] for s in statuses[:3]], [1, 0, 1])
        await self.until(lambda: len(self.values()) == 2)
        self.assertEqual(self.values(), [[1, 1.], [2, 2.]])
        await self.until(lambda: not coordinator.external_owners)

    async def test_interactive_speed_pause_resume_status_and_stop(self):
        from ose.recording.control import ReplayControl
        output = io.StringIO()
        session = ReplayControl("realtime", interactive=True, output=output)
        clock = session.clock
        class Commands:
            def __init__(self, **kwargs):
                self.lines = iter(["pause", "speed 2", "status", "speed -1", "resume", "stop"])
            async def readline(self, prompt):
                return next(self.lines) + "\n"
            def close(self):
                pass
        with patch("ose.recording.control.CommandInput", Commands):
            await session.controls()
        self.assertTrue(clock.stopped)
        self.assertFalse(clock.paused)
        self.assertEqual(clock.speed, 2)
        self.assertIn('"paused": true', output.getvalue())
        self.assertIn("speed must be", output.getvalue())

    async def test_live_cli_breakpoint_preempts_a_unified_temporal_step(self):
        coordinator, _ = await self.cluster()
        path = self.capture(offsets=(0, 10_000_000, 20_000_000))
        process = await asyncio.create_subprocess_exec(sys.executable, '-m', 'ose.cli', 'replay', str(path),
            '--plan', str(self.plan_path), '--host', '127.0.0.1', '--port', str(coordinator.port),
            '--insecure', '--map', 'source.value=external.signal', '--speed', '0',
            '--command', 'break-time 5ms', '--command', 'step 1s',
            '--command', 'step-messages', '--command', 'stop',
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        stdout, stderr = await asyncio.wait_for(process.communicate(), 8)
        self.assertEqual(process.returncode, 0, stderr.decode())
        statuses = [json.loads(line) for line in stderr.splitlines() if line.startswith(b'{')]
        self.assertEqual(statuses[1]['position_ns'], 5_000_000)
        self.assertEqual(statuses[1]['published'], 1)
        self.assertEqual(statuses[1]['last_command']['stop_reason'], 'breakpoint')
        result = json.loads(stdout)
        self.assertEqual(result['published'], 2)
        self.assertEqual(result['last_breakpoint_ns'], 5_000_000)
        await self.until(lambda: len(self.values()) == 2)
        self.assertEqual(self.values(), [[1, 1.], [2, 2.]])

    async def test_cli_command_scripts_run_in_order_and_stop_without_reading_stdin(self):
        coordinator, _ = await self.cluster()
        path = self.capture(offsets=(0, 1_000_000_000, 2_000_000_000))
        cases = [(["pause", "wait .03", "speed .5", "resume", "wait .06", "status"], 1, "stopped"),
                 (["speed 2", "resume", "wait 60"], 3, "complete"),
                 (["speed 2"], 0, "stopped"),
                 (["stop", "invalid"], 0, "stopped")]
        for commands, count, reason in cases:
            with self.subTest(commands=commands):
                arguments = [sys.executable, "-m", "ose.cli", "replay", str(path), "--mode", "realtime",
                    "--plan", str(self.plan_path), "--host", "127.0.0.1", "--port", str(coordinator.port),
                    "--insecure", "--map", "source.value=external.signal", "--interactive"]
                for command in commands:
                    arguments += ["--command", command]
                process = await asyncio.create_subprocess_exec(*arguments, stdin=asyncio.subprocess.DEVNULL,
                    stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
                try:
                    stdout, stderr = await asyncio.wait_for(process.communicate(), 8)
                    self.assertEqual(process.returncode, 0, stderr.decode())
                    summary = json.loads(stdout)
                    self.assertEqual(summary["published"], count)
                    self.assertEqual(summary["stop_reason"], reason)
                    self.assertNotIn("Replay commands:", stderr.decode())
                    if commands[0] == "pause":
                        first = json.loads(stderr.splitlines()[0])
                        self.assertTrue(first["paused"])
                        self.assertEqual(first["published"], 0)
                    await self.until(lambda: not coordinator.external_owners)
                finally:
                    if process.returncode is None:
                        process.kill()
                        await process.communicate()

    async def test_invalid_script_commands_fail_before_first_publication(self):
        from types import SimpleNamespace
        from unittest.mock import AsyncMock
        events = [SimpleNamespace(at=0, topic="external.signal", read=lambda: b"body")]
        for command in ("wait -1", "wait nan", "wait inf", "speed -1", "invalid"):
            with self.subTest(command=command):
                publish = AsyncMock()
                with patch("ose.recording.control.CommandInput", side_effect=AssertionError("stdin must not be read")):
                    with self.assertRaises(ValueError):
                        await play(events, publish, commands=[command], output=io.StringIO())
                publish.assert_not_awaited()

    async def test_cli_rejects_script_errors_and_simulated_speed(self):
        self.plan_path.write_text(json.dumps(self.plan))
        path = self.capture()
        process = await asyncio.create_subprocess_exec(sys.executable, "-m", "ose.cli", "replay", str(path),
            "--mode", "simulated", "--plan", str(self.plan_path), "--duration", "1", "--map", "source.value=external.signal",
            "--speed", "1", stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        _, stderr = await asyncio.wait_for(process.communicate(), 5)
        self.assertEqual(process.returncode, 1)
        self.assertIn("require --mode realtime", stderr.decode())
        coordinator, _ = await self.cluster()
        process = await asyncio.create_subprocess_exec(sys.executable, "-m", "ose.cli", "replay", str(path),
            "--mode", "realtime", "--plan", str(self.plan_path), "--host", "127.0.0.1", "--insecure",
            "--port", str(coordinator.port), "--map", "source.value=external.signal", "--command", "wait -1",
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        _, stderr = await asyncio.wait_for(process.communicate(), 8)
        self.assertEqual(process.returncode, 1)
        self.assertIn("wait requires", stderr.decode())
        self.assertEqual(coordinator.metrics["external_publications"], 0)
        await self.until(lambda: not coordinator.external_owners)

    async def test_distributed_replay_accepts_an_authenticated_producer_identity(self):
        await asyncio.to_thread(distributed_fixture.certificates, self.root, ["coordinator", "sink", "replay", "intruder"])
        tls = lambda name: distributed_fixture.tls(self.root, name)
        coordinator, _ = await self.cluster(tls("coordinator")[0], tls("sink")[1], tls("sink")[0])
        intruder = await connect("127.0.0.1", coordinator.port, self.plan["max_frame"], tls("intruder")[1], "coordinator")
        self.addAsyncCleanup(intruder.close)
        admitted, _ = await intruder.request("external_open", topics=["external.signal"], plan_id=fingerprint(self.plan))
        self.assertTrue(admitted['token'])
        await intruder.request('external_close', token=admitted['token'])
        result = await run(self.capture(), self.plan_path, mappings=["source.value=external.signal"],
            host="127.0.0.1", port=coordinator.port, speed=2, tls_ca=str(self.root / "ca.crt"),
            tls_cert=str(self.root / "replay.crt"), tls_key=str(self.root / "replay.key"))
        await self.until(lambda: len(self.values()) == 3)
        self.assertEqual(result["refused"], 0)

    async def test_cancelled_replay_releases_ownership_without_stopping_workers(self):
        coordinator, _ = await self.cluster()
        replay = asyncio.create_task(run(self.capture(), self.plan_path, mappings=["source.value=external.signal"],
            host="127.0.0.1", port=coordinator.port, speed=.001, insecure=True))
        await self.until(lambda: coordinator.metrics["external_publications"] == 1)
        replay.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await replay
        await self.until(lambda: not coordinator.external_owners)
        self.assertFalse(self.tasks[0].done())

    async def test_first_publication_waits_for_worker_processing_after_ready(self):
        original_work = Worker._work
        async def delayed_work(worker):
            await asyncio.sleep(.15)
            await original_work(worker)
        with patch.object(Worker, "_work", delayed_work):
            coordinator, worker = await self.cluster()
            self.assertEqual(worker.started_at, 0)
            original_publish = coordinator.publish_record
            async def publish(record, **options):
                self.assertGreater(worker.started_at, 0)
                return await original_publish(record, **options)
            with patch.object(coordinator, "publish_record", publish):
                await run(self.capture(offsets=(0,)), self.plan_path, mappings=["source.value=external.signal"],
                    host="127.0.0.1", port=coordinator.port, speed=0, insecure=True)
            await self.until(lambda: len(self.values()) == 1)

    async def test_cli_interactive_pause_speed_resume_eof_and_sigterm(self):
        coordinator, _ = await self.cluster()
        path = self.capture()
        for ending in ("resume", "eof", "sigterm"):
            with self.subTest(ending=ending):
                before = coordinator.metrics["external_publications"]
                process = await asyncio.create_subprocess_exec(sys.executable, "-m", "ose.cli", "replay", str(path),
                    "--mode", "realtime", "--plan", str(self.plan_path), "--speed", ".001", "--interactive",
                    "--host", "127.0.0.1", "--port", str(coordinator.port), "--insecure",
                    "--map", "source.value=external.signal", stdin=asyncio.subprocess.PIPE,
                    stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
                try:
                    process.stdin.write(b"resume\n")
                    await process.stdin.drain()
                    await self.until(lambda: coordinator.metrics["external_publications"] > before)
                    if ending == "sigterm":
                        process.terminate()
                    elif ending == "eof":
                        process.stdin.close()
                    else:
                        process.stdin.write(b"pause\nspeed 2\nstatus\n")
                        await process.stdin.drain()
                        async def paused():
                            while True:
                                line = await process.stderr.readline()
                                if not line:
                                    self.fail("replayer exited before pausing")
                                if line.startswith(b"{"):
                                    status = json.loads(line)
                                    if status.get("paused") and status.get("speed") == 2:
                                        return
                        await asyncio.wait_for(paused(), 5)
                        await asyncio.sleep(.05)
                        self.assertEqual(coordinator.metrics["external_publications"], before + 1)
                        process.stdin.write(b"resume\n")
                        await process.stdin.drain()
                    await asyncio.wait_for(process.wait(), 5)
                    stdout, stderr = await process.communicate()
                    self.assertEqual(process.returncode, 130 if ending == "sigterm" else 0, stderr.decode())
                    if ending != "sigterm":
                        self.assertEqual(json.loads(stdout)["published"], 3 if ending == "resume" else 1)
                    await self.until(lambda: not coordinator.external_owners)
                    self.assertFalse(self.tasks[0].done())
                finally:
                    if process.returncode is None:
                        process.kill()
                        await process.communicate()

    async def test_unavailable_consumer_is_reported_as_refused(self):
        coordinator, _ = await self.cluster()
        self.tasks[0].cancel()
        await asyncio.gather(self.tasks[0], return_exceptions=True)
        result = await run(self.capture(), self.plan_path, mappings=["source.value=external.signal"],
            host="127.0.0.1", port=coordinator.port, speed=0, insecure=True)
        self.assertEqual(result["refused"], 3)
        self.assertEqual(result["delivery_results"], {"unavailable": 3})

    async def test_processing_delay_is_reflected_in_lag_metrics(self):
        from types import SimpleNamespace
        events = [SimpleNamespace(at=at, topic="external.signal", read=lambda: b"body") for at in (0, 10_000_000)]
        async def publish(topic, body):
            await asyncio.sleep(.06)
            return {"signal": "accepted"}
        result = await play(events, publish)
        self.assertGreaterEqual(result["max_lag_seconds"], .04)
        self.assertEqual(result["published"], 2)

    async def test_compiler_accepts_external_distributed_inputs_and_rejects_direct_route(self):
        (self.root / "value.sdl").write_text("message Value {uint64 sequence; fl64 value;}")
        model = dict(version=2, schemas="value.sdl", external_topics={"external.signal": {"root": "Value"}},
            components={"sink": dict(inputs={"input": {"root": "Value"}}, outputs={})},
            subscriptions=[dict(id="signal", from_topic="external.signal", to="sink.input")])
        deployment = dict(version=1, execution="distributed", components={"sink": {"command": [sys.executable, str(self.root / "sink.py")]}})
        model_path, deployment_path = self.root / "model.json", self.root / "deployment.json"
        model_path.write_text(json.dumps(model))
        deployment_path.write_text(json.dumps(deployment))
        result = await asyncio.to_thread(build, model_path, deployment_path, self.codec.root, self.root / "build")
        self.assertIn("external.signal", json.loads(result.read_text())["external_topics"])
        deployment["subscriptions"] = {"signal": {"routing": "direct"}}
        deployment_path.write_text(json.dumps(deployment))
        with self.assertRaisesRegex(ValueError, "external inputs require broker"):
            await asyncio.to_thread(build, model_path, deployment_path, self.codec.root, self.root / "build")
