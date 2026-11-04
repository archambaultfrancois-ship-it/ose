"""A waiting control request must leave its connection usable for other RPCs."""
import asyncio
import json
from pathlib import Path
import unittest

from ose.execution.broker import Broker
from ose.transport.tcp import Client, RemoteError


class ControlConnectionTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.server = Broker(dict(sdl_root=str(Path(__file__).resolve().parents[3] / 'sdl'),
            max_frame=1024 * 1024, max_datagram=1200, subscriptions=[],
            components={'source': dict(inputs={}, outputs={}, trigger={'mode': 'on_change'})}))
        await self.server.start()
        self.addAsyncCleanup(self.server.close)
        self.client = await Client.connect('127.0.0.1', self.server.port)
        self.addAsyncCleanup(self.client.close)
        await self.client.request('hello', node='source', udp_port=12345)
        await self.client.request('recording_declare', b'SDL2|Diagnostic{}',
                                  name='dsp.value', root='Diagnostic')
        await self.client.request('ready')
        self.server.enabled = True

    async def watch(self, version):
        task = asyncio.create_task(self.client.request('recording_component_state', watch=True, after=version))
        self.addAsyncCleanup(self.cancel, task)
        await asyncio.sleep(.03)
        self.assertFalse(task.done())
        return task

    async def cancel(self, task):
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)

    async def test_recording_watch_does_not_block_rpc_and_tracks_open_stop_disconnect(self):
        first, _ = await self.client.request('recording_component_state', watch=True)
        self.assertEqual(first['active'], [])
        watch = await self.watch(first['version'])
        status, _ = await self.client.request('state', timeout=1)
        self.assertTrue(status['enabled'])
        recorder = await Client.connect('127.0.0.1', self.server.port)
        self.addAsyncCleanup(recorder.close)
        opened, _ = await recorder.request('recording_open',
            points={'source.record.dsp.value': self.server.generations['source']})
        active, _ = await asyncio.wait_for(watch, 1)
        self.assertEqual(active['active'], ['dsp.value'])
        watch = await self.watch(active['version'])
        await recorder.request('recording_stop', session=opened['session'])
        stopped, _ = await asyncio.wait_for(watch, 1)
        self.assertEqual(stopped['active'], [])
        watch = await self.watch(stopped['version'])
        await recorder.request('recording_open',
            points={'source.record.dsp.value': self.server.generations['source']})
        active, _ = await asyncio.wait_for(watch, 1)
        watch = await self.watch(active['version'])
        await recorder.close()
        inactive, _ = await asyncio.wait_for(watch, 1)
        self.assertEqual(inactive['active'], [])
        # Legacy clients still get an immediate unversioned snapshot.
        legacy, _ = await self.client.request('recording_component_state')
        self.assertEqual(legacy['active'], [])

    async def test_duplicate_watch_is_bounded_and_invalid_versions_do_not_poison_connection(self):
        first, _ = await self.client.request('recording_component_state', watch=True)
        watch = await self.watch(first['version'])
        with self.assertRaisesRegex(RemoteError, 'one recording watch'):
            await self.client.request('recording_component_state', watch=True, after=first['version'])
        self.assertFalse(watch.done())
        await self.cancel(watch)
        await self.client.close()
        # Closing with a pending watch must reap its server-side request too.
        async def disconnected():
            while self.server.connections:
                await asyncio.sleep(0)
        await asyncio.wait_for(disconnected(), 1)
        self.client = await Client.connect('127.0.0.1', self.server.port)
        self.addAsyncCleanup(self.client.close)
        await self.client.request('hello', node='source', udp_port=12345)
        for version in (-1, True, '1'):
            with self.subTest(version=version), self.assertRaises(RemoteError):
                await self.client.request('recording_component_state', watch=True, after=version)
        result, _ = await self.client.request('state', timeout=1)
        self.assertTrue(result['enabled'])

    async def test_demand_refresh_on_same_connection_leaves_metrics_and_control_usable(self):
        # Replace the legacy connection with a runtime opting into demand reporting.
        await self.client.close()
        async def disconnected():
            while self.server.peers:
                await asyncio.sleep(0)
        await asyncio.wait_for(disconnected(), 1)
        self.client = await Client.connect('127.0.0.1', self.server.port)
        self.addAsyncCleanup(self.client.close)
        hello, _ = await self.client.request('hello', node='source', udp_port=12345, observe_metrics=True)
        self.assertEqual(hello['reporting']['seconds'], 0)
        await self.client.request('ready')
        await self.client.request('metrics', json.dumps({'ticks': 1}).encode(), details=True, report_version=0)
        age = self.server.metric_times['source']
        events = asyncio.Queue()
        self.client.on_event = lambda header, body: events.put_nowait(header)
        waiting = asyncio.create_task(self.client.request('status', timeout=1))
        self.addAsyncCleanup(self.cancel, waiting)
        event = await asyncio.wait_for(events.get(), .5)
        self.assertEqual(event['event'], 'reporting')
        state, _ = await self.client.request('state', timeout=.2)
        self.assertTrue(state['enabled'])
        await self.client.request('metrics', json.dumps({'running': True}).encode(), details=False)
        self.assertFalse(waiting.done())
        self.assertEqual(self.server.metric_times['source'], age)
        self.assertEqual(self.server.metrics['source']['ticks'], 1)
        await self.client.request('metrics', json.dumps({'ticks': 2}).encode(), details=True,
                                  report_version=event['reporting']['version'])
        status, _ = await waiting
        self.assertEqual(status['components']['source']['ticks'], 2)
        self.assertLess(status['observation']['components']['source']['report_age_ms'], 100)

    async def test_unresponsive_reporter_has_bounded_status_wait_and_truthful_age(self):
        peer = self.server.peers['source'][0]
        self.server.demand.register(peer)
        self.server.demand.timeout = .02
        await self.client.request('metrics', json.dumps({'ticks': 3}).encode())
        original = self.server.metric_times['source']
        await asyncio.sleep(.03)
        await self.client.request('metrics', json.dumps({'log_records': [], 'logs_dropped': 0}).encode())
        self.assertEqual(self.server.metric_times['source'], original)
        status, _ = await self.client.request('status', timeout=.3)
        observation = status['observation']['components']['source']
        self.assertGreater(observation['report_age_ms'], observation['presence_age_ms'])
        self.assertEqual(status['components']['source']['ticks'], 3)
