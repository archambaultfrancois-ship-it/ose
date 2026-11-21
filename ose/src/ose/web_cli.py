"""Shared CLI definitions and launchers for the optional browser applications."""
import importlib
import os
from pathlib import Path
import sys


def configure(parser, name, *, checkout=False):
    if name == 'ws':
        parser.add_argument('command', nargs='?', choices=(('setup',) if checkout else ()) +
                            ('init', 'check', 'serve'), default='serve',
                            help='serve by default' + ('; setup prepares source-checkout dependencies' if checkout else ''))
        parser.add_argument('--root', help='application directory; default: OSE_APP_ROOT or current directory' if checkout else
                            'application directory; enables application configuration and credential defaults')
        parser.add_argument('--config', help='configuration file (default: ose-ws.json in --root or the launch directory)')
        parser.add_argument('--working-directory', help='base for relative state and capture paths (default: launch directory)')
        parser.add_argument('--endpoint', help='OSE endpoint file, relative to the calling directory')
        parser.add_argument('--host', help='HTTP bind address; network access requires configured proxy authentication')
        parser.add_argument('--port', type=int, help='HTTP port (default: 8080)')
        parser.add_argument('--auth', action='store_true', help='require a browser access token (default: direct local access)')
    else:
        if checkout:
            parser.add_argument('command', nargs='?', choices=('setup',),
                                help='prepare source-checkout dependencies without starting the editor')
        parser.add_argument('--root', default='.', help='application source directory (default: ' +
                            ('OSE_APP_ROOT or current directory)' if checkout else 'current directory)'))
        parser.add_argument('--model', default='model.json', help='model file (default: model.json)')
        parser.add_argument('--deployment', action='append', default=[], help='deployment file; repeat to load several')
        parser.add_argument('--source-root', action='append', default=[], help='additional allowed schema, subsystem definition or deployment root; repeat')
        parser.add_argument('--sdl-root', default=os.environ.get('SDL_ROOT', str(Path(__file__).resolve().parents[3] / 'sdl')),
                            help='SDL resources (default: SDL_ROOT or the sibling sdl directory)')
        parser.add_argument('--port', type=int, default=8082, help='HTTP port (default: 8082)')
        parser.add_argument('--auth', action='store_true', help='require a browser access token (default: direct local access)')
        parser.add_argument('--create', action='store_true', help='create model/deployment drafts if missing')
        parser.add_argument('--check', action='store_true', help='inspect project and report syntax errors without starting HTTP')


def add_parsers(commands):
    for name, summary in (('ws', 'open the application web console'),
                          ('editor', 'open the graphical model editor')):
        parser = commands.add_parser(name, help=summary, description=summary.capitalize() + '.',
            epilog=f'Examples: ose {name}; ose {name} --port ' + ('8081' if name == 'ws' else '8083') +
                   f'; ose {name} setup (source checkout only).')
        configure(parser, name, checkout=True)


def launch(name, arguments):
    project, module, feature = ('ose-ws', 'ose_ws.cli', 'web') if name == 'ws' else ('ose-editor', 'ose_editor.cli', 'editor')
    framework = Path(__file__).resolve().parents[3]
    script = framework / project / 'tools' / ('console.py' if name == 'ws' else 'editor.py')
    try:
        if script.is_file() and (framework / 'tools/python_env.py').is_file():
            os.execv(sys.executable, [sys.executable, str(script), *arguments])
            return 0
        if arguments and arguments[0] == 'setup':
            print(f'ose {name} setup requires a source checkout. '
                  'For an installed release, rerun the installer to repair dependencies.', file=sys.stderr)
            return 2
        application = importlib.import_module(module)
        root = os.environ.get('OSE_APP_ROOT')
        return application.main(['--root', root, *arguments] if root else arguments)
    except ModuleNotFoundError as error:
        print(f'Cannot start ose {name}: missing Python module {error.name}. '
              f'Install {project} or include the {feature} feature in the OSE release.', file=sys.stderr)
        return 2
    except OSError as error:
        print(f'Cannot start ose {name}: {error}', file=sys.stderr)
        return 2
