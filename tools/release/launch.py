"""Launch installed tools without changing the application's working directory."""
import json
import os
from pathlib import Path
import runpy
import sys


def main():
    prefix = Path(__file__).resolve().parent
    framework = prefix / 'framework'
    report = json.loads((prefix / 'install-report.json').read_text())
    action = sys.argv.pop(1)
    if action == 'sdlc' and report['profile'] != 'sdk':
        raise SystemExit('SDL compilation requires the sdk installation profile.')
    paths = [framework / 'ose/src', framework / 'sdl/runtime/python']
    if 'web' in report['features']:
        paths.append(framework / 'ose-ws/src')
    if 'filter' in report['features']:
        paths += [framework / 'sdl/runtime/filter/python', framework / 'sdl/build/filter/release']
    sys.path[:0] = list(map(str, paths))
    inherited = os.environ.get('PYTHONPATH', '').split(os.pathsep)
    os.environ['PYTHONPATH'] = os.pathsep.join(dict.fromkeys([*map(str, paths), *filter(None, inherited)]))
    os.environ['FRAMEWORK_ROOT'] = str(framework)
    os.environ['SDL_ROOT'] = str(framework / 'sdl')
    os.environ['OSE_ROOT'] = str(framework / 'ose')
    os.environ['OSE_PYTHON'] = sys.executable
    os.environ['PIP_NO_INDEX'] = '1'
    os.environ['PIP_FIND_LINKS'] = str(prefix / 'packages')
    if (prefix / 'cargo-vendor').is_dir():
        os.environ['CARGO_HOME'] = str(prefix / 'cargo-home')
        # Keep rustup's normal toolchain discovery when using an isolated Cargo home.
        os.environ.setdefault('RUSTUP_HOME', str(Path.home() / '.rustup'))
    try:
        if action in ('ose', 'web'):
            runpy.run_module('ose.cli' if action == 'ose' else 'ose_ws.cli', run_name='__main__')
        else:
            path = framework / ('sdl/sdlc/generator.py' if action == 'sdlc' else 'sdl/tools/recording/sdl-recording')
            sys.path.insert(0, str(path.parent))
            runpy.run_path(str(path), run_name='__main__')
    except ModuleNotFoundError as error:
        raise SystemExit(f'Missing Python dependency: {error.name}. See {prefix / "installation.log"} and rerun the installer after supplying compatible wheels.') from None


if __name__ == '__main__':
    main()
