"""Build OSE's optional C extension with the invoking Python and SDL runtime."""
import argparse
import os
from pathlib import Path
import shlex
import subprocess
import sysconfig

SOURCES = ('type_engine.c', 'type_registry.c', 'type_codec.c', 'sdl_wire.c',
           'sdl_dynamic.c', 'type_display.c')


def build(destination, sdl_root):
    runtime = Path(sdl_root).resolve() / 'runtime/c'
    home = Path(__file__).resolve().parents[1]
    destination = Path(destination)
    destination.mkdir(parents=True, exist_ok=True)
    target = destination / ('_native' + sysconfig.get_config_var('EXT_SUFFIX'))
    temporary = target.with_suffix(target.suffix + '.tmp')
    command = shlex.split(os.environ.get('CC', 'cc')) + [
        '-shared', '-fPIC', '-O3', '-std=c11', '-Wall', '-Wextra', '-Werror',
        '-I' + sysconfig.get_path('include'), '-I' + str(runtime),
        str(home / 'native/runtime.c'), *(str(runtime / name) for name in SOURCES),
        '-lm', '-o', str(temporary)]
    subprocess.run(command, check=True)
    temporary.replace(target)
    print(target)


if __name__ == '__main__':
    home = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--sdl-root', type=Path, default=Path(os.environ.get('SDL_ROOT', home.parent / 'sdl')))
    parser.add_argument('--output', type=Path, default=home / 'src/ose')
    args = parser.parse_args()
    build(args.output, args.sdl_root)
