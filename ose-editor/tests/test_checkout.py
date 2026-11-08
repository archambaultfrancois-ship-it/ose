"""Check the checkout launcher's environment lifecycle without downloading packages."""
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


class CheckoutTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='editor checkout ')
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.python = self.root / 'ose-editor/.venv/bin/python'
        framework = patch.object(editor, 'FRAMEWORK', self.root)
        framework.start()
        self.addCleanup(framework.stop)
        environment = patch.dict(os.environ, OSE_APP_ROOT=str(self.root / 'application sources'),
                                 SDL_ROOT=str(self.root / 'sdl'), PYTHONPATH='inherited/source/path')
        environment.start()
        self.addCleanup(environment.stop)
        os.environ.pop('OSE_EDITOR_PYTHON', None)
        self.output = io.StringIO()

    def invoke(self, arguments=()):
        with redirect_stdout(self.output), redirect_stderr(self.output):
            return editor.main(arguments)

    def test_first_launch_prepares_once_then_reuses_the_private_environment(self):
        def prepare(python, managed):
            self.assertTrue(managed)
            python.parent.mkdir(parents=True)
            python.touch()

        with patch.object(editor, 'setup', side_effect=prepare) as setup, \
                patch.object(editor, 'dependencies_ready', return_value=True), \
                patch.object(editor.os, 'execvpe') as execute:
            self.assertEqual(self.invoke(), 0)
            self.assertEqual(self.invoke(), 0)
        setup.assert_called_once_with(self.python, managed=True)
        self.assertEqual(execute.call_count, 2)

    def test_failed_setup_never_starts_the_editor_and_can_be_retried(self):
        failure = subprocess.CalledProcessError(1, ['pip', 'install'])
        with patch.object(editor, 'setup', side_effect=failure), \
                patch.object(editor.os, 'execvpe') as execute:
            self.assertEqual(self.invoke(), 2)
            execute.assert_not_called()
        with patch.object(editor, 'setup') as setup, patch.object(editor.os, 'execvpe'):
            self.assertEqual(self.invoke(), 0)
            setup.assert_called_once()

    def test_help_and_check_do_not_create_environments_or_probe_dependencies(self):
        with patch.object(editor, 'setup') as setup, \
                patch.object(editor, 'dependencies_ready') as ready, \
                patch.object(editor.os, 'execvpe') as execute:
            for arguments in (['--help'], ['--check'], ['setup', '--help']):
                with self.subTest(arguments=arguments):
                    if arguments[0] == 'setup':
                        with self.assertRaises(SystemExit) as result:
                            self.invoke(arguments)
                        self.assertEqual(result.exception.code, 0)
                    else:
                        self.assertEqual(self.invoke(arguments), 0)
            setup.assert_not_called()
            ready.assert_not_called()
        self.assertEqual(execute.call_count, 2)
        self.assertFalse(self.python.parent.exists())

    def test_explicit_interpreter_is_preserved_and_never_installed_into_implicitly(self):
        os.environ['OSE_EDITOR_PYTHON'] = sys.executable
        with patch.object(editor, 'dependencies_ready', return_value=False), \
                patch.object(editor, 'setup') as setup, \
                patch.object(editor.os, 'execvpe') as execute:
            self.assertEqual(self.invoke(), 2)
            setup.assert_not_called()
            execute.assert_not_called()
            self.assertEqual(self.invoke(['setup']), 0)
            setup.assert_called_once_with(Path(sys.executable), managed=False)

    def test_launch_preserves_arguments_caller_and_application_environment(self):
        os.environ['OSE_EDITOR_PYTHON'] = 'chosen-python'
        caller, inherited = Path.cwd(), dict(os.environ)
        arguments = ['--root', 'other project', '--port', '8083']
        with patch.object(editor.shutil, 'which', return_value=sys.executable), \
                patch.object(editor, 'dependencies_ready', return_value=True), \
                patch.object(editor.os, 'execvpe') as execute:
            self.assertEqual(self.invoke(arguments), 0)
        python, command, environment = execute.call_args.args
        self.assertEqual(python, sys.executable)
        self.assertEqual(command[-len(arguments):], arguments)
        self.assertIn(str(self.root / 'application sources'), command)
        self.assertTrue(environment['PYTHONPATH'].startswith(str(self.root / 'ose-editor/src')))
        self.assertTrue(environment['PYTHONPATH'].endswith('inherited/source/path'))
        self.assertEqual(dict(os.environ), inherited)
        self.assertEqual(Path.cwd(), caller)

    def test_setup_installs_local_packages_with_constraints_and_no_inherited_pythonpath(self):
        inherited = dict(os.environ)
        with patch.object(editor.venv, 'EnvBuilder') as builder, \
                patch.object(editor.subprocess, 'run') as run, \
                patch.object(editor, 'dependencies_ready', return_value=True), \
                redirect_stdout(self.output):
            editor.setup(self.python, managed=True)
        builder.return_value.create.assert_called_once_with(self.python.parent.parent)
        command = run.call_args.args[0]
        self.assertIn(str(self.root / 'ose-editor/requirements.lock'), command)
        self.assertEqual(command[-2:], [str(self.root / 'ose'), str(self.root / 'ose-editor')])
        for call in run.call_args_list:
            self.assertNotIn('PYTHONPATH', call.kwargs['env'])
        self.assertEqual(dict(os.environ), inherited)

    def test_old_base_python_is_rejected_before_creating_an_environment(self):
        with patch.object(editor.sys, 'version_info', (3, 9)), \
                patch.object(editor.venv, 'EnvBuilder') as builder, \
                patch.object(editor.subprocess, 'run') as run:
            with self.assertRaisesRegex(ValueError, 'OSE_PYTHON=python3.12'):
                editor.setup(self.python, managed=True)
            builder.assert_not_called()
            run.assert_not_called()
