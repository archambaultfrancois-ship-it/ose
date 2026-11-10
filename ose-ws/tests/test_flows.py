"""Real OSE previews, bounded projection, session ownership and source cleanup."""
import asyncio
import copy
import json
import socket
import time
import unittest
from types import SimpleNamespace
from unittest.mock import patch

import test_api
from test_core import CATALOGUE, VALUE
from ose.delivery.policies import SubscriptionPolicy
from ose.execution.broker import Broker
from ose.execution.distributed import Coordinator, Worker, fingerprint
from ose.transport.secure import connect
from ose_ws.auth import Session
from ose_ws.schema.adapter import SchemaAdapter
from ose_ws.services.flows import FlowSource, preview


class ProjectionTests(unittest.TestCase):
    def test_large_arrays_nested_fields_exact_integers_and_offsets(self):
        adapter = SchemaAdapter("SDL2|Child{1:uint16:code;}|Root{1:uint64:counter;2+:fl32:samples;3?:Child:child;4:Mode:mode;5:int16[2][2]:matrix;}|#Mode{OFF=0;ON=1;}", "Root")
        value = {"counter": 2**64 - 1, "samples": list(range(16384)), "child": None, "mode": "ON", "matrix": [[1, 2], [3, 4]]}
        payload = adapter.values.encode(value)
        wire = adapter.values.api.pack_envelope(adapter.values.api.catalogue_id(adapter.catalogue), payload)
        views = preview(adapter, wire, {0, 32})
        fields = {field["name"]: field for field in views[0]["children"]}
        self.assertEqual(fields["counter"]["value"], str(2**64 - 1))
        self.assertEqual(fields["child"]["kind"], "absent")
        self.assertEqual(fields["mode"]["value"], "ON")
        self.assertEqual(fields["samples"]["length"], 16384)
        self.assertEqual(len(fields["samples"]["children"]), 16)
        self.assertTrue(fields["samples"]["truncated"])
        self.assertEqual(fields["matrix"]["children"][1]["children"][0]["value"], 3)
        self.assertEqual(views[32]["children"][1]["children"][0]["value"], 32.)
        self.assertLess(len(json.dumps(views[0]).encode()), 64 * 1024)

    def test_non_finite_complex_values_and_string_truncation_are_json_safe(self):
        adapter = SchemaAdapter("SDL2|Root{1:fl64:value;2:c64:phasor;3:string:text;}", "Root")
        api, values = adapter.values.api, adapter.values
        message = values.types['Root'](value=float('nan'), phasor=api.Complex64(real=float('inf'), imag=-float('inf')), text='x' * 5000)
        wire = api.pack_envelope(api.catalogue_id(adapter.catalogue), api.encode(values.context, message))
        fields = preview(adapter, wire, {0})[0]["children"]
        self.assertEqual(fields[0]["value"], "NaN")
        self.assertEqual([child["value"] for child in fields[1]["children"]], ["+Infinity", "-Infinity"])
        self.assertTrue(fields[2]["truncated"])
        json.dumps(fields, allow_nan=False)


class FlowLifecycleTests(unittest.IsolatedAsyncioTestCase):
    async def test_close_stops_source_when_an_rpc_absorbs_cancellation(self):
        entered = asyncio.Event()

        class Client:
            calls = 0
            closed = False

            async def request(self, op, **arguments):
                self.calls += 1
                entered.set()
                try:
                    await asyncio.Future()
                except asyncio.CancelledError:
                    if self.calls != 1:
                        raise
                    # Python 3.10 wait_for can return an already completed RPC
                    # instead of propagating a simultaneous task cancellation.
                    return {'available': True, 'generation': 'generation'}, None

            async def close(self):
                self.closed = True

        manager = SimpleNamespace(collector=SimpleNamespace(error=None, snapshot={'epoch': 'epoch'}))
        source = FlowSource(manager, ('epoch', 'source.value'), 'source.value', None)
        client = source.client = Client()
        source.generation = 'generation'
        source.task = asyncio.create_task(source.run())
        await entered.wait()
        closing = asyncio.create_task(source.close())
        try:
            done, _ = await asyncio.wait({closing}, timeout=1)
            self.assertIn(closing, done, 'Live shutdown must survive an absorbed cancellation')
            self.assertTrue(client.closed)
            self.assertEqual(client.calls, 1)
        finally:
            source.task.cancel()
            await asyncio.gather(source.task, closing, return_exceptions=True)


