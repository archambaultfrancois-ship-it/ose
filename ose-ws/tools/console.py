#!/usr/bin/env python3
"""Prepare and launch the web console using the checkout's PATH Python."""
import argparse
import os
from pathlib import Path
import subprocess
import sys

FRAMEWORK = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(FRAMEWORK / 'tools'))
from python_env import build_services, dependencies_ready, dependency_path, install_dependencies, require_python


def setup():
    install_dependencies(FRAMEWORK, ['ose', 'ose-ws[recording]'], ['ose-ws/requirements.lock'])
    build_services(FRAMEWORK)
    print('OSE Web Console dependencies are ready.', flush=True)


def launch(arguments):
    environment = dict(os.environ)
    sdl = Path(environment.get('SDL_ROOT', FRAMEWORK / 'sdl'))
    paths = [str(FRAMEWORK / 'ose-ws/src'), str(FRAMEWORK / 'ose/src'),
             *[str(sdl / path) for path in ('runtime/python', 'runtime/filter/python',
                                           'build/filter/release', 'build/recording-deps')],
             str(dependency_path(FRAMEWORK))]
    paths.extend(filter(None, environment.get('PYTHONPATH', '').split(os.pathsep)))
    environment['PYTHONPATH'] = os.pathsep.join(dict.fromkeys(paths))
    environment['SDL_ROOT'] = str(sdl)
    command = [sys.executable, '-m', 'ose_ws.cli',
               '--root', environment.get('OSE_APP_ROOT', os.getcwd()), *arguments]
    os.execve(sys.executable, command, environment)


def selected_command(arguments):
    # Every long CLI option takes one value; do not mistake a config filename
    # such as "check" for the command, or miss a command after --root PATH.
    options = iter(arguments)
    for argument in options:
        if argument == '--':
            return next(options, 'serve')
        if argument.startswith('--'):
            if '=' not in argument:
                next(options, None)
        elif not argument.startswith('-'):
            return argument
    return 'serve'


def main(argv=None):
    arguments = list(sys.argv[1:] if argv is None else argv)
    try:
        require_python()
        if arguments and arguments[0] == 'setup':
            parser = argparse.ArgumentParser(prog='ose-ws setup',
                description='Install console dependencies with the detected Python from PATH.')
            parser.parse_args(arguments[1:])
            setup()
            return 0
        if any(option in arguments for option in ('-h', '--help')):
            print('Checkout setup: ose-ws setup (also automatic on first launch).', flush=True)
        elif selected_command(arguments) not in ('init', 'check') and not dependencies_ready(
                FRAMEWORK, ['fastapi', 'uvicorn', 'websockets']):
            print('Preparing the OSE Web Console dependencies...', flush=True)
            setup()
        launch(arguments)
        return 0
    except (OSError, ValueError, subprocess.CalledProcessError) as error:
        print('Web console setup/launch failed: ' + str(error), file=sys.stderr)
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
