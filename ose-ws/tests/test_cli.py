"""Runtime files follow the launch directory; configuration inputs keep their base."""
from contextlib import redirect_stderr, redirect_stdout
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from ose_ws import cli
from ose_ws.config import read


class DirectoryTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='ose web directories ')
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.application = self.root / 'application files'
        self.caller = self.root / 'launch files'
        self.application.mkdir()
        self.caller.mkdir()
        self.config = self.application / 'console.json'
        with redirect_stdout(io.StringIO()):
            cli.initialize(self.config)
        self.original = self.config.read_bytes()
        self.original_token = (self.application / '.ose-ws/access.token').read_bytes()
        previous = Path.cwd()
        os.chdir(self.caller)
        self.addCleanup(os.chdir, previous)
        environment = patch.dict(os.environ, {key: value for key, value in os.environ.items()
                                              if not key.startswith('OSE_WS_')}, clear=True)
        environment.start()
        self.addCleanup(environment.stop)

    def launch(self, *options):
        with patch.object(cli, 'serve', return_value=0) as serve:
            self.assertEqual(cli.main(['--config', str(self.config), *options]), 0)
        self.assertEqual(self.config.read_bytes(), self.original)
        self.assertEqual((self.application / '.ose-ws/access.token').read_bytes(), self.original_token)
        self.assertEqual(Path.cwd(), self.caller)
        return serve.call_args.args[0]

    def test_default_storage_follows_caller_and_inputs_follow_configuration(self):
        settings = self.launch()
        self.assertEqual(settings['state_dir'], str(self.caller / '.ose-ws'))
        self.assertEqual(settings['application']['root'], str(self.application))
        self.assertEqual(settings['application']['endpoint'], str(self.application / 'build/endpoint.json'))
        self.assertEqual(settings['auth']['token_file'], str(self.application / '.ose-ws/access.token'))
        # Programmatic reads without a runtime directory keep configuration-relative paths.
        self.assertEqual(read(self.config)['state_dir'], str(self.application / '.ose-ws'))

    def test_override_and_endpoint_are_relative_to_launch_directory(self):
        output = self.caller / 'output files'
        output.mkdir()
        settings = self.launch('--working-directory', 'output files', '--endpoint', 'alternate endpoint.json')
        self.assertEqual(settings['state_dir'], str(output / '.ose-ws'))
        self.assertEqual(settings['application']['endpoint'], str(self.caller / 'alternate endpoint.json'))

    def test_relative_configured_and_environment_state_paths_follow_runtime(self):
        document = json.loads(self.original)
        document['state_dir'] = 'custom state'
        self.config.write_text(json.dumps(document))
        self.original = self.config.read_bytes()
        self.assertEqual(self.launch()['state_dir'], str(self.caller / 'custom state'))
        with patch.dict(os.environ, {'OSE_WS_STATE_DIR': 'environment state'}):
            self.assertEqual(self.launch()['state_dir'], str(self.caller / 'environment state'))

    def test_absolute_state_directory_remains_authoritative(self):
        state = self.root / 'fixed state'
        with patch.dict(os.environ, {'OSE_WS_STATE_DIR': str(state)}):
            self.assertEqual(self.launch()['state_dir'], str(state))

    def test_missing_directory_fails_before_initialization_or_server_start(self):
        for command in ('init', 'check', 'serve'):
            with self.subTest(command=command), patch.object(cli, 'serve') as serve, \
                    patch.object(cli, 'initialize') as initialize, redirect_stderr(io.StringIO()):
                self.assertEqual(cli.main([command, '--config', str(self.config),
                                           '--working-directory', 'missing']), 2)
            serve.assert_not_called()
            initialize.assert_not_called()

    def test_check_uses_configuration_token_without_creating_runtime_state(self):
        with redirect_stdout(io.StringIO()):
            self.assertEqual(cli.main(['check', '--config', str(self.config)]), 0)
        self.assertFalse((self.caller / '.ose-ws').exists())
