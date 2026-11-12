"""Checkout console setup and launch follow the same contract as the editor."""
from contextlib import redirect_stderr, redirect_stdout
import importlib.util
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location('console_checkout',
    Path(__file__).resolve().parents[1] / 'tools/console.py')
console = importlib.util.module_from_spec(spec)
spec.loader.exec_module(console)
import python_env


class CheckoutTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='console checkout ')
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        framework = patch.object(console, 'FRAMEWORK', self.root)
        framework.start()
        self.addCleanup(framework.stop)
        environment = patch.dict(os.environ, OSE_APP_ROOT=str(self.root / 'application sources'),
                                 SDL_ROOT=str(self.root / 'sdl'), PYTHONPATH='inherited/source/path')
        environment.start()
        self.addCleanup(environment.stop)
        self.output = io.StringIO()

    def invoke(self, arguments=()):
        with redirect_stdout(self.output), redirect_stderr(self.output):
            return console.main(arguments)

    def test_first_launch_prepares_missing_dependencies_and_reuses_them(self):
        with patch.object(console, 'setup') as setup, \
                patch.object(console, 'dependencies_ready', side_effect=[False, True]), \
                patch.object(console.os, 'execve') as execute:
            self.assertEqual(self.invoke(), 0)
            self.assertEqual(self.invoke(), 0)
        setup.assert_called_once_with()
        self.assertEqual(execute.call_count, 2)
        self.assertFalse((self.root / 'ose-ws/.venv').exists())

    def test_failed_setup_never_starts_the_console_and_can_be_retried(self):
        with patch.object(console, 'dependencies_ready', return_value=False), \
                patch.object(console, 'setup', side_effect=subprocess.CalledProcessError(1, ['pip'])), \
                patch.object(console.os, 'execve') as execute:
            self.assertEqual(self.invoke(), 2)
            execute.assert_not_called()
        with patch.object(console, 'dependencies_ready', return_value=True), \
                patch.object(console.os, 'execve'):
            self.assertEqual(self.invoke(), 0)

    def test_help_init_and_check_do_not_install_or_probe_dependencies(self):
        with patch.object(console, 'setup') as setup, \
                patch.object(console, 'dependencies_ready') as ready, \
                patch.object(console.os, 'execve'):
            for arguments in (['--help'], ['init'], ['check'], ['--root', 'other app', 'check']):
                self.assertEqual(self.invoke(arguments), 0)
            with self.assertRaises(SystemExit) as result:
                self.invoke(['setup', '--help'])
            self.assertEqual(result.exception.code, 0)
        setup.assert_not_called()
        ready.assert_not_called()

    def test_config_filename_check_does_not_skip_dependency_preparation(self):
        with patch.object(console, 'dependencies_ready', return_value=False), \
                patch.object(console, 'setup') as setup, patch.object(console.os, 'execve'):
            self.assertEqual(self.invoke(['--config', 'check']), 0)
        setup.assert_called_once_with()

    def test_launch_preserves_arguments_caller_and_environment(self):
        caller, inherited = Path.cwd(), dict(os.environ)
        arguments = ['--root', 'other project', '--port', '8083']
        with patch.object(console, 'dependencies_ready', return_value=True), \
                patch.object(console.os, 'execve') as execute:
            self.assertEqual(self.invoke(arguments), 0)
        python, command, environment = execute.call_args.args
        self.assertEqual(python, sys.executable)
        self.assertEqual(command[:3], [sys.executable, '-m', 'ose_ws.cli'])
        self.assertEqual(command[-len(arguments):], arguments)
        self.assertIn(str(self.root / 'application sources'), command)
        self.assertTrue(environment['PYTHONPATH'].startswith(str(self.root / 'ose-ws/src')))
        self.assertIn(str(self.root / 'build/python-deps'), environment['PYTHONPATH'])
        self.assertEqual(dict(os.environ), inherited)
        self.assertEqual(Path.cwd(), caller)

    def test_default_root_follows_caller_without_application_environment(self):
        with patch.dict(os.environ, {}, clear=True), patch.object(console.os, 'execve') as execute:
            console.launch(['check'])
        self.assertEqual(execute.call_args.args[1][-3:], ['--root', str(Path.cwd()), 'check'])

    def test_setup_installs_and_builds_services_with_the_same_python(self):
        inherited = dict(os.environ)
        with patch.object(python_env.subprocess, 'run') as run:
            self.assertEqual(self.invoke(['setup']), 0)
        installation, build = run.call_args_list
        command = installation.args[0]
        self.assertEqual(command[:4], [sys.executable, '-m', 'pip', 'install'])
        self.assertEqual(command[command.index('--target') + 1], str(self.root / 'build/python-deps'))
        self.assertIn(str(self.root / 'ose-ws/requirements.lock'), command)
        self.assertNotIn('PYTHONPATH', installation.kwargs['env'])
        self.assertEqual(build.args[0], ['make', '-C', str(self.root / 'sdl'), 'services',
                                        'PYTHON=' + sys.executable])
        self.assertEqual(dict(os.environ), inherited)

    def test_python_39_is_rejected_before_setup_or_launch(self):
        with patch.object(python_env.sys, 'version_info', (3, 9)), \
                patch.object(console, 'setup') as setup, patch.object(console.os, 'execve') as execute:
            self.assertEqual(self.invoke(), 2)
        setup.assert_not_called()
        execute.assert_not_called()

    @unittest.skipUnless(shutil.which('bash'), 'requires Bash')
    def test_framework_environment_provides_console_for_any_application(self):
        application = self.root / 'application sources'
        application.mkdir()
        python = self.root / 'python3'
        python.write_text('#!' + sys.executable + '\nimport json, sys\n'
                          'if len(sys.argv) > 2 and sys.argv[1] == "-c" and "sys.version_info" in sys.argv[2]:\n'
                          '    exec(sys.argv[2])\nelse:\n    print(json.dumps(sys.argv[1:]))\n')
        python.chmod(0o755)
        framework = Path(__file__).resolve().parents[2]
        environment = dict(os.environ, OSE_APP_ROOT=str(application),
                           FRAMEWORK_ENV=str(framework / 'setenv.sh'),
                           PATH=str(self.root) + os.pathsep + os.environ['PATH'])
        result = subprocess.run(['bash', '--noprofile', '--norc', '-c',
            'set -eu\nsource "$FRAMEWORK_ENV"\nose-ws --port 8081 --config "my console.json"\n'],
            cwd=self.root, env=environment, check=True, text=True, capture_output=True)
        self.assertEqual(json.loads(result.stdout), [str(framework / 'ose-ws/tools/console.py'),
                         '--port', '8081', '--config', 'my console.json'])
        self.assertFalse((application / 'tools').exists())
