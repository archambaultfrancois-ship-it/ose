"""Operational telemetry through real local and distributed control/data RPCs."""
import asyncio
import copy
import os
import unittest

from ose.execution.adapter import Adapter
from ose.execution.broker import Broker
from ose.execution.distributed import Coordinator, Worker
from ose.top import Observer
from ose.transport.secure import connect
from tests.integration import test_distributed as fixture


@unittest.skipUnless(os.environ.get('SDL_ROOT') and os.path.isdir('/proc/self'), 'requires SDL_ROOT and procfs')
class TelemetryIntegrationTests(unittest.IsolatedAsyncioTestCase):
    setUpClass = classmethod(fixture.DistributedTests.setUpClass.__func__)

    async def test_local_and_distributed_metrics_and_acknowledged_supervision(self):
        for distributed in (False, True):
            with self.subTest(distributed=distributed):
                plan = copy.deepcopy(self.template)
                plan['execution'] = 'distributed' if distributed else 'local'
                server = Coordinator(plan) if distributed else Broker(plan)
                await server.start()
                runtimes, tasks, client = {}, [], None
                try:
                    for name in plan['components']:
                        runtime = Worker(plan, name, '127.0.0.1', server.port) if distributed else Adapter(plan, name)
                        runtimes[name] = runtime
                        tasks.append(asyncio.create_task(runtime.run() if distributed else runtime.run('127.0.0.1', server.port)))
                    async def live():
                        for task in tasks:
                            if task.done():
                                task.result()
                                self.fail('runtime exited')
                    async def activate():
                        while not (server.activated if distributed else len(server.ready) == len(runtimes)):
                            await live()
                            await asyncio.sleep(.02)
                        if not distributed:
                            server.enabled = True
                    await asyncio.wait_for(activate(), 10)
                    client = await connect('127.0.0.1', server.port, 1024**2)
                    async def reports():
                        while True:
                            await live()
                            status, _ = await client.request('status')
                            rows = {row['name']: row for row in Observer().sample(status)['components']}
                            if (rows['source']['timers'].get('clock', {}).get('samples', 0) and
                                rows['middle']['input_wait'].get('input', {}).get('samples', 0) and
                                rows['source']['publication_wait'].get('calls', 0) and
                                all(row['resources'].get('component', {}).get('cpu_percent') is not None for row in rows.values())):
                                return rows
                            await asyncio.sleep(.1)
                    rows = await asyncio.wait_for(reports(), 10)
                    self.assertGreaterEqual(rows['source']['timers']['clock']['max_ms'], 0)
                    self.assertGreaterEqual(rows['middle']['input_wait']['input']['p95_ms'], 0)
                    self.assertGreaterEqual(rows['source']['publication_wait']['p95_ms'], 0)
                    for name, runtime in runtimes.items():
                        resource = rows[name]['resources']['component']
                        self.assertEqual(resource['pid'], runtime.process.pid)
                        if os.path.isdir('/proc/self'):
                            self.assertGreater(resource['rss_bytes'], 0)
                        self.assertGreaterEqual(resource['cpu_percent'], 0)
                    await client.request('component_set', components=['source'], enabled=False, timeout=25)
                    status, _ = await client.request('status')
                    row = next(row for row in Observer().sample(status)['components'] if row['name'] == 'source')
                    self.assertEqual(row['state'], 'DISABLED')
                    self.assertEqual(row['supervision']['pending'], False)
                    await client.request('component_set', components=['source'], enabled=True, timeout=25)
                    await asyncio.wait_for(reports(), 10)
                finally:
                    if client:
                        await client.close()
                    for task in tasks:
                        task.cancel()
                    await asyncio.gather(*tasks, return_exceptions=True)
                    await server.close()
