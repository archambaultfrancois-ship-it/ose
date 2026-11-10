"""Real distributed CH/2 workers: feedback, IDs, fencing and callbacks."""
import asyncio
import copy
import json
import os
from pathlib import Path
import tempfile
import time
import unittest

from ose.execution.distributed import Coordinator, Worker, fingerprint, check_plan
from ose.modeling.compiler import build
from ose.transport.secure import connect
from tests.integration import test_distributed as legacy

SDL_ROOT = os.environ.get('SDL_ROOT', str(Path(__file__).resolve().parents[3] / 'sdl'))
A = '''import json
from value import Value
sent = 0
def on_start(ctx):
    ctx.declare_measurement("feedback.count")
    ctx.arm("clock")
def on_clock(ctx, input):
    global sent
    assert input.timed_out and input.message is None
    inbox = ctx.inbox("feedback")
    previous = inbox.get()
    if previous is None and sent: return
    if previous is not None:
        exact = dict(handle=previous.handle, source="b.out")
        if previous.app_id is not None: exact["id"] = previous.app_id
        assert inbox.get(**exact).message.count == sent + 100
        if sent == 2: assert inbox.remove(**exact)
        else: assert inbox.pull(**exact).handle == previous.handle
        assert inbox.get(**exact) is None
        ctx.measure("feedback.count", previous.message.count)
        with open(ctx.parameters["path"], "a") as stream:
            stream.write(json.dumps([previous.message.count, previous.app_id]) + "\\n")
        if ctx.parameters.get("fail"):
            ctx.publish_out(Value(count=999), id=(1 << 64) - 1)
            raise RuntimeError("failed after deferred pull and staged output")
    sent += 1
    ctx.publish_out(Value(count=sent), id=ctx.parameters.get("id"))
    if sent == 3: ctx.disarm("clock")
'''
B = '''import json
from value import Value
def on_input(ctx, input):
    if input.timed_out:
        assert input.message is None
        with open(ctx.parameters["path"] + ".timeout", "a") as stream:
            stream.write(json.dumps([ctx.time_ns, input.due_ns, input.elapsed]) + "\\n")
        return
    assert not input.timed_out and input.source == "a.out"
    with open(ctx.parameters["path"], "a") as stream:
        stream.write(json.dumps([input.message.count, input.app_id, input.handle]) + "\\n")
    ctx.publish_out(Value(count=input.message.count + 100), id=input.app_id)
    return False
'''


