#!/usr/bin/env python3
"""Prepare and launch the editor using the checkout's PATH Python."""
import argparse
import os
from pathlib import Path
import subprocess
import sys

FRAMEWORK = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(FRAMEWORK / 'tools'))
from python_env import dependencies_ready, dependency_path, install_dependencies, require_python


def setup():
    install_dependencies(FRAMEWORK, ['ose', 'ose-editor'], ['ose-editor/requirements.lock'])
    print('OSE Model Editor dependencies are ready.', flush=True)


def launch(arguments):
    environment = dict(os.environ)
    sdl = Path(environment.get('SDL_ROOT', FRAMEWORK / 'sdl'))
    paths = [str(FRAMEWORK / 'ose-editor/src'), str(FRAMEWORK / 'ose/src'),
             str(sdl / 'runtime/python'), str(dependency_path(FRAMEWORK))]
    paths.extend(filter(None, environment.get('PYTHONPATH', '').split(os.pathsep)))
    environment['PYTHONPATH'] = os.pathsep.join(dict.fromkeys(paths))
    command = [sys.executable, '-m', 'ose_editor.cli',
               '--root', environment.get('OSE_APP_ROOT', os.getcwd()),
               '--sdl-root', str(sdl), *arguments]
    os.execve(sys.executable, command, environment)


def main(argv=None):
    arguments = list(sys.argv[1:] if argv is None else argv)
    try:
        require_python()
        if arguments and arguments[0] == 'setup':
            parser = argparse.ArgumentParser(prog='ose-editor setup',
                description='Install editor dependencies with the detected Python from PATH.')
            parser.parse_args(arguments[1:])
            setup()
            return 0
        if any(option in arguments for option in ('-h', '--help', '--check')):
            if '-h' in arguments or '--help' in arguments:
                print('Checkout setup: ose-editor setup (also automatic on first launch).', flush=True)
        elif not dependencies_ready(FRAMEWORK, ['fastapi', 'uvicorn', 'yaml']):
            print('Preparing the OSE Model Editor dependencies...', flush=True)
            setup()
        launch(arguments)
        return 0
    except (OSError, ValueError, subprocess.CalledProcessError) as error:
        print('Editor setup/launch failed: ' + str(error), file=sys.stderr)
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
