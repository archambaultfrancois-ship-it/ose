"""Application workflows shared by the CLI and the few shell shortcuts."""
import argparse
from contextlib import contextmanager
import copy
import importlib
import json
import math
import os
from pathlib import Path
import re
import subprocess
import sys

from ose.execution.plan import read_plan


def _inherit(commands, name, source, summary):
    parser = commands.add_parser(name, parents=[copy.deepcopy(source)],
                                 add_help=False, help=summary)

    def update(command, prog):
        command.prog = prog
        if command.epilog:
            command.epilog = command.epilog.replace('ose ', 'ose app ')
        for action in command._actions:
            if isinstance(action, argparse._SubParsersAction):
                action._prog_prefix = prog
                for child_name, child in action.choices.items():
                    update(child, prog + ' ' + child_name)
    update(parser, parser.prog)
    return parser


def add_parser(commands):
    core = dict(commands.choices)
    app = commands.add_parser('app', help='build and control the selected application',
        description='Application workflows with automatic plan, endpoint and client defaults.')
    app.add_argument('--root', help='application directory (default: OSE_APP_ROOT or current directory)')
    app.add_argument('--sdl-root', dest='app_sdl_root', help='SDL checkout (default: SDL_ROOT, plan or sibling checkout)')
    actions = app.add_subparsers(dest='app_action', required=True, metavar='ACTION')
    actions.add_parser('env', help='show application, framework and Python paths; never show credentials')
    _inherit(actions, 'component', core['component'], 'enable, disable or inspect application components')
    _inherit(actions, 'logs', core['logs'], 'read or follow application component logs')
    for name, target in (('build', 'build'), ('validate', 'validate'), ('simulate', 'simulate')):
        command = actions.add_parser(name, help=f'run the application Makefile target {target}')
        if name == 'simulate':
            command.add_argument('--duration', type=float, help='execution duration')
        command.add_argument('variables', nargs='*', metavar='NAME=VALUE', help='application Makefile assignments')
    command = actions.add_parser('make', help='invoke the application Makefile')
    command.add_argument('arguments', nargs=argparse.REMAINDER, help='make arguments; use -- before flags')
    command = _inherit(actions, 'run', core['run'], 'build and run locally; Ctrl+C stops by default')
    command.description = 'Build with the application Makefile, then execute its plan from the launch directory. Unlimited by default.'
    command.add_argument('variables', nargs='*', metavar='NAME=VALUE', help='build Makefile assignments; DURATION=SECONDS is also accepted')
    command = _inherit(actions, 'distributed', core['distributed-local'], 'build and manage a loopback development cluster')
    command.set_defaults(duration=None)
    command.description = core['distributed-local'].description
    command.epilog = core['distributed-local'].epilog.replace('ose distributed-local', 'ose app distributed')
    command.add_argument('--build-target', default='build', help='application Makefile build target (default: build)')
    command.add_argument('variables', nargs='*', metavar='NAME=VALUE', help='Makefile assignments selecting the distributed deployment')
    parameter = _inherit(actions, 'parameter', core['parameter'], 'read, export, write, watch or synchronize application parameters')
    parameter.description = 'Use the application endpoint and selected client; get exports path-keyed blocks.'
    for action in parameter._actions:
        if isinstance(action, argparse._SubParsersAction):
            for name, child in action.choices.items():
                for option in child._actions:
                    if option.dest == 'client':
                        option.required = False
                        option.default = os.environ.get('OSE_PARAMETER_CLIENT', 'operator')
                        option.help = 'client identity (default: OSE_PARAMETER_CLIENT or operator)'
                if name == 'get':
                    child.set_defaults(export=True)
                    child.description = 'Export {path: block}; PATH is a regex, --exact selects a literal path, omitted selects all.'
    record = _inherit(actions, 'record', core['record'], 'prepare services and record live outputs, or inspect capture files')
    record.description = 'With no action, prepare missing services and open the recorder console.'
    operations = record.add_subparsers(dest='record_action', metavar='ACTION')
    operations.add_parser('deps', help='rebuild recording and filter services')
    command = operations.add_parser('info', help='show capture metadata')
    command.add_argument('file')
    command = operations.add_parser('inspect', help='display all selected messages, or validate/count them')
    command.add_argument('file')
    command.add_argument('-t', '--topic', action='append')
    command.add_argument('-b', '--from', dest='start', metavar='TIME')
    stop = command.add_mutually_exclusive_group()
    stop.add_argument('-e', '--to', dest='end', metavar='TIME')
    stop.add_argument('-d', '--duration', dest='capture_duration', metavar='DURATION')
    command.add_argument('-c', '--check', action='store_true')
    command.add_argument('-s', '--summary', action='store_true')
    command.add_argument('-n', '--limit', type=int, help='maximum displayed messages (default: unlimited)')
    command.add_argument('-a', '--full', action='store_true')
    command.add_argument('-f', '--format', choices=('text', 'json', 'table'), default='text')
    command.add_argument('-i', '--indent-width', type=int)


