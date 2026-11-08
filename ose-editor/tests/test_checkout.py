"""The editor uses the running PATH Python and checkout-local dependencies."""
from contextlib import redirect_stderr, redirect_stdout
import importlib.util
import io
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location('editor_checkout',
    Path(__file__).resolve().parents[1] / 'tools/editor.py')
editor = importlib.util.module_from_spec(spec)
spec.loader.exec_module(editor)
import python_env


class CheckoutTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='editor checkout ')
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        framework = patch.object(editor, 'FRAMEWORK', self.root)
        framework.start()
        self.addCleanup(framework.stop)
        environment = patch.dict(os.environ, OSE_APP_ROOT=str(self.root / 'application sources'),
                                 SDL_ROOT=str(self.root / 'sdl'), PYTHONPATH='inherited/source/path')
        environment.start()
        self.addCleanup(environment.stop)
        self.output = io.StringIO()

    def invoke(self, arguments=()):
        with redirect_stdout(self.output), redirect_stderr(self.output):
            return editor.main(arguments)

    def test_first_launch_prepares_missing_dependencies_and_reuses_them(self):
        with patch.object(editor, 'setup') as setup, \
                patch.object(editor, 'dependencies_ready', side_effect=[False, True]), \
                patch.object(editor.os, 'execve') as execute:
            self.assertEqual(self.invoke(), 0)
            self.assertEqual(self.invoke(), 0)
        setup.assert_called_once_with()
        self.assertEqual(execute.call_count, 2)
        for invocation in execute.call_args_list:
            self.assertEqual(invocation.args[0], sys.executable)
        self.assertFalse((self.root / 'ose-editor/.venv').exists())

    def test_failed_setup_never_starts_the_editor_and_can_be_retried(self):
        with patch.object(editor, 'dependencies_ready', return_value=False), \
                patch.object(editor, 'setup', side_effect=subprocess.CalledProcessError(1, ['pip'])), \
                patch.object(editor.os, 'execve') as execute:
            self.assertEqual(self.invoke(), 2)
            execute.assert_not_called()
        with patch.object(editor, 'dependencies_ready', return_value=True), \
                patch.object(editor.os, 'execve'):
            self.assertEqual(self.invoke(), 0)

    def test_help_and_check_never_install_or_probe_dependencies(self):
        with patch.object(editor, 'setup') as setup, \
                patch.object(editor, 'dependencies_ready') as ready, \
                patch.object(editor.os, 'execve'):
            for arguments in (['--help'], ['--check']):
                self.assertEqual(self.invoke(arguments), 0)
            with self.assertRaises(SystemExit) as result:
                self.invoke(['setup', '--help'])
            self.assertEqual(result.exception.code, 0)
        setup.assert_not_called()
        ready.assert_not_called()

    def test_launch_preserves_arguments_caller_and_uses_current_python(self):
        caller, inherited = Path.cwd(), dict(os.environ)
        arguments = ['--root', 'other project', '--port', '8083']
        with patch.object(editor, 'dependencies_ready', return_value=True), \
                patch.object(editor.os, 'execve') as execute:
            self.assertEqual(self.invoke(arguments), 0)
        python, command, environment = execute.call_args.args
        self.assertEqual(python, sys.executable)
        self.assertEqual(command[-len(arguments):], arguments)
        self.assertIn(str(self.root / 'application sources'), command)
        self.assertTrue(environment['PYTHONPATH'].startswith(str(self.root / 'ose-editor/src')))
        self.assertIn(str(self.root / 'build/python-deps'), environment['PYTHONPATH'])
        self.assertEqual(dict(os.environ), inherited)
        self.assertEqual(Path.cwd(), caller)

    def test_setup_installs_with_the_same_python_into_shared_build_output(self):
        inherited = dict(os.environ)
        with patch.object(python_env.subprocess, 'run') as run:
            self.assertEqual(self.invoke(['setup']), 0)
        command = run.call_args.args[0]
        self.assertEqual(command[:4], [sys.executable, '-m', 'pip', 'install'])
        self.assertEqual(command[command.index('--target') + 1], str(self.root / 'build/python-deps'))
        self.assertIn(str(self.root / 'ose-editor/requirements.lock'), command)
        self.assertNotIn('PYTHONPATH', run.call_args.kwargs['env'])
        self.assertEqual(dict(os.environ), inherited)

    def test_python_39_is_rejected_before_installation_or_launch(self):
        with patch.object(python_env.sys, 'version_info', (3, 9)), \
                patch.object(editor, 'setup') as setup, \
                patch.object(editor.os, 'execve') as execute:
            self.assertEqual(self.invoke(), 2)
        setup.assert_not_called()
        execute.assert_not_called()
        self.assertIn('Python 3.10+ interpreter from PATH', self.output.getvalue())
