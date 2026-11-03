"""A waiting control request must leave its connection usable for other RPCs."""
import asyncio
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
