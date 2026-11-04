"""Check native Linux names across actual SDK initialization and OSE launches."""
import asyncio
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from ose.execution.process import start_component, stop_component
from ose.modeling.compiler import build


SDL_ROOT = os.environ.get('SDL_ROOT')
LANGUAGES = set(os.environ.get('TEST_LANGUAGES', 'python,c,rust,java').split(','))


@unittest.skipUnless(SDL_ROOT and sys.platform.startswith('linux'), 'requires Linux and SDL_ROOT')
class ProcessIdentityTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        environment = patch.dict(os.environ, {
            'PYTHONPATH': str(Path(__file__).resolve().parents[2] / 'src'), 'OSE_PROCESS_PREFIX': 'vw'})
        environment.start()
        self.addCleanup(environment.stop)

    async def test_thread_names_are_native_and_leave_owner_unchanged(self):
        source = '''from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import threading
from ose.execution.identity import identify, initialize_thread, thread_name
identify("web", {"process_names": {"prefix": "vw"}})
barrier = threading.Barrier(2)
def read():
    barrier.wait(timeout=5)
    return Path("/proc/thread-self/comm").read_text().strip()
with ThreadPoolExecutor(max_workers=2, initializer=lambda: initialize_thread(thread_name("io"))) as pool:
    labels = list(pool.map(lambda _: read(), range(2)))
assert len(set(labels)) == 2, labels
assert all(label.startswith("vw-web-io") and len(label) <= 15 for label in labels), labels
assert Path("/proc/self/comm").read_text().strip() == "vw-web"
'''
        result = await asyncio.to_thread(subprocess.run, [sys.executable, '-c', source],
                                         capture_output=True, timeout=15)
        self.assertEqual(result.returncode, 0, result.stderr.decode())

    async def test_sdk_names_are_set_before_ready(self):
        suffixes = {'python': '.py', 'c': '.c', 'rust': '.rs', 'java': '.java', 'ada': '.adb'}
        for language in sorted(LANGUAGES):
            with self.subTest(language=language), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                (root / 'schema.sdl').write_text('message Value {1: uint32 number;}')
                model = {'version': 1, 'schemas': 'schema.sdl', 'subscriptions': [], 'components': {
                    'sensor_1': {'inputs': {}, 'outputs': {}, 'trigger': {'mode': 'periodic', 'period_ms': 100}}}}
                container = {'language': language, 'handler': 'Handler' + suffixes[language]}
                if language in ('java', 'ada'):
                    container['class' if language == 'java' else 'unit'] = 'Handler'
                deployment = {'version': 1, 'process_names': {'prefix': 'vw'},
                              'components': {'sensor_1': {'container': container}}}
                (root / 'model.json').write_text(json.dumps(model))
                (root / 'deployment.json').write_text(json.dumps(deployment))
                path = await asyncio.to_thread(build, root / 'model.json', root / 'deployment.json',
                                               SDL_ROOT, root / 'build')
                plan = json.loads(path.read_text())
                self.assertEqual(plan['process_names']['components']['sensor_1'], 'sensor1')
                process, _ = await start_component(plan['components']['sensor_1'])
                try:
                    self.assertEqual(Path(f'/proc/{process.pid}/comm').read_text().strip(), 'vw-c-sensor1')
                    self.assertIsNone(process.returncode)
                finally:
                    await stop_component(process)
                self.assertEqual(process.returncode, 0)

    async def test_local_broker_adapter_and_component_names(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / 'schema.sdl').write_text('message Value {1: uint32 number;}')
            (root / 'handler.py').write_text('def on_tick(ctx): pass\n')
            model = {'version': 1, 'schemas': 'schema.sdl', 'subscriptions': [], 'components': {
                'sensor_1': {'inputs': {}, 'outputs': {}, 'trigger': {'mode': 'periodic', 'period_ms': 100}}}}
            deployment = {'version': 1, 'process_names': {'prefix': 'vw'}, 'components': {
                'sensor_1': {'container': {'language': 'python', 'handler': 'handler.py'}}}}
            (root / 'model.json').write_text(json.dumps(model))
            (root / 'deployment.json').write_text(json.dumps(deployment))
            plan = await asyncio.to_thread(build, root / 'model.json', root / 'deployment.json', SDL_ROOT, root / 'build')
            env = dict(os.environ, OSE_PROCESS_PREFIX='vw2')
            process = await asyncio.create_subprocess_exec(sys.executable, '-m', 'ose.cli', 'run', '--plan', str(plan),
                '--duration', '0.5', env=env, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
            try:
                ready = json.loads(await asyncio.wait_for(process.stdout.readline(), 15))
                self.assertTrue(ready['running'])
                self.assertEqual(Path(f'/proc/{process.pid}/comm').read_text().strip(), 'vw2-broker')
                stdout, stderr = await asyncio.wait_for(process.communicate(), 15)
                self.assertEqual(process.returncode, 0, stderr.decode())
                status = json.loads((root / 'build/last-status.json').read_text())
                identity = status['components']['sensor_1']['process']
                self.assertEqual(identity['name'], 'vw2-a-sensor1')
                self.assertEqual(identity['child']['name'], 'vw2-c-sensor1')
                self.assertEqual(identity['ppid'], process.pid)
                self.assertEqual(identity['child']['ppid'], identity['pid'])
            finally:
                if process.returncode is None:
                    process.terminate()
                    await process.wait()
