import asyncio
import json
import os
from pathlib import Path
import sys
import tempfile
import time
import unittest
from ose.execution.adapter import Adapter
from ose.execution.broker import Broker
from ose.execution.distributed import Coordinator, Worker


@unittest.skipUnless(os.environ.get('SDL_ROOT'), 'set SDL_ROOT')
class LogStreamTests(unittest.IsolatedAsyncioTestCase):
    async def test_generated_python_c_and_rust_contexts(self):
        from tests.log_components import build_component, check_component
        with tempfile.TemporaryDirectory() as directory:
            for language in ('python', 'c', 'rust'):
                with self.subTest(language=language):
                    plan = await asyncio.to_thread(build_component, Path(directory) / language, language, os.environ['SDL_ROOT'])
                    await check_component(self, plan)

    async def until(self, condition, task):
        deadline = time.monotonic() + 10
        while not condition():
            if task.done(): task.result()
            if time.monotonic() > deadline: self.fail('log stream timed out')
            await asyncio.sleep(.02)

    async def test_local_and_distributed_sdk_cli_stream_and_retention_after_disconnect(self):
        code = '''from ose.execution.component import ComponentSession
s = ComponentSession(auto_ready=False, timed_ticks=True)
s.log('INFO', 'started', 'lifecycle')
s.ready()
for _ in s.ticks():
    s.log('FATAL', 'Échec moteur', 'detection')
s.log('INFO', 'stopped', 'lifecycle')
'''
        with tempfile.TemporaryDirectory() as directory:
            for distributed in (False, True):
                with self.subTest(distributed=distributed):
                    plan = dict(sdl_root=os.environ['SDL_ROOT'], execution='distributed' if distributed else 'local',
                        max_frame=1024**2, max_datagram=1200, subscriptions=[], components={'source': dict(
                            command=[sys.executable, '-c', code], cwd=directory, inputs={}, outputs={},
                            trigger=dict(mode='periodic', period_ms=50))})
                    server = Coordinator(plan) if distributed else Broker(plan)
                    await server.start()
                    component = Worker(plan, 'source', '127.0.0.1', server.port) if distributed else Adapter(plan, 'source')
                    task = asyncio.create_task(component.run() if distributed else component.run('127.0.0.1', server.port))
                    try:
                        if not distributed:
                            await self.until(lambda: 'source' in server.ready, task)
                            server.enabled = True
                        await self.until(lambda: any(row['level'] == 'FATAL' for row in server.logs.query()['items']), task)
                        command = [sys.executable, '-m', 'ose.cli', 'logs', 'source', '--level', 'FATAL',
                                   '--host', '127.0.0.1', '--port', str(server.port), '--format', 'jsonl']
                        if distributed: command += ['--insecure']
                        reader = await asyncio.create_subprocess_exec(*command, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
                        stdout, stderr = await asyncio.wait_for(reader.communicate(), 10)
                        self.assertEqual(reader.returncode, 0, stderr.decode())
                        rows = [json.loads(line) for line in stdout.splitlines()]
                        self.assertTrue(rows)
                        self.assertTrue(all(row['level'] == 'FATAL' and row['component'] == 'source' for row in rows))
                        self.assertEqual(rows[0]['message'], 'Échec moteur')
                        follow = await asyncio.create_subprocess_exec(*command, '--follow', '--interval', '.1',
                            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
                        try:
                            first = json.loads(await asyncio.wait_for(follow.stdout.readline(), 10))
                            self.assertEqual(first['level'], 'FATAL')
                            seen = int(first['sequence'])
                            while True:
                                newer = json.loads(await asyncio.wait_for(follow.stdout.readline(), 10))
                                if int(newer['sequence']) > seen: break
                        finally:
                            follow.terminate(); await follow.communicate()
                        task.cancel(); await asyncio.gather(task, return_exceptions=True)
                        rows = server.logs.query()['items']
                        self.assertIn('started', [row['message'] for row in rows])
                        self.assertIn('stopped', [row['message'] for row in rows])
                        self.assertTrue(any(row['level'] == 'FATAL' for row in rows))
                    finally:
                        task.cancel(); await asyncio.gather(task, return_exceptions=True)
                        await server.close()

    async def test_fatal_startup_deposit_survives_failed_initialization(self):
        code = '''from ose.execution.component import ComponentSession
s = ComponentSession(auto_ready=False, timed_ticks=True)
s.log('FATAL', 'startup failed', 'lifecycle')
raise SystemExit(1)
'''
        with tempfile.TemporaryDirectory() as directory:
            for distributed in (False, True):
                with self.subTest(distributed=distributed):
                    plan = dict(sdl_root=os.environ['SDL_ROOT'], execution='distributed' if distributed else 'local',
                        max_frame=1024**2, max_datagram=1200, subscriptions=[], components={'source': dict(
                            command=[sys.executable, '-c', code], cwd=directory, inputs={}, outputs={},
                            trigger=dict(mode='periodic', period_ms=50))})
                    server = Coordinator(plan) if distributed else Broker(plan)
                    await server.start()
                    component = Worker(plan, 'source', '127.0.0.1', server.port) if distributed else Adapter(plan, 'source')
                    try:
                        with self.assertRaises((EOFError, asyncio.IncompleteReadError)):
                            await asyncio.wait_for(component.run() if distributed else component.run('127.0.0.1', server.port), 10)
                        rows = server.logs.query(levels=['FATAL'])['items']
                        self.assertEqual([row['message'] for row in rows], ['startup failed'])
                    finally:
                        await server.close()