class DistributedInputTests(unittest.IsolatedAsyncioTestCase):
    @classmethod
    def setUpClass(cls):
        temporary = tempfile.TemporaryDirectory(prefix='ose distributed inputs ')
        cls.addClassCleanup(temporary.cleanup)
        cls.root = Path(temporary.name)
        (cls.root / 'value.sdl').write_text('message Value {1: required uint32 count;}')
        (cls.root / 'a.py').write_text(A); (cls.root / 'b.py').write_text(B)
        model = dict(version=2, schemas='value.sdl', components={
            'a': dict(inputs=dict(clock=dict(kind='timer', timer=dict(mode='periodic', delay=.04, start='manual'),
                activates=dict(timeout=['out'])), feedback=dict(root='Value', mode='pull')), outputs=dict(out=dict(root='Value'))),
            'b': dict(inputs=dict(input=dict(root='Value', activates=dict(message=['out']))), outputs=dict(out=dict(root='Value')))},
            subscriptions=[dict(id='forward', **{'from': 'a.out', 'to': 'b.input'}, qos=dict(capacity=1, overflow='wait',
                wait_timeout_ms=100, ack='received', retry=dict(ack_timeout_ms=40))),
                dict(id='feedback', **{'from': 'b.out', 'to': 'a.feedback'}, qos=dict(capacity=1, overflow='wait',
                wait_timeout_ms=100, ack='received', retry=dict(ack_timeout_ms=40)))])
        deployment = dict(version=1, execution='distributed', components={name: dict(container=dict(language='python', handler=name + '.py'))
            for name in model['components']})
        (cls.root / 'model.json').write_text(json.dumps(model)); (cls.root / 'deployment.json').write_text(json.dumps(deployment))
        path = build(cls.root / 'model.json', cls.root / 'deployment.json', SDL_ROOT, cls.root / 'build')
        cls.template = json.loads(path.read_text())

    async def asyncSetUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='ose distributed case ')
        self.addCleanup(temporary.cleanup)
        self.case = Path(temporary.name)
        self.plan = copy.deepcopy(self.template)
        for name in self.plan['components']:
            self.plan['components'][name]['parameters'] = dict(path=str(self.case / name), id=(1 << 64) - 1)
        self.workers, self.tasks = {}, {}
        self.coordinator = None
        self.addAsyncCleanup(self.shutdown)

    async def shutdown(self):
        for task in self.tasks.values(): task.cancel()
        await asyncio.gather(*self.tasks.values(), return_exceptions=True)
        if self.coordinator is not None: await self.coordinator.close()

    async def until(self, predicate, timeout=8):
        deadline = time.monotonic() + timeout
        while not predicate():
            for task in self.tasks.values():
                if task.done():
                    task.result(); self.fail('worker stopped')
            if time.monotonic() >= deadline:
                state = {name: {'metrics': dict(worker.metrics), 'deliveries': {
                    key: engine.snapshot() for key, engine in worker.engines.items()}} for name, worker in self.workers.items()}
                if self.coordinator is not None:
                    state['broker'] = {key: engine.snapshot() for key, engine in self.coordinator.engines.items()}
                self.fail('distributed input condition timed out: ' + repr(state))
            await asyncio.sleep(.01)

    async def start_graph(self, routes=('broker', 'broker'), *, tls=False, configure=None):
        for sub, routing in zip(self.plan['subscriptions'], routes): sub['routing'] = routing
        if tls:
            legacy.certificates(self.case, ['coordinator', 'a', 'b', 'observer'])
        context = legacy.tls(self.case, 'coordinator')[0] if tls else None
        self.coordinator = Coordinator(self.plan, context, lease_seconds=.8)
        await self.coordinator.start()
        for name in self.plan['components']:
            server, client = legacy.tls(self.case, name) if tls else (None, None)
            worker = Worker(self.plan, name, '127.0.0.1', self.coordinator.port, server_context=server, client_context=client)
            self.workers[name] = worker
            if configure: configure(worker)
            self.tasks[name] = asyncio.create_task(worker.run())
            await self.until(worker.started.is_set)
        await self.until(lambda: self.coordinator.activated)

    def values(self, name):
        path = self.case / name
        return [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []

    async def finished(self):
        await self.until(lambda: self.workers['a'].metrics['publications'] == 3 and self.workers['b'].metrics['publications'] == 3
            and self.workers['a'].buffers['feedback'].store.size() == 1)
        await self.until(lambda: all(delivery.snapshot().get('acknowledged', 0) == 3 for worker in self.workers.values()
            for delivery in worker.engines.values()) and all(delivery.snapshot().get('acknowledged', 0) == 3
            for delivery in self.coordinator.engines.values()))

    async def check_feedback(self, app_id):
        await self.finished()
        self.assertEqual([row[:2] for row in self.values('b')], [[1, app_id], [2, app_id], [3, app_id]])
        self.assertEqual(len({row[2] for row in self.values('b')}), 3)
        self.assertEqual(self.values('a'), [[101, app_id], [102, app_id]])
        record = self.workers['a'].buffers['feedback'].get()
        self.assertEqual(record.get('app_id'), app_id)
        self.assertEqual('app_id' in record, app_id is not None)
        self.assertIsNone(self.workers['a'].scheduler.next_event(self.workers['a']._input_now()))
        count = self.workers['a'].metrics['completed_ticks']
        await asyncio.sleep(.09)
        self.assertEqual(self.workers['a'].metrics['completed_ticks'], count)

    async def test_broker_feedback_preserves_max_id_and_passive_pull(self):
        await self.start_graph()
        await self.check_feedback((1 << 64) - 1)

    async def test_direct_feedback_preserves_zero_id(self):
        for node in self.plan['components'].values(): node['parameters']['id'] = 0
        await self.start_graph(('direct', 'direct'))
        await self.check_feedback(0)
        self.assertFalse(self.coordinator.engines)
        self.assertGreater(self.workers['a'].metrics['direct_bytes_received'], 0)

    async def test_mixed_feedback_preserves_absent_id(self):
        for node in self.plan['components'].values(): node['parameters'].pop('id')
        await self.start_graph(('direct', 'broker'))
        await self.check_feedback(None)
        self.assertEqual(set(self.coordinator.engines), {'feedback'})

    async def test_feedback_over_mutual_tls(self):
        await self.start_graph(('direct', 'broker'), tls=True)
        await self.check_feedback((1 << 64) - 1)

    async def test_callback_failure_cancels_pull_and_does_not_publish_staged_output(self):
        self.plan['components']['a']['parameters']['fail'] = True
        cancellation = []
        def configure(worker):
            if worker.name != 'a': return
            original = worker.scheduler.complete
            def complete(reference, now_ns, *, success=True):
                original(reference, now_ns, success=success)
                if not success: cancellation.append(worker.buffers['feedback'].store.size())
            worker.scheduler.complete = complete
        await self.start_graph(('direct', 'broker'), configure=configure)
        with self.assertRaises(asyncio.IncompleteReadError):
            await asyncio.wait_for(asyncio.shield(self.tasks['a']), 5)
        self.assertEqual([row[:2] for row in self.values('b')], [[1, (1 << 64) - 1]])
        self.assertEqual(self.workers['a'].metrics['publications'], 1)
        self.assertEqual(cancellation, [1])
        self.assertIsNone(self.workers['a'].scheduler.active)

    async def test_retry_after_lost_direct_response_does_not_reactivate_push(self):
        lost = []
        def configure(worker):
            if worker.name != 'b': return
            original = worker.rpc.handler
            async def receive(peer, header, body):
                result = await original(peer, header, body)
                if header['op'] == 'deliver' and not lost:
                    lost.append(header['id'])
                    raise ValueError('simulate a lost admission response')
                return result
            worker.rpc.handler = receive
        await self.start_graph(('direct', 'broker'), configure=configure)
        await self.check_feedback((1 << 64) - 1)
        self.assertEqual(len(lost), 1)
        self.assertGreater(self.workers['b'].buffers['input'].metrics['duplicates'], 0)
        self.assertEqual(self.workers['b'].metrics['completed_ticks'], 3)

    async def test_retry_after_lost_broker_ack_does_not_reactivate_push(self):
        lost = []
        def configure(worker):
            if worker.name != 'b': return
            original = worker.control
            async def control(op, body=b'', **kwargs):
                if op == 'ack' and not lost:
                    lost.append(kwargs['id'])
                    return {}, None
                return await original(op, body, **kwargs)
            worker.control = control
        await self.start_graph(configure=configure)
        await self.check_feedback((1 << 64) - 1)
        self.assertEqual(len(lost), 1)
        self.assertGreater(self.workers['b'].buffers['input'].metrics['duplicates'], 0)
        self.assertEqual(self.workers['b'].metrics['completed_ticks'], 3)

    async def test_push_inactivity_uses_the_same_callback_without_a_message(self):
        self.plan['components']['b']['inputs']['input']['timeout'] = .4
        await self.start_graph(('direct', 'broker'))
        await self.finished()
        worker = self.workers['b']
        first_due = worker.scheduler.deadlines['input']
        await self.until(lambda: len(self.values('b.timeout')) >= 2)
        first, second = self.values('b.timeout')[:2]
        self.assertEqual(first[1], first_due)
        self.assertGreaterEqual(first[0], first[1])
        self.assertGreaterEqual(second[1] - first[1], 400_000_000)
        self.assertEqual(worker.metrics['publications'], 3)
        self.assertEqual(len(self.values('b')), 3)

    async def test_native_c_workers_share_distributed_pull_and_push_semantics(self):
        from tests.integration import test_c_inputs as native
        model = json.loads((self.root / 'model.json').read_text())
        model['schemas'] = str(self.root / 'value.sdl')
        model['components']['a']['inputs']['clock']['timer'] = dict(mode='periodic', delay=.2)
        for sub in model['subscriptions']:
            sub['qos']['retry']['ack_timeout_ms'] = 200
        (self.case / 'model.json').write_text(json.dumps(model))
        for name, handler in (('a', native.A), ('b', native.B)):
            (self.case / (name + '.c')).write_text(handler)
        deployment = dict(version=1, execution='distributed', components={
            name: dict(container=dict(language='c', handler=name + '.c')) for name in ('a', 'b')})
        (self.case / 'deployment.json').write_text(json.dumps(deployment))
        path = await asyncio.to_thread(build, self.case / 'model.json', self.case / 'deployment.json', SDL_ROOT, self.case / 'build')
        self.plan = json.loads(path.read_text())
        await self.start_graph(('direct', 'broker'))
        await self.finished()
        self.assertEqual(self.workers['a'].metrics['completed_ticks'], 3)
        self.assertEqual(self.workers['b'].metrics['completed_ticks'], 3)
        record = self.workers['a'].buffers['feedback'].get()
        self.assertEqual(record['app_id'], (1 << 64) - 1)
        self.assertEqual(record['payload'], b'\x01\0\0\0g')

    async def test_remote_parameters_logs_measurements_and_internal_recording(self):
        (self.case / 'probe.py').write_text('''from value import Value
def on_start(ctx):
    value = ctx.parameters_settings()
    assert value.count == 40
    value.count = 41
    ctx.set_parameters_settings(value)
    ctx.declare_recording_point("debug.value", Value)
    ctx.declare_measurement("observed.count")
    ctx.log("INFO", "initialized")
def on_clock(ctx, input):
    value = ctx.parameters_settings()
    ctx.publish_out(value)
    ctx.record("debug.value", value)
    ctx.measure("observed.count", value.count)
    value.count += 1
    ctx.set_parameters_settings(value)
    if ctx.tick_index == 2: ctx.disarm("clock")
''')
        node = dict(inputs=dict(clock=dict(kind='timer', timer=dict(mode='periodic', delay=.3),
                    activates=dict(timeout=['out']))), outputs=dict(out=dict(root='Value')),
                    parameter_bindings=dict(settings=dict(path='shared/settings', access='read_write')))
        model = dict(version=2, schemas=str(self.root / 'value.sdl'), components=dict(probe=node), subscriptions=[],
                     shared_parameters={'shared/settings': dict(root='Value', initial=dict(count=40))})
        deployment = dict(version=1, execution='distributed', components=dict(probe=dict(
                          container=dict(language='python', handler='probe.py'))))
        (self.case / 'model.json').write_text(json.dumps(model))
        (self.case / 'deployment.json').write_text(json.dumps(deployment))
        path = build(self.case / 'model.json', self.case / 'deployment.json', SDL_ROOT, self.case / 'build')
        self.plan = json.loads(path.read_text())
        await self.start_graph()
        worker = self.workers['probe']
        self.assertNotIn('probe.clock', worker.recording_points())
        client = await connect('127.0.0.1', self.coordinator.sessions['probe']['port'], self.plan['max_frame'])
        try:
            opened, _ = await client.request('recording_open', points={'probe.record.debug.value': worker.token})
            values = []
            deadline = time.monotonic() + 5
            while len(values) < 2:
                self.assertLess(time.monotonic(), deadline, 'missing distributed recording samples')
                result, body = await asyncio.wait_for(client.request('recording_next', session=opened['session']), 2)
                if result.get('empty'): continue
                desc = self.plan['components']['probe']['outputs']['out']
                value = worker.codec.api.decode_dynamic(worker.codec.api.prepare(desc['catalogue']),
                                                       body[worker.codec.api.ENVELOPE_HEADER_SIZE:])
                values.append(value.fields['count'])
            self.assertEqual(values, [41, 42])
            await self.until(lambda: worker.metrics['completed_ticks'] == 2)
            self.assertEqual(worker.process.measurements.snapshot(), {'observed.count': 42.})
            self.assertEqual(worker.metrics['publications'], 2)
            await self.until(lambda: bool(self.coordinator.logs.records))
            self.assertEqual(self.coordinator.logs.records[0][0]['message'], 'initialized')
        finally:
            await client.close()

    async def test_external_publisher_preserves_ids_and_rejects_invalid_ids_before_admission(self):
        (self.case / 'sink.py').write_text('''import json
def on_input(ctx, input):
    assert not input.timed_out and input.source == "external.signal"
    with open(ctx.parameters["path"], "a") as stream:
        stream.write(json.dumps([input.app_id, input.handle]) + "\\n")
''')
        model = dict(version=2, schemas=str(self.root / 'value.sdl'),
            external_topics={'external.signal': dict(root='Value')}, components=dict(sink=dict(
                inputs=dict(input=dict(root='Value')), outputs={}, parameters=dict(path=str(self.case / 'sink')))),
            subscriptions=[dict(id='external', from_topic='external.signal', to='sink.input',
                                qos=dict(ack='received', retry={}))])
        deployment = dict(version=1, execution='distributed', components=dict(sink=dict(
                          container=dict(language='python', handler='sink.py'))))
        (self.case / 'model.json').write_text(json.dumps(model))
        (self.case / 'deployment.json').write_text(json.dumps(deployment))
        path = build(self.case / 'model.json', self.case / 'deployment.json', SDL_ROOT, self.case / 'build')
        self.plan = json.loads(path.read_text())
        await self.start_graph()
        client = await connect('127.0.0.1', self.coordinator.port, self.plan['max_frame'])
        try:
            session, _ = await client.request('external_open', topics=['external.signal'], plan_id=fingerprint(self.plan))
            desc = self.plan['external_topics']['external.signal']
            body = self.workers['sink'].codec.wrap(desc['catalogue'], b'\x01\0\0\0\1')
            for app_id in (None, True, -1, 1 << 64, '7'):
                with self.subTest(invalid=app_id), self.assertRaises(ValueError):
                    await client.request('external_publish', body, token=session['token'], topic='external.signal', app_id=app_id)
            self.assertEqual(self.coordinator.metrics['external_publications'], 0)
            self.assertEqual(next(iter(self.coordinator.external_peers.values()))['sequence'], 0)
            for extra in ({'app_id': 0}, {'app_id': (1 << 64) - 1}, {}, {'app_id': 0}):
                await client.request('external_publish', body, token=session['token'], topic='external.signal', **extra)
            await self.until(lambda: self.workers['sink'].metrics['completed_ticks'] == 4)
            values = self.values('sink')
            self.assertEqual([row[0] for row in values], [0, (1 << 64) - 1, None, 0])
            self.assertEqual(len({row[1] for row in values}), 4)
        finally:
            await client.close()

    async def test_cold_replacement_uses_new_generation_and_fences_old_direct_sender(self):
        await self.start_graph(('direct', 'direct'))
        await self.check_feedback((1 << 64) - 1)
        old = self.workers['a'].token
        survivor = self.workers['b'].process.pid
        self.tasks['a'].cancel(); await asyncio.gather(self.tasks['a'], return_exceptions=True)
        worker = Worker(self.plan, 'a', '127.0.0.1', self.coordinator.port)
        # Cold restart does not replay messages addressed to the old generation.
        # Let the surviving sender discover the new recipient before restarting
        # this request/response workload.
        original = worker._input_work
        async def after_discovery():
            await self.until(lambda: self.workers['b'].directory['nodes'].get('a', {}).get('token') == worker.token)
            await original()
        worker._input_work = after_discovery
        self.workers['a'] = worker; self.tasks['a'] = asyncio.create_task(worker.run())
        await self.until(lambda: worker.metrics['publications'] == 3 and self.workers['b'].metrics['completed_ticks'] == 6)
        self.assertNotEqual(old, worker.token); self.assertEqual(self.workers['b'].process.pid, survivor)
        with self.assertRaisesRegex(ValueError, 'obsolete'):
            self.coordinator.authorize(self.plan['subscriptions'][0], old, self.workers['b'].token)
        desc = self.plan['components']['a']['outputs']['out']
        body = worker.codec.wrap(desc['catalogue'], b'\x01\0\0\0\1')
        client = await connect('127.0.0.1', self.coordinator.sessions['b']['port'], self.plan['max_frame'])
        try:
            with self.assertRaises(ValueError):
                await client.request('deliver', body, subscription='forward', source='a', topic='a.out', epoch=self.coordinator.epoch,
                    source_token=old, target_token=self.workers['b'].token, id=old + ':99', sequence=99, app_id=(1 << 64) - 1)
        finally:
            await client.close()

    def test_fingerprint_separates_execution_models_and_checks_consistency(self):
        other = copy.deepcopy(self.plan)
        other['version'] = 1
        self.assertNotEqual(fingerprint(self.plan), fingerprint(other))
        with self.assertRaisesRegex(ValueError, 'version 2 application plan'):
            check_plan(other)

