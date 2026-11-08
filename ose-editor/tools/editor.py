#!/usr/bin/env python3
"""Prepare and launch the model editor directly from a framework checkout."""
import argparse
import os
from pathlib import Path
import shutil
import subprocess
import sys
import venv


FRAMEWORK = Path(__file__).resolve().parents[2]


def dependency_environment():
    environment = dict(os.environ)
    # Prepared SDL directories must not satisfy this environment's dependencies.
    environment.pop('PYTHONPATH', None)
    return environment


def dependencies_ready(python):
    result = subprocess.run([str(python), '-c',
                             'import sys; assert sys.version_info >= (3, 10); import fastapi, uvicorn, yaml'],
                            env=dependency_environment(), stdout=subprocess.DEVNULL,
                            stderr=subprocess.DEVNULL)
    return result.returncode == 0


def setup(python, managed):
    if managed and not python.is_file():
        if sys.version_info < (3, 10):
            raise ValueError('The editor requires Python 3.10+. Set OSE_PYTHON=python3.12 '
                             'before running ose-editor setup.')
        venv.EnvBuilder(with_pip=True).create(python.parent.parent)
    subprocess.run([str(python), '-c',
                    'import sys; sys.exit(0 if sys.version_info >= (3, 10) else '
                    '"The editor requires Python 3.10+; select a compatible interpreter.")'],
                   check=True, env=dependency_environment())
    subprocess.run([str(python), '-m', 'pip', 'install', '--disable-pip-version-check',
                    '-c', str(FRAMEWORK / 'ose-editor/requirements.lock'),
                    str(FRAMEWORK / 'ose'), str(FRAMEWORK / 'ose-editor')],
                   check=True, env=dependency_environment())
    if not dependencies_ready(python):
        raise ValueError('Editor dependencies are still unavailable after setup.')
    print('OSE Model Editor dependencies are ready.', flush=True)


def launch(python, arguments):
    environment = dict(os.environ)
    sdl = Path(environment.get('SDL_ROOT', FRAMEWORK / 'sdl'))
    paths = [str(FRAMEWORK / 'ose-editor/src'), str(FRAMEWORK / 'ose/src'),
             str(sdl / 'runtime/python')]
    paths.extend(filter(None, environment.get('PYTHONPATH', '').split(os.pathsep)))
    environment['PYTHONPATH'] = os.pathsep.join(dict.fromkeys(paths))
    command = [str(python), '-m', 'ose_editor.cli',
               '--root', environment.get('OSE_APP_ROOT', os.getcwd()),
               '--sdl-root', str(sdl), *arguments]
    # Replace the launcher so signals reach the editor and release its project lock.
    os.execvpe(str(python), command, environment)


def main(argv=None):
    arguments = list(sys.argv[1:] if argv is None else argv)
    override = os.environ.get('OSE_EDITOR_PYTHON')
    python = Path(shutil.which(override) or override) if override else FRAMEWORK / 'ose-editor/.venv/bin/python'
    try:
        if arguments and arguments[0] == 'setup':
            parser = argparse.ArgumentParser(prog='ose-editor setup',
                description='Install the checkout editor dependencies in its dedicated environment.')
            parser.parse_args(arguments[1:])
            setup(python, managed=not override)
            return 0
        if any(option in arguments for option in ('-h', '--help', '--check')):
            if '-h' in arguments or '--help' in arguments:
                print('Checkout setup: ose-editor setup (also automatic on first launch).', flush=True)
            if not override and not python.is_file():
                python = Path(sys.executable)
        elif not python.is_file() or not dependencies_ready(python):
            if override:
                raise ValueError('The selected OSE_EDITOR_PYTHON cannot run the editor. '
                                 'Run ose-editor setup to install its dependencies.')
            print('Preparing the OSE Model Editor environment...', flush=True)
            setup(python, managed=True)
        launch(python, arguments)
        return 0
    except (OSError, ValueError, subprocess.CalledProcessError) as error:
        print('Editor setup/launch failed: ' + str(error), file=sys.stderr)
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