class FlowAPITests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp = test_api.APITests.asyncSetUp
    until = test_api.APITests.until

    async def link(self, identity='settings-link', topic='source.settings'):
        sub = {"id": identity, "from": topic, "to": "source.input", "policy": SubscriptionPolicy.resolve().to_dict()}
        self.broker.subscriptions[identity] = sub
        self.broker.observation['subscriptions'][identity] = sub
        await self.until(lambda: identity in self.app.state.collector.snapshot.get('observation', {}).get('subscriptions', {}))
        return identity

    async def start_live(self, subscription='settings-link', **options):
        response = await self.client.post('/api/v1/flow-watches', json={
            'subscription': subscription, 'epoch': self.app.state.collector.snapshot['epoch'], 'interval_ms': 250, **options})
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()['id']

    async def publish(self, counter=2**64 - 1):
        adapter = SchemaAdapter(CATALOGUE, 'Settings')
        plain = adapter.convert(copy.deepcopy(VALUE), to_web=False)
        plain['counter'] = counter
        await self.broker.publish('source.settings', self.broker.codec.wrap(CATALOGUE, adapter.values.encode(plain)))

    async def snapshot(self, identity):
        response = await self.client.get('/api/v1/flow-watches/' + identity)
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    async def test_shared_latest_samples_and_release_after_last_reader(self):
        await self.link()
        await self.link('fanout-link')
        first, second = await self.start_live(), await self.start_live('fanout-link')
        self.assertEqual(len(self.app.state.flows.sources), 1)
        self.assertEqual(len(self.broker.recording.sessions), 1)
        self.assertEqual((await self.snapshot(first))['status'], 'waiting')
        for counter in (10, 11, 12):
            await self.publish(counter)
        source = next(iter(self.app.state.flows.sources.values()))
        await self.until(lambda: bool(source.sample) and source.sample['source_sequence'] == '3')
        a, b = await self.snapshot(first), await self.snapshot(second)
        self.assertEqual(a['sample'], b['sample'])
        fields = {v['name']: v for v in a['sample']['value']['children']}
        self.assertEqual(fields['counter']['value'], '12')
        self.assertEqual(a['sample']['generation'], 'generation-one')
        self.assertEqual(self.app.state.recordings.objects, {})
        self.assertEqual((await self.client.delete('/api/v1/flow-watches/' + first)).status_code, 204)
        self.assertEqual(len(self.app.state.flows.sources), 1)
        self.assertEqual((await self.client.delete('/api/v1/flow-watches/' + second)).status_code, 204)
        await self.until(lambda: not self.broker.recording.sessions)
        self.assertFalse(self.app.state.flows.sources)

    async def test_scoped_rights_csrf_owner_and_quota(self):
        await self.link()
        signed_in = self.app.state.auth.sessions[self.client.cookies.get('ose_ws_session')]
        request = {'subscription': 'settings-link', 'epoch': self.broker.epoch}
        with patch.object(signed_in, 'permissions', {'components.read': ['*']}):
            self.assertEqual((await self.client.post('/api/v1/flow-watches', json=request)).status_code, 403)
        with patch.object(signed_in, 'permissions', {'flows.read': ['other.*']}):
            self.assertEqual((await self.client.post('/api/v1/flow-watches', json=request)).status_code, 403)
        with patch.object(signed_in, 'permissions', {'flows.read': ['source.*']}):
            self.assertEqual((await self.client.post('/api/v1/flow-watches', json=request, headers={'X-CSRF-Token': 'wrong'})).status_code, 403)
            first, second = await self.start_live(), await self.start_live()
            self.assertEqual((await self.client.post('/api/v1/flow-watches', json=request)).status_code, 429)
            other = Session('other-session', signed_in.actor, '', time.time(), time.time(), {'admin': ['*']})
            with self.assertRaisesRegex(Exception, 'expired or not found'):
                self.app.state.flows.snapshot(other, first)
            self.assertEqual(len(self.broker.recording.sessions), 1)
            await self.client.delete('/api/v1/flow-watches/' + first)
            await self.client.delete('/api/v1/flow-watches/' + second)

    async def test_expiry_revocation_logout_and_generation_changes(self):
        await self.link()
        first = await self.start_live()
        self.app.state.flows.watches[first]['until'] = 0
        await self.until(lambda: not self.app.state.flows.sources)
        self.assertEqual((await self.client.get('/api/v1/flow-watches/' + first)).status_code, 404)
        first = await self.start_live()
        signed_in = self.app.state.auth.sessions[self.client.cookies.get('ose_ws_session')]
        with patch.object(signed_in, 'permissions', {'components.read': ['*']}):
            self.assertEqual((await self.client.get('/api/v1/flow-watches/' + first)).status_code, 403)
            await self.until(lambda: not self.app.state.flows.sources)
        first = await self.start_live()
        self.broker.generations['source'] = 'replacement'
        await self.until(lambda: self.app.state.collector.snapshot['observation']['components']['source']['generation'] == 'replacement')
        await self.until(lambda: bool(next(iter(self.app.state.flows.sources.values())).error))
        self.assertEqual((await self.snapshot(first))['status'], 'unavailable')
        await self.client.delete('/api/v1/flow-watches/' + first)
        await self.start_live()
        self.assertEqual((await self.client.post('/api/v1/auth/logout')).status_code, 204)
        self.assertFalse(self.app.state.flows.sources)
        await self.until(lambda: not self.broker.recording.sessions)

    async def test_epoch_validation_and_late_open_failure_leave_no_tap(self):
        await self.link()
        response = await self.client.post('/api/v1/flow-watches', json={'subscription': 'settings-link', 'epoch': 'old'})
        self.assertEqual(response.status_code, 409)
        for value in (-1, 1.5, True):
            response = await self.client.post('/api/v1/flow-watches', json={'subscription': 'settings-link', 'epoch': self.broker.epoch, 'array_offset': value})
            self.assertEqual(response.status_code, 422)
        # A real native source refuses unavailable points before opening a tap.
        self.broker.ready.clear()
        self.broker.peers.pop('source')
        response = await self.client.post('/api/v1/flow-watches', json={'subscription': 'settings-link', 'epoch': self.broker.epoch})
        self.assertEqual(response.status_code, 503)
        self.assertFalse(self.app.state.flows.sources)
        self.assertFalse(self.broker.recording.sessions)

    async def test_large_values_offset_renewal_and_byte_budget_failure(self):
        await self.link()
        watch = await self.start_live()
        adapter = SchemaAdapter(CATALOGUE, 'Settings')
        plain = adapter.convert(copy.deepcopy(VALUE), to_web=False)
        plain['samples'] = [2**63 - 1] * 16384
        wire = self.broker.codec.wrap(CATALOGUE, adapter.values.encode(plain))
        await self.broker.publish('source.settings', wire)
        source = next(iter(self.app.state.flows.sources.values()))
        await self.until(lambda: 0 in source.previews)
        first = await self.snapshot(watch)
        arrays = [n for n in first['sample']['value']['children'] if n['name'] == 'samples'][0]
        self.assertEqual(arrays['length'], 16384)
        self.assertEqual(len(arrays['children']), 16)
        self.assertEqual(arrays['children'][0]['value'], str(2**63 - 1))
        self.assertLess(len(json.dumps(first).encode()), 64 * 1024)
        response = await self.client.post('/api/v1/flow-watches', json={'id': watch, 'subscription': 'settings-link', 'epoch': self.broker.epoch, 'array_offset': 32})
        self.assertEqual(response.status_code, 200, response.text)
        await self.until(lambda: 32 in source.previews)
        second = await self.snapshot(watch)
        self.assertEqual(second['sample']['source_sequence'], first['sample']['source_sequence'])
        arrays = [n for n in second['sample']['value']['children'] if n['name'] == 'samples'][0]
        self.assertEqual(arrays['children'][0]['name'], '[32]')
        self.assertEqual(len(self.broker.recording.sessions), 1)
        await self.client.delete('/api/v1/flow-watches/' + watch)
        self.app.state.flows.settings['max_payload_bytes'] = 1024
        watch = await self.start_live()
        source = next(iter(self.app.state.flows.sources.values()))
        await self.broker.publish('source.settings', wire)
        await self.until(lambda: source.error is not None)
        self.assertIn('budget', source.error)
        self.assertEqual((await self.snapshot(watch))['status'], 'unavailable')
        await self.until(lambda: not self.broker.recording.sessions)

    async def switch(self, runtime, distributed=False):
        collector = self.app.state.collector
        await collector.close()
        collector.client = None
        self.app.state.connection.config.update(port=runtime.port, distributed=distributed, insecure=distributed)
        await collector.start()
        await self.until(lambda: collector.snapshot.get('epoch') == runtime.epoch)

    async def test_local_observation_preserves_business_delivery_queue(self):
        source_node = self.broker.plan['components']['source']
        sub = {'id': 'link', 'from': 'source.settings', 'to': 'sink.input', 'policy': SubscriptionPolicy.resolve().to_dict()}
        plan = dict(self.broker.plan, components={'source': source_node, 'sink': {'inputs': {'input': source_node['outputs']['settings']}, 'outputs': {}, 'execution_model': 2}}, subscriptions=[sub])
        broker = Broker(plan)
        await broker.start(); self.addAsyncCleanup(broker.close)
        broker.peers['source'] = (None, None); broker.ready.add('source'); broker.enabled = True
        await self.switch(broker)
        watch = await self.start_live('link')
        adapter = SchemaAdapter(CATALOGUE, 'Settings')
        wire = broker.codec.wrap(CATALOGUE, adapter.values.encode(adapter.convert(copy.deepcopy(VALUE), to_web=False)))
        await broker.publish('source.settings', wire)
        await self.until(lambda: bool(next(iter(self.app.state.flows.sources.values())).previews))
        self.assertEqual((await self.snapshot(watch))['status'], 'live')
        self.assertEqual(broker.engines['link'].snapshot()['depth'], 1)
        self.assertEqual(broker.engines['link'].snapshot()['accepted'], 1)
        self.assertEqual(broker.engines['link'].snapshot().get('acknowledged', 0), 0)

    async def test_distributed_component_broker_and_direct_routes_and_external_topic(self):
        for routing in ('broker', 'direct'):
            sub = {'id': 'link', 'from': 'source.settings', 'to': 'sink.input', 'routing': routing, 'policy': SubscriptionPolicy.resolve().to_dict()}
            source_node = self.broker.plan['components']['source']
            plan = dict(self.broker.plan, execution='distributed', components={'source': source_node, 'sink': {'inputs': {'input': source_node['outputs']['settings']}, 'outputs': {}, 'execution_model': 2}}, subscriptions=[sub])
            # This manually driven worker fixture has no heartbeat task.
            coordinator = Coordinator(plan, lease_seconds=30)
            await coordinator.start(); self.addAsyncCleanup(coordinator.close)
            worker = Worker(plan, 'source', '127.0.0.1', coordinator.port)
            port = await worker.rpc.start('127.0.0.1', 0)
            worker.client = await connect('127.0.0.1', coordinator.port, 1024**2)
            registered, _ = await worker.client.request('register', node='source', host='127.0.0.1', port=port, plan_id=fingerprint(plan))
            worker.token, worker.epoch = registered['token'], registered['epoch']
            await worker.control('ready'); worker.started.set(); coordinator.activated = True
            self.addAsyncCleanup(worker.close, [])
            await self.switch(coordinator, True)
            self.assertTrue(coordinator.recording_points()['source.settings']['available'],
                            f'{routing}: {coordinator.directory()}')
            watch = await self.start_live('link')
            adapter = SchemaAdapter(CATALOGUE, 'Settings')
            await worker.publish('source.settings', worker.codec.wrap(CATALOGUE, adapter.values.encode(adapter.convert(copy.deepcopy(VALUE), to_web=False))))
            await self.until(lambda: bool(next(iter(self.app.state.flows.sources.values())).previews))
            self.assertEqual((await self.snapshot(watch))['status'], 'live')
            self.assertEqual(len(worker.recording.sessions), 1)
            self.assertEqual(len(coordinator.recording.sessions), 0)
            await self.client.delete('/api/v1/flow-watches/' + watch)
            await self.until(lambda: not worker.recording.sessions)
        external = {'id': 'external', 'from': 'source.external', 'from_topic': 'source.external', 'to': 'source.input', 'policy': SubscriptionPolicy.resolve().to_dict()}
        plan = dict(self.broker.plan, execution='distributed', external_topics={'source.external': self.broker.plan['components']['source']['outputs']['settings']}, subscriptions=[external])
        coordinator = Coordinator(plan)
        await coordinator.start(); self.addAsyncCleanup(coordinator.close)
        coordinator.activated = True
        publisher = await connect('127.0.0.1', coordinator.port, 1024**2)
        self.addAsyncCleanup(publisher.close)
        opened, _ = await publisher.request('external_open', plan_id=fingerprint(plan), topics=['source.external'])
        await self.switch(coordinator, True)
        watch = await self.start_live('external')
        adapter = SchemaAdapter(CATALOGUE, 'Settings')
        wire = coordinator.codec.wrap(CATALOGUE, adapter.values.encode(adapter.convert(copy.deepcopy(VALUE), to_web=False)))
        await publisher.request('external_publish', wire, topic='source.external', token=opened['token'])
        await self.until(lambda: bool(next(iter(self.app.state.flows.sources.values())).previews))
        self.assertEqual((await self.snapshot(watch))['status'], 'live')
        source = next(iter(self.app.state.flows.sources.values()))
        await publisher.close()
        await self.until(lambda: source.error is not None)
        stopped = await self.snapshot(watch)
        self.assertEqual(stopped['status'], 'unavailable')
        self.assertEqual(stopped['sample']['source_sequence'], '1')
        await self.until(lambda: not coordinator.recording.sessions)
        await self.client.delete('/api/v1/flow-watches/' + watch)
        await self.until(lambda: not coordinator.recording.sessions)

    async def test_websocket_delivers_samples_and_releases_watch_on_disconnect(self):
        import uvicorn
        from websockets.asyncio.client import connect as websocket_connect
        await self.link()
        watch = await self.start_live()
        listener = socket.socket(); listener.bind(('127.0.0.1', 0)); listener.listen()
        port = listener.getsockname()[1]
        server = uvicorn.Server(uvicorn.Config(self.app, log_level='error', lifespan='off', proxy_headers=False))
        task = asyncio.create_task(server.serve(sockets=[listener]))
        try:
            await self.until(lambda: server.started)
            async with websocket_connect(f'ws://127.0.0.1:{port}/api/v1/live', origin=f'http://127.0.0.1:{port}', additional_headers={'Cookie': f'ose_ws_session={self.client.cookies.get("ose_ws_session")}'}) as ws:
                await ws.send(json.dumps({'action': 'subscribe', 'subscription_id': 'flow', 'stream': 'flow', 'selection': [watch], 'interval_ms': 250}))
                await self.publish()
                for _ in range(20):
                    event = json.loads(await asyncio.wait_for(ws.recv(), 3))
                    if event['type'] == 'flow.sample' and event['data'].get('sample'):
                        self.assertEqual(event['data']['sample']['source_sequence'], '1')
                        break
                else:
                    self.fail('No Live sample received')
            await self.until(lambda: not self.app.state.flows.sources)
        finally:
            server.should_exit = True
            await asyncio.wait_for(task, 5)
            listener.close()