class Application:
    def __init__(self, args):
        self.caller = Path.cwd()
        self.root = Path(args.root or os.environ.get('OSE_APP_ROOT') or self.caller).resolve()
        if not self.root.is_dir():
            raise ValueError(f'application directory does not exist: {self.root}')
        self.python = sys.executable
        self.plan = self.root / getattr(args, 'plan', 'build/plan.json')
        selected = args.app_sdl_root or getattr(args, 'sdl_root', None) or os.environ.get('SDL_ROOT')
        # Build commands must be able to replace an obsolete compiled plan.
        rebuilding = getattr(args, 'app_action', None) in ('build', 'validate', 'make', 'run', 'simulate', 'distributed')
        if not selected and self.plan.is_file():
            try:
                selected = read_plan(self.plan).get('sdl_root')
            except ValueError:
                if not rebuilding:
                    raise
        if not selected and (self.root / 'deps/sdl-ose/sdl').is_dir():
            selected = self.root / 'deps/sdl-ose/sdl'
        self.sdl = Path(selected).resolve() if selected else Path(__file__).resolve().parents[3] / 'sdl'
        self.paths = [str(Path(__file__).resolve().parents[1]),
                      *[str(self.sdl / path) for path in ('runtime/python', 'runtime/filter/python',
                                                         'build/filter/release', 'build/recording-deps')]]

    @staticmethod
    def read(path):
        try:
            document = json.loads(path.read_text())
            if not isinstance(document, dict):
                raise ValueError('expected a JSON object')
            return document
        except (OSError, UnicodeError, ValueError) as error:
            raise ValueError(f'cannot read application configuration {path}: {error}') from error

    @contextmanager
    def activate(self):
        previous, paths = dict(os.environ), sys.path[:]
        inherited = os.environ.get('PYTHONPATH', '').split(os.pathsep)
        os.environ['PYTHONPATH'] = os.pathsep.join(dict.fromkeys(self.paths + [p for p in inherited if p]))
        os.environ.update(OSE_APP_ROOT=str(self.root), SDL_ROOT=str(self.sdl),
                          FRAMEWORK_ROOT=str(self.sdl.parent))
        sys.path[:0] = self.paths
        os.chdir(self.root)
        try:
            yield
        finally:
            os.chdir(self.caller)
            sys.path[:] = paths
            os.environ.clear()
            os.environ.update(previous)

    def make(self, targets, variables=()):
        if not (self.root / 'Makefile').is_file() and not (self.root / 'makefile').is_file():
            raise ValueError(f'application Makefile not found in {self.root}')
        for value in variables:
            if not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]*=.*', value, re.DOTALL):
                raise ValueError(f'expected a Makefile assignment, got: {value}')
        subprocess.run(['make', f'PYTHON={self.python}', *targets, *variables], check=True, cwd=self.root)

    def parameter_token(self, args):
        if args.token_env != 'OSE_PARAMETER_TOKEN' or args.token_env in os.environ:
            return
        for configuration in (self.plan, self.root / 'deployment.local.json', self.root / 'deployment.json'):
            if not configuration.is_file():
                continue
            try:
                document = read_plan(configuration) if configuration == self.plan else self.read(configuration)
                token = document.get('parameter_clients', {}).get(args.client, {}).get('token', '')
            except (AttributeError, TypeError) as error:
                raise ValueError(f'invalid parameter clients in {configuration}') from error
            if not isinstance(token, str) or '\0' in token:
                raise ValueError(f'parameter client token must be text without NUL characters: {configuration}')
            if token:
                os.environ[args.token_env] = token
            # The first existing plan/configuration is authoritative, even without a token.
            return

    def recording_services(self, rebuild=False):
        targets = []
        for module, target in (('sdl_recording', 'recording-deps'), ('sdl_filter', 'filter-python')):
            if rebuild or subprocess.run([self.python, '-c', f'import {module}'],
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode:
                targets.append(target)
        if targets:
            print('Preparing recording services: ' + ' '.join(targets), file=sys.stderr, flush=True)
            subprocess.run(['make', '-C', str(self.sdl), f'PYTHON={self.python}', *targets], check=True)
        importlib.invalidate_caches()
        subprocess.run([self.python, '-c', 'import sdl_recording, sdl_filter'], check=True)


def run(args):
    from ose.cli import execute
    context = Application(args)
    if args.app_action in ('run', 'distributed') and args.params_sync is not None:
        from ose.execution.launch import sync_options
        if args.app_action == 'run' and args.mode != 'realtime':
            raise ValueError('--params-sync requires real-time mode')
        args.params_sync = sync_options(args)['directory']
    if args.app_action == 'run':
        directory = (context.caller / (args.working_directory or '.')).resolve()
        if not directory.is_dir():
            raise ValueError(f'working directory does not exist: {directory}')
        args.working_directory = str(directory)
    if args.app_action == 'parameter' and args.parameter_action == 'sync':
        # Sync storage follows the caller; plan/endpoint and other files follow the application.
        args.dir = str(Path(args.dir).resolve())
    with context.activate():
        action = args.app_action
        if action in ('run', 'distributed', 'simulate'):
            from ose.execution.identity import identify
            identify('launch')
        if action == 'env':
            print(f'Application: {context.root}\nFramework: {context.sdl.parent}\nSDL: {context.sdl}\nPython: {context.python}')
            return 0
        if action == 'parameter':
            context.parameter_token(args)
            args.action, args.sdl_root = 'parameter', str(context.sdl)
            return execute(args)
        if action == 'component':
            args.action = 'component'
            return execute(args)
        if action == 'logs':
            args.action = 'logs'
            return execute(args)
        if action == 'record':
            operation = args.record_action
            if operation == 'deps':
                context.recording_services(rebuild=True)
                return 0
            if operation in ('info', 'inspect'):
                command = [context.python, str(context.sdl / 'tools/recording/sdl-recording'), operation, args.file]
                if operation == 'inspect':
                    command += ['--format', args.format]
                    for name, flag in (('start', '--from'), ('end', '--to'), ('capture_duration', '--duration'),
                                       ('limit', '--limit'), ('indent_width', '--indent-width')):
                        value = getattr(args, name)
                        if value is not None:
                            command.append(f'{flag}={value}')
                    for topic in args.topic or ():
                        command.append('--topic=' + topic)
                    command += [flag for name, flag in (('check', '--check'), ('summary', '--summary'),
                                                        ('full', '--full')) if getattr(args, name)]
                subprocess.run(command, check=True)
                return 0
            context.recording_services()
            args.action, args.sdl_root = 'record', str(context.sdl)
            return execute(args)
        if action == 'make':
            arguments = args.arguments[1:] if args.arguments[:1] == ['--'] else args.arguments
            context.make(arguments)
            return 0
        variables = list(args.variables)
        duration = getattr(args, 'duration', None)
        for value in variables if action in ('run', 'simulate', 'distributed') else ():
            if value.startswith('DURATION=') and getattr(args, 'duration', None) is None:
                try:
                    duration = float(value.split('=', 1)[1])
                except ValueError as error:
                    raise ValueError('duration must be a positive finite number') from error
        if duration is not None and (not math.isfinite(duration) or duration <= 0):
            raise ValueError('duration must be a positive finite number')
        if action in ('run', 'distributed'):
            variables = [value for value in variables if not value.startswith('DURATION=')]
            context.make([args.build_target if action == 'distributed' else 'build'], variables)
            args.action = 'distributed-local' if action == 'distributed' else 'run'
            args.duration = 300 if action == 'distributed' and duration is None else duration
            args.plan = str(context.plan)
            if action == 'run' and args.scenario is not None:
                args.scenario = str((context.root / args.scenario).resolve())
            # Build/configuration paths belong to the application; runtime
            # outputs and component relative paths belong to the caller.
            os.chdir(context.caller)
            return execute(args)
        if duration is not None:
            variables.append(f'DURATION={duration:g}')
        context.make([action], variables)
        return 0
