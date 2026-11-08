"""One PATH Python for the checkout, with dependencies stored as build output."""
import os
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]


def require_python():
    if sys.version_info < (3, 10):
        raise ValueError(f'Python 3.10+ is required; {sys.executable} is Python '
                         f'{sys.version.split()[0]}. Use a Python 3.10+ interpreter from PATH.')


def dependency_path(framework):
    return Path(framework) / 'build/python-deps'


def use_dependencies(framework):
    directory = str(dependency_path(framework))
    if directory not in sys.path:
        sys.path.insert(0, directory)
    paths = [directory, *filter(None, os.environ.get('PYTHONPATH', '').split(os.pathsep))]
    os.environ['PYTHONPATH'] = os.pathsep.join(dict.fromkeys(paths))


def dependencies_ready(framework, modules):
    environment = dict(os.environ, PYTHONPATH=str(dependency_path(framework)))
    return subprocess.run([sys.executable, '-c', 'import ' + ', '.join(modules)],
                          env=environment, stdout=subprocess.DEVNULL,
                          stderr=subprocess.DEVNULL).returncode == 0


def install_dependencies(framework, projects, constraints):
    require_python()
    framework = Path(framework)
    environment = dict(os.environ)
    environment.pop('PYTHONPATH', None)
    command = [sys.executable, '-m', 'pip', 'install', '--disable-pip-version-check',
               '--upgrade', '--target', str(dependency_path(framework))]
    for constraint in constraints:
        command.extend(['-c', str(framework / constraint)])
    command.extend(str(framework / project) for project in projects)
    subprocess.run(command, check=True, env=environment)


def main():
    try:
        require_python()
        if sys.argv[1:] == ['--check']:
            return 0
        if sys.argv[1:]:
            raise ValueError('Usage: python3 tools/python_env.py [--check]')
        install_dependencies(ROOT, ['ose[recording,sync]', 'ose-ws[test]', 'ose-editor[test]'],
                             ['ose-ws/requirements.lock', 'ose-editor/requirements.lock'])
        print('Checkout Python dependencies are ready in build/python-deps.')
        return 0
    except (OSError, ValueError, subprocess.CalledProcessError) as error:
        print(str(error), file=sys.stderr)
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
