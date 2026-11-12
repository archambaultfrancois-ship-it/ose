"""Application defaults are generic and preserve the caller's runtime directory."""
from contextlib import redirect_stderr, redirect_stdout
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from ose_ws import cli

FRAMEWORK = Path(__file__).resolve().parents[2]


class ApplicationTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='web application ')
        self.addCleanup(temporary.cleanup)
        self.base = Path(temporary.name)
        self.root = self.base / 'another-application'
        self.caller = self.base / 'launch files'
        self.root.mkdir()
        self.caller.mkdir()
        previous = Path.cwd()
        os.chdir(self.caller)
        self.addCleanup(os.chdir, previous)
        environment = patch.dict(os.environ, SDL_ROOT=str(FRAMEWORK / 'sdl'))
        environment.start()
        self.addCleanup(environment.stop)
        for key in ('OSE_PARAMETER_TOKEN', 'OSE_PROCESS_PREFIX', 'OSE_WS_STATE_DIR', 'OSE_WS_HOST', 'OSE_WS_PORT'):
            os.environ.pop(key, None)

    def capture(self, config):
        self.settings = config
        self.token = os.environ.get('OSE_PARAMETER_TOKEN')
        self.prefix = os.environ.get('OSE_PROCESS_PREFIX')
        self.cwd = Path.cwd()
        return 0

    def invoke(self, arguments=()):
        previous = dict(os.environ)
        with patch.object(cli, 'serve', side_effect=self.capture), redirect_stdout(io.StringIO()), \
                redirect_stderr(io.StringIO()):
            result = cli.main(['--root', str(self.root), *arguments])
        self.assertEqual(Path.cwd(), self.caller)
        self.assertEqual(dict(os.environ), previous)
        return result

    def test_first_launch_initializes_once_and_resolves_fresh_credentials(self):
        (self.root / 'deployment.local.json').write_text(json.dumps({
            'process_names': {'prefix': 'demo'},
            'parameter_clients': {'operator': {'token': 'deployment-test-token'}}}))
        self.assertEqual(self.invoke(), 0)
        self.assertEqual(self.token, 'deployment-test-token')
        self.assertEqual(self.prefix, 'demo')
        config = self.root / 'ose-ws.json'
        original = config.read_bytes()
        self.assertEqual(json.loads(original)['application']['name'], 'Another Application')
        self.assertEqual(self.settings['application']['root'], str(self.root))
        token = self.root / '.ose-ws/access.token'
        original_token = token.read_bytes()
        self.assertNotEqual(original_token.decode().strip(), self.token)
        self.assertEqual(token.stat().st_mode & 0o777, 0o600)
        self.assertEqual(self.settings['state_dir'], str(self.caller / '.ose-ws'))
        self.assertEqual(self.cwd, self.caller)
        (self.root / 'build').mkdir()
        for value in ('first-plan-token', 'replacement-plan-token'):
            (self.root / 'build/plan.json').write_text(json.dumps({
                'version': 2, 'components': {'source': {'execution_model': 2}},
                'process_names': {'prefix': 'plan'},
                'parameter_clients': {'operator': {'token': value}}}))
            self.assertEqual(self.invoke(), 0)
            self.assertEqual(self.token, value)
            self.assertEqual(self.prefix, 'plan')
        self.assertEqual(config.read_bytes(), original)
        self.assertEqual(token.read_bytes(), original_token)

    def test_explicit_credentials_prefix_and_caller_relative_endpoint_are_preserved(self):
        with patch.dict(os.environ, OSE_PARAMETER_TOKEN='manual-test-token', OSE_PROCESS_PREFIX='other'):
            self.assertEqual(self.invoke(['--endpoint', 'alternate endpoint.json', '--port', '8081']), 0)
        self.assertEqual(self.token, 'manual-test-token')
        self.assertEqual(self.prefix, 'other')
        self.assertEqual(self.settings['application']['endpoint'], str(self.caller / 'alternate endpoint.json'))
        self.assertEqual(self.settings['listen']['port'], 8081)

    def test_runtime_directory_follows_caller_and_inputs_stay_in_application(self):
        output = self.caller / 'output files'
        output.mkdir()
        self.assertEqual(self.invoke(['--working-directory', 'output files']), 0)
        self.assertEqual(self.settings['state_dir'], str(output / '.ose-ws'))
        self.assertEqual(self.settings['auth']['token_file'], str(self.root / '.ose-ws/access.token'))
        self.assertEqual(self.settings['application']['endpoint'], str(self.root / 'build/endpoint.json'))

    def test_invalid_runtime_directory_does_not_initialize_configuration(self):
        self.assertEqual(self.invoke(['--working-directory', 'missing']), 2)
        self.assertFalse((self.root / 'ose-ws.json').exists())
        self.assertFalse((self.root / '.ose-ws').exists())

    def test_check_and_explicit_config_do_not_initialize_missing_files(self):
        for arguments in (['check'], ['--config', 'custom.json']):
            self.assertEqual(self.invoke(arguments), 3)
            self.assertFalse((self.root / 'ose-ws.json').exists())
            self.assertFalse((self.caller / 'custom.json').exists())
            self.assertFalse((self.root / '.ose-ws').exists())

    def test_init_with_config_elsewhere_keeps_selected_application_root(self):
        self.assertEqual(self.invoke(['init', '--config', 'custom.json']), 0)
        self.assertEqual(self.invoke(['--config', 'custom.json']), 0)
        self.assertEqual(self.settings['application']['root'], str(self.root))
        self.assertEqual(self.settings['auth']['token_file'], str(self.caller / '.ose-ws/access.token'))

    def test_explicit_config_resolves_credentials_for_its_actual_application(self):
        other = self.base / 'configured application'
        other.mkdir()
        (other / 'deployment.local.json').write_text(json.dumps({
            'parameter_clients': {'operator': {'token': 'configured-app-token'}}}))
        config = self.caller / 'custom.json'
        with redirect_stdout(io.StringIO()):
            cli.initialize(config, application_root=other)
        self.assertEqual(self.invoke(['--config', str(config)]), 0)
        self.assertEqual(self.token, 'configured-app-token')
        self.assertEqual(self.settings['application']['root'], str(other))

    def test_server_error_restores_environment_and_caller(self):
        previous = dict(os.environ)
        with patch.object(cli, 'serve', side_effect=RuntimeError('test failure')), \
                redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
            self.assertEqual(cli.main(['--root', str(self.root)]), 3)
        self.assertEqual(dict(os.environ), previous)
        self.assertEqual(Path.cwd(), self.caller)
