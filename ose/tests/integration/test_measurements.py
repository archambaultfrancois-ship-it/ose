import asyncio
import copy
import json
import os
from pathlib import Path
import signal
import sys
import tempfile
import time
import unittest

from ose.execution.adapter import Adapter
from ose.execution.broker import Broker
from ose.execution.distributed import Coordinator, Worker
from ose.execution.process import start_component, stop_component, tick_events
from ose.measures import sample
from tests.measurement_components import build_component, check_component

SDL_ROOT = os.environ.get('SDL_ROOT')


@unittest.skipUnless(SDL_ROOT, 'set SDL_ROOT')
class MeasurementTests(unittest.IsolatedAsyncioTestCase):
    @classmethod
    def setUpClass(cls):
        cls.directory = tempfile.TemporaryDirectory()
        cls.addClassCleanup(cls.directory.cleanup)
        cls.root = Path(cls.directory.name)
        cls.plan = build_component(cls.root / 'python', 'python', SDL_ROOT)

    async def until(self, condition, task=None):
        deadline = time.monotonic() + 10
        while not condition():
            if task and task.done(): task.result()
            if time.monotonic() > deadline: self.fail('measurement condition timed out')
            await asyncio.sleep(.02)

    async def test_generated_python_c_and_rust_sdk_values_and_restarts(self):
        await check_component(self, self.plan)
        for language in ('c', 'rust'):
            with self.subTest(language=language):
                plan = await asyncio.to_thread(build_component, self.root / language, language, SDL_ROOT)
                await check_component(self, plan)

    async def test_local_and_distributed_status_cli_and_disconnect(self):
        for distributed in (False, True):
            with self.subTest(distributed=distributed):
                plan = copy.deepcopy(self.plan)
                plan['execution'] = 'distributed' if distributed else 'local'
                server = Coordinator(plan) if distributed else Broker(plan)
                await server.start()
                component = Worker(plan, 'source', '127.0.0.1', server.port) if distributed else Adapter(plan, 'source')
                task = asyncio.create_task(component.run() if distributed else component.run('127.0.0.1', server.port))
                try:
                    if not distributed:
                        await self.until(lambda: 'source' in server.ready, task)
                        server.enabled = True
                    await self.until(lambda: sample(server.snapshot())['measurements'].get('source', {}).get('moteur.temperature') is not None, task)
                    command = [sys.executable, '-m', 'ose.cli', 'measure', 'source.moteur.*', '--host', '127.0.0.1',
                               '--port', str(server.port), '--count', '2', '--interval', '.1', '--format', 'jsonl']
                    if distributed: command.append('--insecure')
                    process = await asyncio.create_subprocess_exec(*command, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
                    stdout, stderr = await asyncio.wait_for(process.communicate(), 10)
                    self.assertEqual(process.returncode, 0, stderr.decode())
                    reports = [json.loads(line) for line in stdout.splitlines()]
                    self.assertEqual(len(reports), 2)
                    for report in reports:
                        self.assertIsInstance(report['measurements']['source']['moteur.temperature'], float)
                        self.assertIsNone(report['measurements']['source']['moteur.unpublished'])
                    watch = command.copy()
                    index = watch.index('--count'); del watch[index:index + 2]
                    watch.append('--watch')
                    reader = await asyncio.create_subprocess_exec(*watch, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
                    try:
                        first = await asyncio.wait_for(reader.stdout.readline(), 10)
                        self.assertIn('source', json.loads(first)['measurements'])
                        reader.send_signal(signal.SIGINT)
                        _, errors = await asyncio.wait_for(reader.communicate(), 10)
                        self.assertEqual(reader.returncode, 130, errors.decode())
                    finally:
                        if reader.returncode is None:
                            reader.kill(); await reader.communicate()
                    task.cancel(); await asyncio.gather(task, return_exceptions=True)
                    await self.until(lambda: not sample(server.snapshot())['measurements'])
                    if not distributed:
                        self.assertNotIn('measurements', server.metrics.get('source', {}))
                finally:
                    task.cancel(); await asyncio.gather(task, return_exceptions=True)
                    await server.close()

    async def test_shutdown_diagnostic_does_not_persist_measurements(self):
        path = Path(self.plan['build_dir']) / 'plan.json'
        process = await asyncio.create_subprocess_exec(sys.executable, '-m', 'ose.cli', 'run', '--plan', str(path),
            '--duration', '.3', stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        stdout, stderr = await asyncio.wait_for(process.communicate(), 15)
        self.assertEqual(process.returncode, 0, stderr.decode())
        final = json.loads((path.parent / 'last-status.json').read_text())
        self.assertNotIn('measurements', final['components']['source'])

    async def test_engine_rejects_invalid_declarations_and_values(self):
        code = '''from ose.execution.component import ComponentSession
from ose.execution.ipc import port_data
import struct
s = ComponentSession(auto_ready=False)
case = s.config['parameters']['case']
name = 'bad..name' if case == 'bad_name' else 'moteur.temperature'
s.send('M', port_data(name, b''))
if case == 'duplicate': s.send('M', port_data(name, b''))
if case == 'early_value': s.send('V', port_data(name, struct.pack('>d', 1.)))
s.ready()
for _ in s.ticks():
    if case == 'late_declare': s.send('M', port_data('late', b''))
    else:
        payload = b'bad' if case == 'bad_size' else struct.pack('>d', float('nan') if case == 'nan' else 1.)
        s.send('V', port_data('unknown' if case == 'unknown' else name, payload))
'''
        for kind in ('bad_name', 'duplicate', 'early_value', 'late_declare', 'bad_size', 'nan', 'unknown'):
            with self.subTest(kind=kind):
                node = dict(command=[sys.executable, '-c', code], cwd=str(self.root), parameters={'case': kind})
                if kind in ('bad_name', 'duplicate', 'early_value'):
                    with self.assertRaises(ValueError): await start_component(node)
                else:
                    process, _ = await start_component(node)
                    try:
                        with self.assertRaises(ValueError):
                            _ = [item async for item in tick_events(process, node, {}, 0, 1)]
                    finally:
                        await stop_component(process)
