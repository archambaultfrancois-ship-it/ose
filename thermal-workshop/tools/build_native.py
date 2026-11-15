"""Compile the application's sample/burn kernels for the selected Python."""
import os
from pathlib import Path
import shlex
import subprocess
import sysconfig


def build(destination):
    home = Path(__file__).resolve().parents[1]
    destination = Path(destination)
    destination.mkdir(parents=True, exist_ok=True)
    target = destination / ('_thermal_native' + sysconfig.get_config_var('EXT_SUFFIX'))
    source = home / 'native/workload.c'
    if target.is_file() and target.stat().st_mtime_ns >= source.stat().st_mtime_ns:
        return
    temporary = target.with_suffix(target.suffix + '.tmp')
    command = shlex.split(os.environ.get('CC', 'cc')) + [
        '-shared', '-fPIC', '-O3', '-std=c11', '-Wall', '-Wextra', '-Werror',
        '-I' + sysconfig.get_path('include'), str(source), '-lm', '-o', str(temporary)]
    subprocess.run(command, check=True)
    temporary.replace(target)
