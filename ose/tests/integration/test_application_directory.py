"""Real Python/C callbacks write relative files in the app-run launch directory."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time
import unittest

from ose.modeling.compiler import build


SDL_ROOT = os.environ.get('SDL_ROOT')
FILES = [language + '-' + stage + '.txt' for language in ('python', 'c') for stage in ('start', 'tick', 'stop')]
PYTHON_SOURCE = '''from pathlib import Path
tick_recorded = False
def write(stage):
    Path('python-' + stage + '.txt').write_text(str(Path.cwd()))
def on_start(ctx): write('start')
def on_clock(ctx, input):
    global tick_recorded
    if not tick_recorded:
        write('tick')
        tick_recorded = True
def on_stop(ctx): write('stop')
'''
C_SOURCE = '''#include "component.h"
#include <stdio.h>
#include <unistd.h>
static int write_path(const char *name) {
    char directory[4096];
    if (!getcwd(directory, sizeof(directory))) return 0;
    FILE *file = fopen(name, "w");
    if (!file) return 0;
    int ok = fputs(directory, file) >= 0;
    return fclose(file) == 0 && ok;
}
static int start(OseContext *ctx) { (void)ctx; return write_path("c-start.txt"); }
static int on_clock(OseContext *ctx, const Input_clock *input) {
    (void)input;
    static int recorded = 0;
    (void)ctx;
    if (!recorded) recorded = write_path("c-tick.txt");
    return recorded;
}
static int stop(OseContext *ctx) { (void)ctx; return write_path("c-stop.txt"); }
const ComponentCallbacks component_callbacks = { .on_start=start, .on_clock=on_clock, .on_stop=stop };
'''


@unittest.skipUnless(SDL_ROOT and shutil.which('make') and shutil.which('cc'), 'requires SDL_ROOT, make and C')
class ApplicationDirectoryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        temporary = tempfile.TemporaryDirectory(prefix='ose launch directories ')
        cls.addClassCleanup(temporary.cleanup)
        cls.directory = Path(temporary.name)
        cls.project = cls.directory / 'application with spaces'
        cls.project.mkdir()
        (cls.project / 'empty.sdl').write_text('message Empty {}')
        (cls.project / 'source.py').write_text(PYTHON_SOURCE)
        (cls.project / 'source.c').write_text(C_SOURCE)
        model = dict(version=2, schemas='empty.sdl', subscriptions=[], components={
            language: dict(inputs={'clock': dict(kind='timer', timer=dict(mode='periodic', delay=.01))}, outputs={})
            for language in ('python', 'c')})
        deployment = dict(version=1, components={language: dict(container=dict(language=language,
            handler='source.' + ('py' if language == 'python' else 'c'))) for language in model['components']})
        for name, value in (('model', model), ('deployment', deployment)):
            (cls.project / (name + '.json')).write_text(json.dumps(value))
        cls.plan = build(cls.project / 'model.json', cls.project / 'deployment.json', SDL_ROOT, cls.project / 'build')
        cls.original_plan = cls.plan.read_bytes()
        (cls.project / 'Makefile').write_text('.PHONY: build\nbuild:\n\t@pwd > build/make-cwd.txt\n')
        cls.environment = dict(os.environ, PYTHONPATH=str(Path(__file__).resolve().parents[2] / 'src'),
                               SDL_ROOT=str(Path(SDL_ROOT).resolve()))
        cls.command = [sys.executable, '-m', 'ose.cli']

    def setUp(self):
        for name in FILES:
            (self.project / name).unlink(missing_ok=True)

    def launch(self, options, caller, output, mode):
        options += ['--mode', mode]
        if mode == 'simulated':
            options += ['--duration', '.15']
        with subprocess.Popen(self.command + options, cwd=caller, env=self.environment,
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True) as process:
            try:
                if mode == 'realtime':
                    # Wait for actual callbacks, independent of runner load. The
                    # fixture records each first tick once, so shutdown cannot
                    # interrupt a later rewrite of the evidence being asserted.
                    deadline = time.monotonic() + 20
                    while process.poll() is None:
                        if all((output / name).is_file() and (output / name).read_text() == str(output)
                               for name in ('python-tick.txt', 'c-tick.txt')):
                            break
                        self.assertLess(time.monotonic(), deadline, 'first component ticks timed out')
                        time.sleep(.01)
                    if process.poll() is None:
                        process.terminate()
                stdout, stderr = process.communicate(timeout=30)
            finally:
                if process.poll() is None:
                    process.kill()
                    process.communicate(timeout=5)
        self.assertEqual(process.returncode, 0, stdout + stderr)
        self.assertEqual(self.plan.read_bytes(), self.original_plan)

    def assert_outputs(self, directory):
        for name in FILES:
            self.assertEqual((directory / name).read_text(), str(directory), name)

    def test_app_run_uses_launch_directory_in_realtime_and_simulation(self):
        for mode in ('realtime', 'simulated'):
            with self.subTest(mode=mode):
                caller = self.directory / ('launch ' + mode)
                caller.mkdir()
                options = ['app', '--root', str(self.project), 'run']
                if mode == 'simulated':
                    options += ['--trace', 'trace.jsonl']
                self.launch(options, caller, caller, mode)
                self.assert_outputs(caller)
                self.assertTrue(all(not (self.project / name).exists() for name in FILES))
                self.assertEqual((self.project / 'build/make-cwd.txt').read_text().strip(), str(self.project))
                self.assertTrue((self.project / 'build/last-status.json').is_file())
                if mode == 'realtime':
                    self.assertTrue((self.project / 'build/endpoint.json').is_file())
                else:
                    self.assertTrue((caller / 'trace.jsonl').is_file())
                    self.assertFalse((self.project / 'trace.jsonl').exists())

    def test_explicit_runtime_directory_resolves_from_launch_directory(self):
        for mode in ('realtime', 'simulated'):
            with self.subTest(mode=mode):
                caller = self.directory / ('launch with override ' + mode)
                output = caller / 'output files'
                output.mkdir(parents=True)
                self.launch(['app', '--root', str(self.project), 'run',
                             '--working-directory', 'output files'], caller, output, mode)
                self.assert_outputs(output)
                self.assertTrue(all(not (caller / name).exists() for name in FILES))
                self.assertTrue(all(not (self.project / name).exists() for name in FILES))

    def test_low_level_run_keeps_explicit_plan_working_directories(self):
        for mode in ('realtime', 'simulated'):
            with self.subTest(mode=mode):
                for name in FILES:
                    (self.project / name).unlink(missing_ok=True)
                caller = self.directory / ('low level launch ' + mode)
                caller.mkdir()
                self.launch(['run', '--plan', str(self.plan)], caller, self.project, mode)
                self.assert_outputs(self.project)
                self.assertTrue(all(not (caller / name).exists() for name in FILES))
