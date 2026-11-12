"""Batch capture across local run and simulated/live replay."""
import asyncio
import json
import os
from pathlib import Path
import struct
import subprocess
import sys
import unittest

from ose.recording.batch import destination, select
from ose.recording.replay import run
from ose.execution.simulation import run as simulate
from sdl_recording import RecordingReader
from tests.recording import test_replay as fixture
CAT = fixture.CAT


class BatchTests(unittest.IsolatedAsyncioTestCase):
    setUp = fixture.ReplayTests.setUp
    capture = fixture.ReplayTests.capture

    def records(self, path):
        reader = RecordingReader(path)
        reader.validate()
        return list(reader.iter_records())

    async def test_simulated_replay_records_union_filters_microsteps_and_overwrites(self):
        path, _ = self.capture()
        for format in ('pcapng', 'mcap'):
            output = self.root / ('recorded.' + format)
            output.write_bytes(b'previous capture')
            await run(path, self.plan_path, .001, mappings=['source.value=external.signal'], microsteps=True,
                      record=['sink.*, sequence == 1', 'sink.out, sequence == 3'], record_file=output)
            records = self.records(output)
            outputs = [r for r in records if r.channel.topic == 'sink.out']
            self.assertEqual([r.decode().fields['sequence'] for r in outputs], [1, 3])
            self.assertEqual([r.publish_time_ns for r in outputs], [7, 7])
            self.assertTrue(all(r.publish_time_ns == r.log_time_ns for r in records))
            self.assertTrue(all('ose.microstep' in r.metadata for r in records))
            self.assertTrue(all(r.channel.metadata['ose.clock'] == 'virtual' for r in records))
            summary = await run(output, self.plan_path, .001, mappings=['sink.out=external.signal'])
            self.assertEqual(summary['external_events'], 2)

    async def test_simulated_run_records_inputs_diagnostics_ids_without_changing_trace(self):
        script = self.root / 'sink.py'
        script.write_text('''import os
from ose.execution.input_process import InputSession
from ose.execution.input_ipc import publication_data
from ose.codec.sdl2 import SDL2
s = InputSession(auto_ready=False)
s.declare_recording_point('sample', 'Value', "''' + CAT + '''")
codec = SDL2(os.environ['SDL_ROOT'])
s.ready()
for activation in s.activations():
    _, payload = codec.api.unpack_envelope(activation.record['body'])
    s.record('sample', payload)
    s.send('P', publication_data('out', payload, activation.record.get('app_id')))
''')
        body = self.root / 'input.s2ps'
        body.write_bytes(self.codec.wrap(CAT, b'\x01' + struct.pack('>Qd', 1, 2.)))
        scenario = self.root / 'scenario.json'
        scenario.write_text(json.dumps(dict(version=1, events=[dict(at=1e-6, topic='external.signal',
                                                                   envelope=str(body), app_id=42)])))
        trace = self.root / 'trace.jsonl'
        await simulate(self.plan_path, .001, scenario=scenario, trace=trace, microsteps=True)
        expected = trace.read_bytes()
        output = self.root / 'capture.pcapng'
        await simulate(self.plan_path, .001, scenario=scenario, trace=trace, microsteps=True,
                       record=['*'], record_file=output)
        self.assertEqual(trace.read_bytes(), expected)
        records = self.records(output)
        self.assertEqual({r.channel.topic for r in records},
                         {'external.signal', 'sink.input', 'sink.out', 'sink.record.sample'})
        self.assertEqual({r.publish_time_ns for r in records}, {1000})
        self.assertEqual({r.metadata.get('ose.app_id') for r in records if r.channel.topic != 'sink.record.sample'}, {'42'})

    async def test_simulated_replay_window_is_half_open_and_keeps_original_positions(self):
        path, _ = self.capture(offsets=(0, 10, 20, 20, 30))
        for format in ('pcapng', 'mcap'):
            output = self.root / ('window.' + format)
            result = await run(path, self.plan_path, .001, mappings=['source.value=external.signal'],
                record=['external.*'], record_file=output, record_from='10ns', record_until='30ns')
            self.assertEqual(result['external_events'], 5)
            if format == 'mcap':
                result = await asyncio.to_thread(subprocess.run,
                    [sys.executable, '-m', 'ose.cli', 'replay', str(path), '--plan', str(self.plan_path),
                     '--mode', 'simulated', '--duration', '.001', '--map', 'source.value=external.signal',
                     '--record', 'external.*', '--record-file', str(output),
                     '--record-from', '10ns', '--record-until', '30ns'],
                    capture_output=True, text=True, timeout=20)
                self.assertEqual(result.returncode, 0, result.stderr)
            records = self.records(output)
            self.assertEqual([r.publish_time_ns for r in records], [10, 20, 20])
            self.assertEqual([r.decode().fields['sequence'] for r in records], [2, 3, 4])

    async def test_cli_default_file_and_errors_preserve_inputs(self):
        command = [sys.executable, '-m', 'ose.cli', 'run', '--plan', str(self.plan_path),
                   '--mode', 'simulated', '--duration', '.001']
        env = dict(os.environ)
        env['PYTHONPATH'] = os.pathsep.join(str(Path(p).resolve()) for p in env['PYTHONPATH'].split(os.pathsep))
        for _ in range(2):
            result = await asyncio.to_thread(subprocess.run, command + ['--record', 'sink.out'],
                                            cwd=self.root, env=env, capture_output=True, text=True, timeout=20)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(self.records(self.root / 'capture.pcapng'), [])
        for args in (['--record-file', 'out.mcap'], ['--record', 'absent'],
                     ['--record', 'sink.out, missing_field == 1'], ['--record', 'sink.out,'],
                     ['--record', '*', '--record-file', str(self.plan_path)],
                     ['--record-from', '1s'], ['--record-until', '1s'],
                     ['--record', '*', '--record-from', '2s', '--record-until', '1s']):
            original = self.plan_path.read_bytes()
            result = await asyncio.to_thread(subprocess.run, command + args, capture_output=True, text=True, timeout=20)
            self.assertNotEqual(result.returncode, 0, result.stderr)
            self.assertEqual(self.plan_path.read_bytes(), original)

    async def test_realtime_run_arms_before_first_timer_and_overwrites(self):
        script = self.root / 'sink.py'
        script.write_text('''import struct
from ose.execution.input_process import InputSession
from ose.execution.input_ipc import publication_data
s = InputSession(auto_ready=False)
s.declare_recording_point('sample', 'Value', "''' + CAT + '''")
s.ready()
for index, activation in enumerate(s.activations(), 1):
    payload = b'\\x01' + struct.pack('>Qd', index, float(index))
    s.record('sample', payload)
    s.send('P', publication_data('out', payload))
''')
        self.plan.update(max_frame=1024 * 1024, max_datagram=1200)
        self.plan['external_topics'] = {}
        self.plan['subscriptions'] = []
        self.plan['components']['sink']['inputs'] = {'clock': dict(kind='timer',
            timer=dict(mode='periodic', delay=.02), activates={'timeout': ['out']})}
        self.plan_path.write_text(json.dumps(self.plan))
        for format in ('pcapng', 'mcap'):
            output = self.root / ('live.' + format)
            output.write_bytes(b'old capture')
            window_options = ['--record-from', '50ms', '--record-until', '110ms'] if format == 'mcap' else []
            result = await asyncio.to_thread(subprocess.run,
                [sys.executable, '-m', 'ose.cli', 'run', '--plan', str(self.plan_path), '--duration', '.15',
                 '--record', 'sink.*', '--record-file', str(output), *window_options], capture_output=True, text=True, timeout=30)
            self.assertEqual(result.returncode, 0, result.stderr)
            records = self.records(output)
            self.assertGreaterEqual(len(records), 1)
            self.assertEqual({r.channel.topic for r in records}, {'sink.out', 'sink.record.sample'})
            for topic in ('sink.out', 'sink.record.sample'):
                first = next(r for r in records if r.channel.topic == topic)
                if window_options:
                    self.assertGreater(first.decode().fields['sequence'], 1)
                else:
                    self.assertEqual(first.decode().fields['sequence'], 1)
            self.assertTrue(all(r.log_time_ns >= r.publish_time_ns > 10**18 for r in records))
            if window_options:
                dates = [r.publish_time_ns for r in records]
                self.assertLess(max(dates) - min(dates), 60_000_000)

    async def test_realtime_replay_cli_records_before_injecting(self):
        from ose.execution.broker import Broker
        from ose.execution.adapter import Adapter
        self.plan.update(max_frame=1024 * 1024, max_datagram=1200)
        self.plan_path.write_text(json.dumps(self.plan))
        broker = Broker(self.plan)
        await broker.start()
        task = asyncio.create_task(Adapter(self.plan, 'sink').run('127.0.0.1', broker.port))
        async def close():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            await broker.close()
        self.addAsyncCleanup(close)
        async def ready():
            while 'sink' not in broker.ready:
                if task.done():
                    task.result()
                await asyncio.sleep(.01)
        await asyncio.wait_for(ready(), 10)
        broker.enabled = True
        original, _ = self.capture(offsets=(0, 40_000_000, 80_000_000))
        path = self.root / 'simulated.mcap'
        await run(original, self.plan_path, .1, mappings=['source.value=external.signal'],
                  record=['sink.out'], record_file=path)
        output = self.root / 'replayed.mcap'
        result = await asyncio.to_thread(subprocess.run,
            [sys.executable, '-m', 'ose.cli', 'replay', str(path), '--plan', str(self.plan_path),
             '--map', 'sink.out=external.signal', '--host', '127.0.0.1', '--port', str(broker.port),
             '--record', 'external.*, sequence == 1', '--record', 'external.signal, sequence == 3',
             '--record-file', str(output)], capture_output=True, text=True, timeout=20)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual([r.decode().fields['sequence'] for r in self.records(output)], [1, 3])

    def test_selection_unions_and_unfiltered_clause_overrides(self):
        points = {'sink.out': dict(topic='sink.out', catalogue=CAT, root='Value')}
        clauses = ['sink.*, sequence == 1', 'sink.out, sequence == 3']
        self.assertEqual(select(clauses, points, sdl_root=self.codec.root),
                         {'sink.out': '(sequence == 1) || (sequence == 3)'})
        self.assertEqual(select(clauses + ['sink.out'], points, sdl_root=self.codec.root), {'sink.out': None})
        with self.assertRaisesRegex(ValueError, 'no recording point'):
            select(['other'], points)
        with self.assertRaisesRegex(ValueError, 'requires --record'):
            destination(None, 'capture.mcap')
