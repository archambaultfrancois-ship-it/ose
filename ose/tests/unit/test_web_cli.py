"""Browser commands share help/completion and retain checkout/install behavior."""
from contextlib import redirect_stderr, redirect_stdout
import io
import os
from pathlib import Path
import sys
import unittest
from unittest.mock import Mock, patch

from ose.cli import create_parser, main
from ose import web_cli


class WebCliTests(unittest.TestCase):
    def test_help_is_available_without_importing_optional_applications(self):
        for command in ([], ['ws'], ['editor'], ['ws', 'setup'], ['editor', 'setup']):
            with self.subTest(command=command), patch.object(web_cli, 'launch') as launch:
                output = io.StringIO()
                with redirect_stdout(output), self.assertRaises(SystemExit) as result:
                    main([*command, '--help'])
                self.assertEqual(result.exception.code, 0)
                launch.assert_not_called()
                self.assertIn('ose', output.getvalue())
        help_text = create_parser().format_help()
        self.assertIn('ws', help_text)
        self.assertIn('editor', help_text)
        self.assertIn('ose ws', help_text)
        self.assertIn('ose editor', help_text)

    def test_checkout_dispatch_preserves_arguments_python_and_caller(self):
        caller, environment = Path.cwd(), dict(os.environ)
        framework = Path(web_cli.__file__).resolve().parents[3]
        for name, script, arguments in (
            ('ws', 'ose-ws/tools/console.py', ['--root', 'other app', '--config', 'check', 'check']),
            ('editor', 'ose-editor/tools/editor.py', ['--deployment', 'one.json', '--deployment', 'two.json', '--port', '8083']),
            ('ws', 'ose-ws/tools/console.py', ['setup']),
            ('editor', 'ose-editor/tools/editor.py', ['setup']),
        ):
            with self.subTest(name=name, arguments=arguments), patch.object(web_cli.os, 'execv') as execute:
                self.assertEqual(main([name, *arguments]), 0)
                execute.assert_called_once_with(sys.executable, [sys.executable, str(framework / script), *arguments])
        self.assertEqual(Path.cwd(), caller)
        self.assertEqual(dict(os.environ), environment)

    def test_installed_dispatch_uses_optional_modules_and_application_defaults(self):
        for name, module in (('ws', 'ose_ws.cli'), ('editor', 'ose_editor.cli')):
            application = Mock()
            application.main.return_value = 7
            arguments = ['--root', 'explicit project', '--port', '8083']
            with self.subTest(name=name), patch.object(Path, 'is_file', return_value=False), \
                    patch.object(web_cli.importlib, 'import_module', return_value=application) as load, \
                    patch.dict(os.environ, OSE_APP_ROOT='selected application'):
                self.assertEqual(main([name, *arguments]), 7)
            load.assert_called_once_with(module)
            application.main.assert_called_once_with(['--root', 'selected application', *arguments])

    def test_missing_features_and_installed_setup_fail_without_installing(self):
        with patch.object(Path, 'is_file', return_value=False), \
                patch.object(web_cli.importlib, 'import_module', side_effect=ModuleNotFoundError(name='ose_ws')) as load:
            output = io.StringIO()
            with redirect_stderr(output):
                self.assertEqual(main(['ws']), 2)
                self.assertEqual(main(['ws', 'setup']), 2)
            self.assertIn('web feature', output.getvalue())
            self.assertIn('rerun the installer', output.getvalue())
            self.assertEqual(load.call_count, 1)

    def test_invalid_options_fail_before_dependency_preparation(self):
        for arguments in (['ws', '--port', 'invalid'], ['editor', '--unknown'], ['ws', 'unknown']):
            with self.subTest(arguments=arguments), patch.object(web_cli, 'launch') as launch, \
                    redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as result:
                main(arguments)
            self.assertEqual(result.exception.code, 2)
            launch.assert_not_called()
