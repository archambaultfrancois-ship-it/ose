#!/usr/bin/env python3
"""Build a platform-specific, offline OSE/SDL/web framework release."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import platform
import shutil
import subprocess
import sys
import sysconfig
import tarfile
import tempfile


ROOT = Path(__file__).resolve().parents[2]
HERE = Path(__file__).resolve().parent
REQUIREMENTS = {
    'base': ['pip', 'setuptools==84.2.1', 'wheel==0.48.0'],
    'sdk': ['PyYAML==6.0.3'],
    'web': ['fastapi==0.162.0', 'uvicorn==0.54.0', 'websockets==17.4'],
    'recording': ['mcap==1.5.0'],
    'filter': [],
    'sync': ['inotify-simple>=1.3.5,<3'],
}


def run(command, **kwargs):
    print('+ ' + ' '.join(map(str, command)), flush=True)
    subprocess.run(list(map(str, command)), check=True, **kwargs)


def copy_resources(root, destination):
    if not (root / 'ose-ws/src/ose_ws/static/index.html').is_file():
        raise ValueError('Build the embedded web frontend before creating a release')
    directories = ['ose/src/ose', 'ose/sdk', 'ose-ws/src/ose_ws', 'sdl/sdlc',
                   *['sdl/runtime/' + lang for lang in ('python', 'c', 'rust', 'java', 'ada', 'filter', 'recording')],
                   'sdl/tools/filter', 'sdl/tools/recording']
    excluded = {'__pycache__', 'tests', 'target', 'build', '.git', 'matlab'}
    for name in directories:
        source = root / name
        for path in sorted(source.rglob('*')):
            relative = path.relative_to(source)
            if any(part in excluded for part in relative.parts) or path.suffix in ('.pyc', '.pyo'):
                continue
            if path.is_symlink():
                raise ValueError('Source resource cannot be a symlink: ' + str(path))
            if path.is_file():
                target = destination / 'framework' / name / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(path, target)
    shutil.copy2(HERE / 'sdl.Makefile', destination / 'framework/sdl/Makefile')
    for name in ('README.md', 'LICENSE', 'LICENSE.md', 'COPYING', 'NOTICE'):
        for project in ('', 'ose', 'sdl', 'ose-ws'):
            source = root / project / name
            if source.is_file():
                target = destination / 'framework' / project / name
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source, target)
    # Include third-party license files, including those accompanying the frontend.
    for path in (root / 'ose-ws/src/ose_ws/static').rglob('*'):
        if path.is_file() and path.suffix in ('.js', '.css') and not path.stat().st_size:
            raise ValueError('Empty frontend asset: ' + str(path))


def prepare_packages(stage, packages_dir=None, offline=False):
    packages = stage / 'packages'
    packages.mkdir()
    command = [sys.executable, '-m', 'pip', 'wheel', '--wheel-dir', str(packages),
               '--constraint', str(ROOT / 'ose-ws/requirements.lock')]
    if packages_dir:
        command += ['--find-links', str(packages_dir)]
    if offline:
        command.append('--no-index')
    command += [requirement for group in REQUIREMENTS.values() for requirement in group]
    run(command)


def prepare_native(stage, offline=False):
    sdl = stage / 'framework/sdl'
    vendor = stage / 'cargo-vendor'
    command = ['cargo', 'vendor', '--locked', '--manifest-path', str(sdl / 'runtime/recording/Cargo.toml')]
    if offline:
        command.append('--offline')
    # The recording dependency graph includes the external crates; filter and
    # component runtimes have only local path dependencies.
    run(command + [str(vendor)])
    cargo_home = stage / '.build-cargo'
    cargo_home.mkdir()
    (cargo_home / 'config.toml').write_text('[source.crates-io]\nreplace-with="ose-vendored"\n'
        '[source.ose-vendored]\ndirectory=' + json.dumps(str(vendor)) + '\n[net]\noffline=true\n')
    env = dict(os.environ, CARGO_HOME=str(cargo_home))
    env.setdefault('RUSTUP_HOME', str(Path.home() / '.rustup'))
    run([sys.executable, sdl / 'tools/filter/build_native.py', '--python'], env=env)
    # Retain only the loadable filter and Python extension, not compiler output.
    release = sdl / 'build/filter/release'
    for path in list(release.iterdir()):
        if path.is_file() and (path.name.startswith('_sdl_filter_native') or
                              path.name in ('libsdl_filter.so', 'libsdl_filter.dylib', 'sdl_filter.dll')):
            continue
        if path.is_dir():
            shutil.rmtree(path)
        else:
            path.unlink()
    for path in list((sdl / 'build/filter').iterdir()):
        if path != release:
            if path.is_dir():
                shutil.rmtree(path)
            else:
                path.unlink()
    shutil.rmtree(cargo_home)


def manifest(stage, root):
    revision = subprocess.check_output(['git', '-C', str(root), 'rev-parse', 'HEAD'], text=True).strip()
    dirty = bool(subprocess.check_output(['git', '-C', str(root), 'status', '--porcelain'], text=True))
    files = {p.relative_to(stage).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
             for p in sorted(stage.rglob('*')) if p.is_file()}
    data = {'format': 1, 'revision': revision, 'dirty': dirty,
            'target': {'platform': sysconfig.get_platform(), 'system': sys.platform,
                       'machine': platform.machine(), 'python': f'{sys.version_info.major}.{sys.version_info.minor}'},
            'requirements': REQUIREMENTS, 'files': files}
    if (stage / 'python/runtime.json').is_file():
        data['python_runtime'] = json.loads((stage / 'python/runtime.json').read_text())
    return data


def write_bundle(archive, bundle):
    header = (HERE / 'self-extract.sh').read_text()
    checksum = hashlib.sha256(archive.read_bytes()).hexdigest()
    header = header.replace('@PAYLOAD_SHA256@', checksum)
    header = header.replace('@PAYLOAD_LINE@', str(header.count('\n') + 1))
    with bundle.open('wb') as output:
        output.write(header.encode())
        with archive.open('rb') as payload:
            shutil.copyfileobj(payload, output)
    bundle.chmod(0o755)
    checksum = hashlib.sha256(bundle.read_bytes()).hexdigest()
    bundle.with_name(bundle.name + '.sha256').write_text(checksum + '  ' + bundle.name + '\n')


def main(argv=None):
    from private_python import prepare as prepare_python
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, default=ROOT / 'build/releases')
    parser.add_argument('--packages-dir', type=Path, help='local wheels to use when preparing dependencies')
    parser.add_argument('--offline', action='store_true', help='prepare the release using only locally available wheels and crates')
    args = parser.parse_args(argv)
    output = args.output.expanduser().resolve()
    output.mkdir(parents=True, exist_ok=True)
    try:
        with tempfile.TemporaryDirectory(prefix='ose-release-') as temporary:
            stage = Path(temporary) / 'ose-release'
            stage.mkdir()
            copy_resources(ROOT, stage)
            for name in ('install.py', 'launch.py', 'ose-install', 'install-resources.sh'):
                shutil.copy2(HERE / name, stage / name)
            shutil.copy2(ROOT / 'docs/release.md', stage / 'README.md')
            (stage / 'ose-install').chmod(0o755)
            prepare_packages(stage, args.packages_dir, args.offline)
            prepare_native(stage, args.offline)
            prepare_python(stage)
            data = manifest(stage, ROOT)
            (stage / 'manifest.json').write_text(json.dumps(data, indent=2, sort_keys=True) + '\n')
            (stage / 'SHA256SUMS').write_text(''.join(value + '  ' + name + '\n' for name, value in data['files'].items()))
            stem = 'ose-' + sysconfig.get_platform() + f'-cp{sys.version_info.major}{sys.version_info.minor}'
            archive = Path(temporary) / (stem + '.tar.gz')
            with tarfile.open(archive, 'w:gz') as stream:
                stream.add(stage, arcname='ose-release')
            bundle = output / (stem + '.sh')
            write_bundle(archive, bundle)
            # Replace the former distribution format for this exact target.
            for suffix in ('.tar.gz', '.tar.gz.sha256'):
                (output / (stem + suffix)).unlink(missing_ok=True)
            print('Release: ' + str(bundle))
        return 0
    except (OSError, ValueError, subprocess.SubprocessError) as error:
        print('Release build failed: ' + str(error), file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
