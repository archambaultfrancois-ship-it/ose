"""Package the build interpreter, its stdlib and non-system ELF dependencies."""
import json
import os
from pathlib import Path
import re
import shlex
import shutil
import subprocess
import sys
import sysconfig
import tempfile
import zipfile


HERE = Path(__file__).resolve().parent
# These are provided by the target OS/loader. All other linked libraries travel
# with Python (OpenSSL, SQLite, libffi, compression, Termux compatibility, ...).
SYSTEM_LIBRARIES = {
    'libc.so', 'libm.so', 'libdl.so', 'liblog.so', 'libandroid.so',
    'libc.so.6', 'libm.so.6', 'libdl.so.2', 'libpthread.so.0', 'librt.so.1',
    'libutil.so.1', 'libresolv.so.2',
}
EXCLUDED = {'site-packages', 'dist-packages', '__pycache__', 'test', 'tests',
            'idlelib', 'tkinter', 'turtledemo', 'ensurepip'}


def dynamic(path):
    result = subprocess.run(['readelf', '-d', str(path)], capture_output=True, text=True, check=True)
    needed = re.findall(r'Shared library: \[([^]]+)\]', result.stdout)
    paths = re.findall(r'\((?:RUNPATH|RPATH)\).*?\[([^]]*)\]', result.stdout)
    directories = [Path(value.replace('${ORIGIN}', str(path.parent)).replace('$ORIGIN', str(path.parent)))
                   for entry in paths for value in entry.split(':') if value]
    return needed, directories


def library_cache():
    result = {}
    ldconfig = shutil.which('ldconfig') or ('/sbin/ldconfig' if Path('/sbin/ldconfig').is_file() else None)
    if ldconfig:
        completed = subprocess.run([ldconfig, '-p'], capture_output=True, text=True)
        for line in completed.stdout.splitlines():
            match = re.match(r'\s*(\S+)\s+.*=>\s+(\S+)', line)
            if match:
                result.setdefault(match[1], []).append(Path(match[2]))
    return result


def copy_libraries(seeds, destination, local_root=None):
    cache = library_cache()
    search = [Path(p) for p in os.environ.get('LD_LIBRARY_PATH', '').split(':') if p]
    search += [Path(sysconfig.get_config_var('LIBDIR')), Path(sys.base_prefix) / 'lib',
               Path('/usr/local/lib'), Path('/usr/lib'), Path('/lib')]
    pending, seen, copied = list(seeds), set(), {}
    while pending:
        path = pending.pop().resolve()
        if path in seen:
            continue
        seen.add(path)
        needed, local = dynamic(path)
        for name in needed:
            if name in SYSTEM_LIBRARIES or name.startswith(('ld-linux-', 'ld-musl-')):
                continue
            if '/' in name:
                raise ValueError('Non-relocatable absolute library dependency: ' + name)
            candidates = [directory / name for directory in [path.parent, *local, *search]] + cache.get(name, [])
            source = next((candidate.resolve() for candidate in candidates if candidate.is_file()), None)
            if source is None:
                raise ValueError(f'Cannot bundle {name}, required by {path}')
            if local_root and source.is_relative_to(local_root):
                # Wheels/framework modules already carry their adjacent libraries.
                pending.append(source)
                continue
            if name in copied and copied[name] != source:
                raise ValueError('Conflicting private library: ' + name)
            copied[name] = source
            shutil.copy2(source, destination / name)
            pending.append(source)
    return sorted(set(copied.values()))


def package_libraries(stage, destination):
    """Collect external dependencies of native wheels, preserving wheel RPATHs."""
    dependencies = []
    for wheel in sorted((stage / 'packages').glob('*.whl')):
        with tempfile.TemporaryDirectory(prefix='ose-wheel-libraries-') as temporary:
            root = Path(temporary).resolve()
            modules = []
            with zipfile.ZipFile(wheel) as archive:
                for item in archive.infolist():
                    if item.is_dir():
                        continue
                    relative = Path(item.filename)
                    if relative.is_absolute() or '..' in relative.parts:
                        raise ValueError('Unsafe wheel path: ' + item.filename)
                    with archive.open(item) as stream:
                        if stream.read(4) != b'\x7fELF':
                            continue
                    path = root / relative
                    path.parent.mkdir(parents=True, exist_ok=True)
                    path.write_bytes(archive.read(item))
                    modules.append(path)
            dependencies += copy_libraries(modules, destination, local_root=root)
    framework = stage / 'framework'
    modules = list(framework.rglob('*.so'))
    if modules:
        dependencies += copy_libraries(modules, destination, local_root=framework)
    return dependencies


