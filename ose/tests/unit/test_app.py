"""Application workflows resolve context without shell aliases or side effects on help."""
import contextlib
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

from ose.cli import create_parser, main
from ose.app import Application


class ApplicationTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory(prefix='ose app ')
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        environment = {key: value for key, value in os.environ.items()
                       if not key.startswith('OSE_') and key not in ('SDL_ROOT', 'FRAMEWORK_ROOT')}
        self.env = patch.dict(os.environ, environment, clear=True)
        self.env.start()
        self.addCleanup(self.env.stop)

    def command(self, *args):
        return main(['app', '--root', str(self.root), *args])

    def test_context_can_be_used_without_an_app_command(self):
        caller = Path.cwd()
        context = Application(SimpleNamespace(root=str(self.root), app_sdl_root=str(self.root / 'sdl')))
        with context.activate():
            self.assertEqual(Path.cwd(), self.root)
            self.assertEqual(os.environ['OSE_APP_ROOT'], str(self.root))
            self.assertEqual(os.environ['SDL_ROOT'], str(self.root / 'sdl'))
        self.assertEqual(Path.cwd(), caller)
        self.assertNotIn('OSE_APP_ROOT', os.environ)

    def configuration(self, document, name='build/plan.json'):
        if name == 'build/plan.json':
            document = {'version': 2, 'components': {'source': {'execution_model': 2}}, **document}
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(document))

    def parameter(self, *args):
        observed = {}
        async def inspect(options):
            observed.update(cwd=Path.cwd(), client=options.client, export=getattr(options, 'export', None),
                            token=os.environ.get(options.token_env), directory=getattr(options, 'dir', None),
                            file=getattr(options, 'file', None), plan=options.plan, endpoint=options.endpoint)
        with patch('ose.parameters.cli.run', new=AsyncMock(side_effect=inspect)):
            self.assertEqual(self.command('parameter', *args), 0)
        return observed

    def test_client_and_token_are_resolved_each_time_and_environment_is_restored(self):
        self.configuration({'parameter_clients': {'operator': {'token': 'test-operator'},
                                                'maintenance': {'token': 'test-maintenance'}}})
        cwd = Path.cwd()
        first = self.parameter('get')
        second = self.parameter('get', '--client', 'maintenance')
        self.assertEqual((first['client'], first['token']), ('operator', 'test-operator'))
        self.assertEqual((second['client'], second['token']), ('maintenance', 'test-maintenance'))
        self.assertTrue(first['export'])
        self.assertEqual(first['cwd'], self.root)
        self.assertEqual(first['plan'], 'build/plan.json')
        self.assertEqual(first['endpoint'], 'build/endpoint.json')
        self.assertEqual(Path.cwd(), cwd)
        self.assertNotIn('OSE_PARAMETER_TOKEN', os.environ)
        os.environ['OSE_PARAMETER_CLIENT'] = 'maintenance'
        self.assertEqual(self.parameter('get')['token'], 'test-maintenance')

    def test_explicit_tokens_including_empty_and_custom_environments_take_precedence(self):
        self.configuration({'parameter_clients': {'operator': {'token': 'test-auto'}}})
        for token in ('test-manual', ''):
            with self.subTest(token=token):
                os.environ['OSE_PARAMETER_TOKEN'] = token
                self.assertEqual(self.parameter('get')['token'], token)
                self.assertEqual(os.environ['OSE_PARAMETER_TOKEN'], token)
        del os.environ['OSE_PARAMETER_TOKEN']
        self.assertIsNone(self.parameter('get', '--token-env', 'CUSTOM_TOKEN')['token'])
        os.environ['CUSTOM_TOKEN'] = 'test-custom'
        self.assertEqual(self.parameter('get', '--token-env', 'CUSTOM_TOKEN')['token'], 'test-custom')

    def test_plan_precedes_deployments_including_clients_without_tokens(self):
        self.configuration({'parameter_clients': {'operator': {'token': 'test-deployment'}}}, 'deployment.json')
        self.assertEqual(self.parameter('get')['token'], 'test-deployment')
        self.configuration({'parameter_clients': {'operator': {'token': 'test-local'}}}, 'deployment.local.json')
        self.assertEqual(self.parameter('get')['token'], 'test-local')
        self.configuration({'parameter_clients': {'operator': {}}})
        self.assertIsNone(self.parameter('get')['token'])
        self.configuration({'parameter_clients': {'operator': {'token': 'test-$(false)\n'}}})
        self.assertEqual(self.parameter('get')['token'], 'test-$(false)\n')

    def test_invalid_configuration_fails_before_connecting_and_never_prints_token(self):
        self.configuration({'parameter_clients': {'operator': {'token': ['invalid']}}})
        stderr = io.StringIO()
        with contextlib.redirect_stderr(stderr), patch('ose.parameters.cli.run', new=AsyncMock()) as service:
            self.assertEqual(self.command('parameter', 'get'), 1)
        service.assert_not_called()
        self.assertIn('token must be text', stderr.getvalue())

    def test_old_plan_fails_before_parameter_connection(self):
        self.configuration({'version': 1})
        for environment in ({}, {'SDL_ROOT': str(self.root)}):
            with self.subTest(environment=environment), patch.dict(os.environ, environment):
                stderr = io.StringIO()
                with contextlib.redirect_stderr(stderr), patch('ose.parameters.cli.run', new=AsyncMock()) as service:
                    self.assertEqual(self.command('parameter', 'get'), 1)
                service.assert_not_called()
                self.assertIn('version 2 application plan', stderr.getvalue())

    def test_build_can_replace_an_obsolete_plan(self):
        self.configuration({'version': 1})
        with patch('ose.app.Application.make') as build:
            self.assertEqual(self.command('build'), 0)
        build.assert_called_once_with(['build'], [])

    def test_sync_storage_follows_caller_and_import_files_follow_application(self):
        caller = Path.cwd()
        synced = self.parameter('sync', '--dir', 'relative folder')
        self.assertEqual(synced['directory'], str(caller / 'relative folder'))
        imported = self.parameter('set', '--file', 'settings.json')
        self.assertEqual(imported['file'], 'settings.json')
        self.assertEqual(imported['cwd'], self.root)

    def test_root_priority_and_env_do_not_read_or_expose_credentials(self):
        os.environ['OSE_APP_ROOT'] = '/wrong/application'
        os.environ['OSE_PARAMETER_TOKEN'] = 'test-secret-not-for-output'
        stdout = io.StringIO()
        with contextlib.redirect_stdout(stdout):
            self.assertEqual(self.command('env'), 0)
        self.assertIn(str(self.root), stdout.getvalue())
        self.assertNotIn('test-secret-not-for-output', stdout.getvalue())
        os.environ['OSE_APP_ROOT'] = str(self.root)
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(main(['app', 'env']), 0)

    @unittest.skipUnless(shutil.which('make'), 'requires make')
    def test_run_builds_before_execution_and_preserves_unlimited_default(self):
        (self.root / 'Makefile').write_text('.PHONY: build\nbuild:\n\t@mkdir -p build\n'
            '\t@printf \'{"version":2,"execution":"local","components":{"source":{"execution_model":2}}}\\n\' > build/plan.json\n'
            '\t@printf "%s" "$(SCENARIO)" > build/scenario\n')
        for options, duration in [([], None), (['--duration', '2'], 2), (['DURATION=3'], 3)]:
            with self.subTest(options=options), patch('ose.execution.supervisor.run', new=AsyncMock()) as launch:
                self.assertEqual(self.command('run', *options, 'SCENARIO=normal'), 0)
                launch.assert_awaited_once_with(str(self.root / 'build/plan.json'), duration, 0,
                                                working_directory=str(Path.cwd()))
                self.assertEqual((self.root / 'build/scenario').read_text(), 'normal')

    def test_run_uses_caller_for_execution_and_trace_but_application_for_build_and_scenario(self):
        caller = Path.cwd()
        self.configuration({'execution': 'local'})
        observed = {}
        def build(*args, **kwargs):
            observed['build_cwd'] = Path.cwd()
        async def simulate(plan, duration, **options):
            observed.update(run_cwd=Path.cwd(), plan=plan, **options)
            return {}
        with patch('ose.app.Application.make', side_effect=build), \
                patch('ose.execution.simulation.run', new=AsyncMock(side_effect=simulate)), \
                contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(self.command('run', '--mode', 'simulated', '--duration', '1',
                                         '--scenario', 'inputs/scenario.json', '--trace', 'result.jsonl'), 0)
        self.assertEqual(observed['build_cwd'], self.root)
        self.assertEqual(observed['run_cwd'], caller)
        self.assertEqual(observed['working_directory'], str(caller))
        self.assertEqual(observed['plan'], str(self.root / 'build/plan.json'))
        self.assertEqual(observed['scenario'], str(self.root / 'inputs/scenario.json'))
        self.assertEqual(observed['trace'], 'result.jsonl')
        self.assertEqual(Path.cwd(), caller)

    def test_invalid_working_directory_fails_before_build(self):
        with patch('ose.app.Application.make') as build, contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(self.command('run', '--working-directory', str(self.root / 'missing')), 1)
        build.assert_not_called()

    @unittest.skipUnless(shutil.which('make'), 'requires make')
    def test_build_failure_prevents_running_and_invalid_duration_does_not_build(self):
        (self.root / 'Makefile').write_text('build:\n\t@exit 7\n')
        with patch('ose.execution.supervisor.run', new=AsyncMock()) as launch, contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(self.command('run'), 1)
            launch.assert_not_called()
        with patch('ose.app.subprocess.run') as build, contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(self.command('run', '--duration', '-1'), 1)
            build.assert_not_called()

    def test_all_help_commands_skip_builds_dependencies_and_configuration(self):
        for arguments in (['--help'], ['run', '--help'], ['record', '--help'],
                          ['record', 'inspect', '--help'], ['parameter', 'get', '--help']):
            with self.subTest(arguments=arguments), contextlib.redirect_stdout(io.StringIO()), \
                    patch('ose.app.subprocess.run') as process, self.assertRaises(SystemExit) as stopped:
                main(['app', '--root', '/nonexistent/application', *arguments])
            self.assertEqual(stopped.exception.code, 0)
            process.assert_not_called()

    def test_record_prepares_only_missing_services_and_then_opens_console(self):
        calls = []
        def invoke(command, **kwargs):
            calls.append(command)
            return subprocess.CompletedProcess(command, 1 if command[-1] == 'import sdl_filter' else 0)
        with patch('ose.app.subprocess.run', side_effect=invoke), \
                patch('ose.recording.console.run', new=AsyncMock(return_value=0)) as console, \
                contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(self.command('record', '--label', 'test-recorder'), 0)
        make = next(command for command in calls if command[0] == 'make')
        self.assertEqual(make[-1], 'filter-python')
        self.assertNotIn('recording-deps', make)
        console.assert_awaited_once()
        self.assertEqual(console.call_args.args[0].label, 'test-recorder')

    def test_record_inspect_preserves_selection_and_does_not_add_a_limit(self):
        with patch('ose.app.subprocess.run') as process:
            self.assertEqual(self.command('record', 'inspect', 'capture with spaces.pcapng',
                '-cs', '-b', '+1s', '-d', '1s', '-t', 'alarm.event', '-f', 'json'), 0)
        arguments = process.call_args.args[0]
        self.assertIn('capture with spaces.pcapng', arguments)
        for option in ('--check', '--summary', '--from=+1s', '--duration=1s', '--topic=alarm.event'):
            self.assertIn(option, arguments)
        self.assertFalse(any('--limit' in value for value in arguments))

    def test_application_defaults_do_not_change_low_level_cli_contract(self):
        parser = create_parser()
        raw = parser.parse_args(['parameter', 'get', 'path', '--client', 'operator'])
        self.assertFalse(raw.export)
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            parser.parse_args(['parameter', 'get'])
