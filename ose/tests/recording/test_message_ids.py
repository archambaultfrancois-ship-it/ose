"""CENV2 application IDs survive capture and CH2 simulated/live replay."""
import asyncio
import copy
import json
import os
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch

from ose.execution.adapter import Adapter
from ose.execution.broker import Broker
from ose.execution.distributed import Coordinator, Worker
from ose.modeling.compiler import build
from ose.recording.live_replay import publisher, run as replay_live
from ose.recording.recorder import Recorder
from ose.recording.replay import run as replay_simulated
from sdl_recording import RecordingReader, RecordingWriter

IDS = [None, 0, (1 << 64) - 1, 0]
HANDLER = '''import json
def on_input(ctx, ref):
    with open(ctx.parameters["path"], "a") as stream:
        stream.write(json.dumps([ref.message.value, ref.app_id]) + "\\n")
    if ctx.parameters.get("forward"):
        ctx.publish_out(ref.message, id=ref.app_id)
'''


class MessageIDTests(unittest.IsolatedAsyncioTestCase):
    @classmethod
    def setUpClass(cls):
        temporary = tempfile.TemporaryDirectory()
        cls.addClassCleanup(temporary.cleanup)
        cls.root = Path(temporary.name)
        (cls.root / 'value.sdl').write_text('message Value {1: required uint8 value;}')
        (cls.root / 'handler.py').write_text(HANDLER)
        model = dict(version=2, schemas='value.sdl', external_topics={'external.signal': dict(root='Value')},
            components=dict(forward=dict(inputs=dict(input=dict(root='Value', activates=dict(message=['out']))),
                outputs=dict(out=dict(root='Value'))), sink=dict(inputs=dict(input=dict(root='Value')), outputs={})),
            subscriptions=[dict(id='external', from_topic='external.signal', to='forward.input', qos=dict(capacity=16)),
                dict(id='internal', **{'from': 'forward.out', 'to': 'sink.input'}, qos=dict(capacity=16))])
        deployment = dict(version=1, components={name: dict(container=dict(language='python', handler='handler.py'))
                                               for name in model['components']})
        (cls.root / 'model.json').write_text(json.dumps(model))
        (cls.root / 'deployment.json').write_text(json.dumps(deployment))
        path = build(cls.root / 'model.json', cls.root / 'deployment.json', os.environ['SDL_ROOT'], cls.root / 'build')
        cls.template = json.loads(path.read_text())

    async def asyncSetUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.case = Path(temporary.name)
        self.plan = copy.deepcopy(self.template)
        self.plan['build_dir'] = str(self.case)
        for name, node in self.plan['components'].items():
            node['parameters'] = dict(path=str(self.case / name), forward=name == 'forward')
        self.path = self.case / 'plan.json'
        self.tasks = []
        self.server = None
        self.addAsyncCleanup(self.shutdown)

    async def shutdown(self):
        for task in self.tasks:
            task.cancel()
        await asyncio.gather(*self.tasks, return_exceptions=True)
        if self.server:
            await self.server.close()

    def values(self, name='sink'):
        path = self.case / name
        return [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []

    async def until(self, predicate):
        deadline = time.monotonic() + 10
        while not predicate():
            for task in self.tasks:
                if task.done():
                    task.result()
                    self.fail('component stopped')
            self.assertLess(time.monotonic(), deadline, 'message-ID recording timed out')
            await asyncio.sleep(.01)

    async def start(self, distributed=False, direct=False):
        self.plan['execution'] = 'distributed' if distributed else 'local'
        if distributed:
            for sub in self.plan['subscriptions']:
                sub['routing'] = 'direct' if direct and sub['id'] == 'internal' else 'broker'
            self.server = Coordinator(self.plan, lease_seconds=2)
        else:
            self.server = Broker(self.plan)
        await self.server.start()
        self.workers = {}
        for name in self.plan['components']:
            worker = (Worker(self.plan, name, '127.0.0.1', self.server.port) if distributed
                      else Adapter(self.plan, name))
            self.workers[name] = worker
            self.tasks.append(asyncio.create_task(worker.run() if distributed else worker.run('127.0.0.1', self.server.port)))
        if distributed:
            await self.until(lambda: all(worker.started_at for worker in self.workers.values()))
        else:
            await self.until(lambda: len(self.server.ready) == 2)
            self.server.enabled = True
            await self.until(lambda: all(worker.activated_at_ns for worker in self.workers.values()))
        self.path.write_text(json.dumps(self.plan))

    async def check_roundtrip(self, *, distributed=False, direct=False):
        await self.start(distributed, direct)
        recorder = Recorder('127.0.0.1', self.server.port, distributed=distributed)
        await recorder.connect()
        self.addAsyncCleanup(recorder.close)
        points = ['external.signal', 'forward.input', 'forward.out', 'sink.input']
        recorder.selected.update(dict.fromkeys(points))
        desc = self.plan['external_topics']['external.signal']
        codec = self.server.codec
        for format in ('pcapng', 'mcap'):
            path = self.case / ('capture.' + format)
            before = len(self.values())
            async with publisher(self.plan, ['external.signal'], host='127.0.0.1', port=self.server.port,
                                 distributed=distributed) as publish:
                capture = await recorder.start(path, format=format)
                if not distributed:
                    await self.until(lambda: all('input' in worker.component_recording.active_inputs
                                                for worker in self.workers.values()))
                for value, app_id in enumerate(IDS):
                    await publish('external.signal', codec.wrap(desc['catalogue'], bytes((1, value))), app_id=app_id)
                await self.until(lambda: len(self.values()) == before + len(IDS))
                await self.until(lambda: all(capture.topic_counts[point] == len(IDS) for point in points))
                await recorder.stop()
            records = list(RecordingReader(path).iter_records())
            for point in points:
                selected = [record for record in records if record.channel.topic == point]
                self.assertEqual([record.metadata.get('ose.app_id') for record in selected],
                                 [str(value) if value is not None else None for value in IDS], point)
                self.assertEqual([record.payload for record in selected], [bytes((1, i)) for i in range(4)])
            self.assertEqual(len(records), 16)
            result = await replay_live(path, self.path, mappings=['forward.out=external.signal'],
                host='127.0.0.1', port=self.server.port, distributed=distributed,
                insecure=distributed, loops=2, speed=0)
            self.assertEqual(result['published'], 8)
            await self.until(lambda: len(self.values()) == before + 12)
            self.assertEqual(self.values()[before:], [[i, app_id] for i, app_id in enumerate(IDS)] * 3)

    async def test_local_capture_and_live_replay_preserve_absent_zero_duplicate_and_uint64_ids(self):
        await self.check_roundtrip()

    async def test_distributed_broker_capture_and_live_replay_preserve_ids(self):
        await self.check_roundtrip(distributed=True)

    async def test_distributed_direct_capture_and_live_replay_preserve_ids(self):
        await self.check_roundtrip(distributed=True, direct=True)

    async def test_simulated_replay_sorting_repetition_and_parallel_traces_preserve_ids(self):
        self.path.write_text(json.dumps(self.plan))
        desc = self.plan['external_topics']['external.signal']
        for format in ('pcapng', 'mcap'):
            source = self.case / ('source.' + format)
            with RecordingWriter(source, format=format) as writer:
                evolved = desc['catalogue'][:-1] + '2:uint8:extra;}'
                channel = writer.register_channel(topic='source.out', catalogue=evolved, root_type='Value')
                for index, at in enumerate((7, 0, 7, 10)):
                    writer.write_encoded(channel, bytes((1, index, 9)), log_time_ns=at, sequence=0,
                        metadata={'ose.app_id': str(IDS[index])} if IDS[index] is not None else None)
            reference = None
            for workers in (1, 4):
                trace = self.case / 'trace.jsonl'
                await replay_simulated(source, self.path, .001, mappings=['source.out=external.signal'],
                                       loops=2, workers=workers, trace=trace, schema_policy='compatible')
                events = [json.loads(line) for line in trace.read_text().splitlines()]
                for topic in ('external.signal', 'forward.out'):
                    publications = [event for event in events if event.get('topic') == topic]
                    self.assertEqual([event.get('app_id') for event in publications], [0, None, IDS[2], 0] * 2)
                    self.assertEqual(len({event['id'] for event in publications}), 8)
                if reference is None:
                    reference = trace.read_bytes()
                self.assertEqual(trace.read_bytes(), reference)

    async def test_invalid_recorded_ids_fail_preflight_before_any_component_starts(self):
        desc = self.plan['external_topics']['external.signal']
        self.path.write_text(json.dumps(self.plan))
        for value in ('-1', str(1 << 64), '00', '1.0', 'true', ''):
            source = self.case / 'invalid.pcapng'
            with RecordingWriter(source, overwrite=True) as writer:
                channel = writer.register_channel(topic='source.out', catalogue=desc['catalogue'], root_type='Value')
                writer.write_encoded(channel, b'\x01\x01', metadata={'ose.app_id': value})
            with self.subTest(value=value), patch('ose.execution.simulation.run') as simulate:
                with self.assertRaisesRegex(ValueError, 'ose.app_id'):
                    await replay_simulated(source, self.path, .001, mappings=['source.out=external.signal'])
                simulate.assert_not_called()