def licenses(runtime, paths):
    """Keep distribution copyright notices for the libraries copied from a host."""
    destination = runtime / 'licenses'
    destination.mkdir()
    if not shutil.which('dpkg-query'):
        return
    packages = set()
    for path in paths:
        result = subprocess.run(['dpkg-query', '-S', str(path)], capture_output=True, text=True)
        for line in result.stdout.splitlines():
            if ': ' in line:
                packages.add(line.split(': ', 1)[0])
    for package in sorted(packages):
        result = subprocess.run(['dpkg-query', '-L', package], capture_output=True, text=True)
        for name in result.stdout.splitlines():
            source = Path(name)
            if '/share/doc/' in name and source.is_file() and (
                    source.name.lower().startswith(('license', 'copyright', 'copying', 'notice'))):
                target = destination / package / source.name
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source, target)


def prepare(stage):
    if sys.platform not in ('linux', 'android'):
        raise ValueError('Bundled Python currently supports Linux and Android release targets')
    if not shutil.which('readelf'):
        raise ValueError('readelf is required to collect private Python native libraries')
    runtime = stage / 'python'
    version = f'python{sys.version_info.major}.{sys.version_info.minor}'
    stdlib = Path(sysconfig.get_path('stdlib'))
    library = Path(sysconfig.get_config_var('LIBDIR')) / sysconfig.get_config_var('LDLIBRARY')
    if not library.is_file() or '.so' not in library.name:
        raise ValueError('Release building requires a shared CPython library')
    target = runtime / 'lib' / version
    target.mkdir(parents=True)
    for directory, children, names in os.walk(stdlib):
        children[:] = [name for name in children if name not in EXCLUDED and not name.startswith('config-')]
        relative = Path(directory).relative_to(stdlib)
        for name in names:
            source = Path(directory) / name
            if source.suffix in ('.pyc', '.pyo', '.a') or name in (
                    'sitecustomize.py', 'usercustomize.py', 'EXTERNALLY-MANAGED'):
                continue
            output = target / relative / name
            output.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, output)  # Dereference trusted host symlinks.
    (target / 'site-packages').mkdir(exist_ok=True)
    include = Path(sysconfig.get_path('include'))
    shutil.copytree(include, runtime / 'include' / version, symlinks=False)
    shutil.copy2(library, runtime / 'lib' / library.name)
    extensions = list((target / 'lib-dynload').glob('*.so'))
    # Resolve dependencies from the original module location (for $ORIGIN paths).
    seeds = [library, *[stdlib / p.relative_to(target) for p in extensions]]
    dependencies = copy_libraries(seeds, runtime / 'lib')
    dependencies += package_libraries(stage, runtime / 'lib')
    licenses(runtime, [library.resolve(), *dependencies])
    if not (target / 'LICENSE.txt').is_file():
        raise ValueError('The host Python distribution must provide stdlib/LICENSE.txt')

    # sysconfig's build variables otherwise retain the build host's prefix.
    for config in target.glob('_sysconfigdata*.py'):
        with config.open('a') as stream:
            stream.write('\n# Paths for the relocatable OSE interpreter.\nimport sys as _sys, os as _os\n'
                'for _key, _value in {"prefix": _sys.base_prefix, "exec_prefix": _sys.base_prefix, '
                '"LIBDIR": _os.path.join(_sys.base_prefix, "lib"), '
                '"LIBPL": _os.path.join(_sys.base_prefix, "lib"), '
                '"INCLUDEPY": _os.path.join(_sys.base_prefix, "include", "' + version + '"), '
                '"BINDIR": _os.path.join(_sys.base_prefix, "bin"), "platlibdir": "lib"}.items():\n'
                '    build_time_vars[_key] = _value\n')
    binary = runtime / 'bin' / 'python'
    binary.parent.mkdir()
    compiler = shlex.split(os.environ.get('CC', 'cc'))
    subprocess.run([*compiler, '-std=c11', '-O2', '-Wall', '-Wextra', '-Werror',
                    '-I' + str(include), '-DOSE_PYTHON_LIBRARY=' + json.dumps(library.name),
                    str(HERE / 'python-launcher.c'), '-ldl', '-o', str(binary)], check=True)
    for alias in ('python3', version):
        shutil.copy2(binary, binary.with_name(alias))
    metadata = {'version': sys.version.split()[0], 'implementation': 'cpython',
                'libraries': sorted(p.name for p in (runtime / 'lib').iterdir() if p.is_file())}
    (runtime / 'runtime.json').write_text(json.dumps(metadata, indent=2) + '\n')
    # Fail creation rather than ship an interpreter that falls back to host files.
    probe = ('import sys,sysconfig,ssl,sqlite3,ctypes,bz2,lzma,zlib,multiprocessing,subprocess,pathlib; '
             'root=pathlib.Path(sys.executable).resolve().parents[1]; '
             'assert pathlib.Path(sys.prefix)==root,sys.path; '
             'assert pathlib.Path(sysconfig.get_config_var("LIBDIR"))==root/"lib"; '
             'assert all(not p or pathlib.Path(p).is_relative_to(root) for p in sys.path),sys.path; '
             'print("Private Python ready:",sys.version.split()[0])')
    subprocess.run([str(binary), '-I', '-c', probe], check=True)
    return metadata
