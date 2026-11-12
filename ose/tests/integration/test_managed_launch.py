"""Managed launches work on a standalone application, without Vibration Watch."""
import asyncio
import importlib.util
import json
import os
from pathlib import Path
import signal
import sys
import tempfile
import unittest

from ose.modeling.compiler import build

SDL_ROOT = os.environ.get('SDL_ROOT')


@unittest.skipUnless(SDL_ROOT and importlib.util.find_spec('inotify_simple'), 'requires SDL_ROOT and the sync extra')
class ManagedLaunchTests(unittest.IsolatedAsyncioTestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory(prefix='ose managed launch ')
        cls.addClassCleanup(cls.temporary.cleanup)
        cls.root = Path(cls.temporary.name)
        (cls.root / 'settings.sdl').write_text('message Settings { fl64 value; }')
        (cls.root / 'handler.py').write_text('''from pathlib import Path
def on_clock(ctx, input):
    Path('observed.txt').write_text(str(ctx.parameters_settings().value))
''')
        model = dict(version=2, schemas='settings.sdl', subscriptions=[],
            shared_parameters={'control/settings': dict(root='Settings', initial={'value': 1.})},
            components={'probe': dict(inputs={'clock': dict(kind='timer', timer=dict(mode='periodic', delay=.05))},
                outputs={}, parameter_bindings={'settings': dict(path='control/settings', access='read')})})
        deployment = dict(version=1, components={'probe': dict(container=dict(language='python', handler='handler.py'))},
            parameter_clients={'maintenance': dict(token='test-maintenance', read=['control/settings'], write=['control/settings'])})
        (cls.root / 'model.json').write_text(json.dumps(model))
        cls.plans = {}
        for mode in ('local', 'distributed'):
            deployment['execution'] = mode
            (cls.root / 'deployment.json').write_text(json.dumps(deployment))
            cls.plans[mode] = build(cls.root / 'model.json', cls.root / 'deployment.json', SDL_ROOT,
                                    cls.root / 'build' / mode)
        (cls.root / 'Makefile').write_text('.PHONY: build\nbuild:\n\t@touch built\n')

    async def start(self, mode, directory, *options):
        environment = {key: value for key, value in os.environ.items() if not key.startswith('OSE_')}
        source = str(Path(__file__).resolve().parents[2] / 'src')
        environment['PYTHONPATH'] = source + os.pathsep + environment.get('PYTHONPATH', '')
        command = (['app', '--root', str(self.root), 'distributed'] if mode == 'distributed' else ['run'])
        log = directory / 'launch.log'
        stream = log.open('w')
        self.addCleanup(stream.close)
        process = await asyncio.create_subprocess_exec(sys.executable, '-m', 'ose.cli', *command,
            '--plan', str(self.plans[mode]), '--duration', '30', '--params-sync', 'parameters',
            '--params-client', 'maintenance', *options, cwd=directory, env=environment, stdout=stream, stderr=stream)
        async def cleanup():
            if process.returncode is None:
                process.terminate()
                try:
                    await asyncio.wait_for(process.wait(), 15)
                except asyncio.TimeoutError:
                    process.kill()
                    await process.wait()
        self.addAsyncCleanup(cleanup)
        return process, log

    async def until(self, predicate, process, log):
        async def wait():
            while not predicate():
                self.assertIsNone(process.returncode, log.read_text())
                await asyncio.sleep(.05)
        await asyncio.wait_for(wait(), 30)

    def children(self, process):
        path = Path(f'/proc/{process.pid}/task/{process.pid}/children')
        if path.exists():
            return [int(pid) for pid in path.read_text().split()]
        # Android kernels may omit the per-task children file.
        children = []
        for path in Path('/proc').glob('[0-9]*/stat'):
            try:
                fields = path.read_text().rsplit(')', 1)[1].split()
                if int(fields[1]) == process.pid:
                    children.append(int(path.parent.name))
            except (OSError, ValueError):
                continue
        return sorted(children)

    async def test_initial_files_edits_and_signal_cleanup_in_both_modes(self):
        for mode in self.plans:
            with self.subTest(mode=mode), tempfile.TemporaryDirectory() as temporary:
                directory = Path(temporary)
                observed = self.root / 'observed.txt'
                observed.unlink(missing_ok=True)
                file = directory / 'parameters/control/settings.json'
                file.parent.mkdir(parents=True)
                file.write_text(json.dumps({'control/settings': {'value': 4.}}))
                process, log = await self.start(mode, directory)
                await self.until(lambda: observed.exists() and observed.read_text() == '4.0', process, log)
                file.write_text(json.dumps({'control/settings': {'value': 9.}}))
                await self.until(lambda: observed.read_text() == '9.0', process, log)
                children = self.children(process)
                self.assertGreaterEqual(len(children), 2)
                process.send_signal(signal.SIGTERM)
                self.assertEqual(await asyncio.wait_for(process.wait(), 15), 130, log.read_text())
                for pid in children:
                    with self.assertRaises(ProcessLookupError):
                        os.kill(pid, 0)
                self.assertFalse((self.root / 'parameters').exists())

    async def test_sync_failure_stops_the_application_in_both_modes(self):
        for mode in self.plans:
            with self.subTest(mode=mode), tempfile.TemporaryDirectory() as temporary:
                process, log = await self.start(mode, Path(temporary), '--params-client', 'unknown')
                self.assertEqual(await asyncio.wait_for(process.wait(), 30), 1, log.read_text())
                self.assertIn('client is not declared', log.read_text())

    async def test_worker_failure_stops_local_cluster(self):
        with tempfile.TemporaryDirectory() as temporary:
            process, log = await self.start('distributed', Path(temporary))
            file = Path(temporary) / 'parameters/control/settings.json'
            await self.until(file.exists, process, log)
            children = self.children(process)
            # Coordinator, the sole worker from the plan, then the synchronizer.
            self.assertEqual(len(children), 3)
            worker = next(pid for pid in children if b'\0worker\0' in Path(f'/proc/{pid}/cmdline').read_bytes())
            os.kill(worker, signal.SIGTERM)
            self.assertEqual(await asyncio.wait_for(process.wait(), 15), 1, log.read_text())
            self.assertIn('probe exited', log.read_text())
            for pid in children:
                with self.assertRaises(ProcessLookupError):
                    os.kill(pid, 0)

    async def test_synchronizer_exit_stops_and_reaps_both_modes(self):
        for mode in self.plans:
            with self.subTest(mode=mode), tempfile.TemporaryDirectory() as temporary:
                process, log = await self.start(mode, Path(temporary))
                await self.until((Path(temporary) / 'parameters/control/settings.json').exists, process, log)
                children = self.children(process)
                sync = next(pid for pid in children if b'\0parameter\0sync\0' in Path(f'/proc/{pid}/cmdline').read_bytes())
                os.kill(sync, signal.SIGTERM)
                self.assertEqual(await asyncio.wait_for(process.wait(), 15), 1, log.read_text())
                self.assertIn('params-sync exited', log.read_text())
                for pid in children:
                    with self.assertRaises(ProcessLookupError):
                        os.kill(pid, 0)
