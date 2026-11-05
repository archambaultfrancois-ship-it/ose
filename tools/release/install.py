#!/usr/bin/env python3
"""Install an extracted OSE framework release without contacting the network."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import platform
import shlex
import shutil
import subprocess
import sys
import sysconfig
import tempfile


LANGUAGES = ('python', 'c', 'rust', 'java', 'ada')
FEATURES = ('web', 'recording', 'filter', 'sync')


def selection(value, allowed):
    selected = set(allowed) if value == 'all' else set() if value == 'none' else set(value.split(','))
    if selected - set(allowed):
        raise argparse.ArgumentTypeError('Choose all, none or a comma-separated list of: ' + ','.join(allowed))
    return sorted(selected)


def digest(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest() if hasattr(hashlib, 'file_digest') else hashlib.sha256(stream.read()).hexdigest()


def verify(source):
    manifest = json.loads((source / 'manifest.json').read_text())
    if manifest.get('format') != 1:
        raise ValueError('Unsupported release manifest')
    for name, expected in manifest['files'].items():
        relative = Path(name)
        if relative.is_absolute() or '..' in relative.parts or '\\' in name:
            raise ValueError('Unsafe release path: ' + name)
        path = source / relative
        if any(p.is_symlink() for p in (path, *path.parents) if p != source and source in p.parents):
            raise ValueError('Release resources cannot be symlinks: ' + name)
        if not path.is_file() or digest(path) != expected:
            raise ValueError('Missing or corrupt release resource: ' + name)
    return manifest


def included(name, profile, languages, features):
    parts = Path(name).parts
    if parts[0] == 'packages':
        return True  # Keep the offline repository for later repairs and SDK builds.
    if parts[0] == 'cargo-vendor':
        return profile == 'sdk' and bool({'recording', 'filter'} & set(features))
    if parts[0] != 'framework':
        return False
    path = '/'.join(parts[1:])
    if path.startswith('ose-ws/'):
        return 'web' in features
    if path.startswith('ose/sdk/'):
        if profile != 'sdk':
            return False
        # sdk_root currently uses these two files to recognize an SDK installation.
        if path in ('ose/sdk/c/ose_callbacks.c', 'ose/sdk/rust/Cargo.toml'):
            return True
        sdk = path.removeprefix('ose/sdk/').split('/')
        language = sdk[1] if sdk[0] == 'client' else sdk[0]
        required = set(languages)
        if required & {'c', 'rust', 'java', 'ada'}:
            # C/JNI/Ada external clients use the shared Rust/C client backend.
            required.update(('c', 'rust'))
        return language in required
    if path.startswith('sdl/sdlc/'):
        return profile == 'sdk'
    for language in ('java', 'ada'):
        if path.startswith('sdl/runtime/' + language + '/'):
            return language in languages
    if path.startswith(('sdl/runtime/filter/', 'sdl/tools/filter/', 'sdl/build/filter/')):
        if 'filter' not in features:
            return False
        if profile == 'runtime':
            return path.startswith(('sdl/runtime/filter/python/sdl_filter.py', 'sdl/build/filter/'))
    if path.startswith(('sdl/runtime/recording/', 'sdl/tools/recording/')):
        return 'recording' in features and (profile == 'sdk' or path.startswith('sdl/tools/recording/'))
    if path.startswith(('sdl/runtime/python/sdl_recording', 'sdl/runtime/python/sdl_pcapng')):
        return 'recording' in features
    return True


def command(variable, default):
    if variable in ('JAVA', 'JAVAC') and variable not in os.environ and os.environ.get('JAVA_HOME'):
        return [str(Path(os.environ['JAVA_HOME']) / 'bin' / default)]
    try:
        return shlex.split(os.environ.get(variable, default))
    except ValueError:
        return []


def prerequisites(profile, languages, features):
    warnings = []
    if sys.version_info < (3, 10):
        warnings.append('Python 3.10+ is needed by the web console; the current interpreter is ' + platform.python_version())
    tools = []
    if profile == 'sdk':
        tools.append(('MAKE', 'make'))
        if {'c', 'ada'} & set(languages):
            tools.append(('CC', 'cc'))
        if {'rust', 'c', 'ada'} & set(languages) or {'filter', 'recording'} & set(features):
            tools.append(('CARGO', 'cargo'))
        if 'rust' in languages:
            tools.append(('RUSTC', 'rustc'))
        if 'java' in languages:
            tools.append(('JAVAC', 'javac'))
        if 'ada' in languages:
            tools.append(('GNATMAKE', 'gnatmake'))
    if 'java' in languages:
        tools.append(('JAVA', 'java'))
    for variable, default in tools:
        cmd = command(variable, default)
        if not cmd or not shutil.which(cmd[0]):
            warnings.append(f'{variable}: {default} is unavailable; the corresponding builds or executions will require it')
        else:
            try:
                result = subprocess.run(cmd + ['-version' if variable == 'JAVA' else '--version'],
                                        capture_output=True, timeout=10)
                # GNATMAKE/JAVAC use their own version spelling.
                if variable == 'JAVAC':
                    result = subprocess.run(cmd + ['-version'], capture_output=True, timeout=10)
                if result.returncode:
                    warnings.append(f'{variable}: the tool is present but could not report its version')
            except (OSError, subprocess.TimeoutExpired):
                warnings.append(f'{variable}: the tool could not be executed')
    if profile == 'sdk' and 'java' in languages and command('JAVAC', 'javac') and shutil.which(command('JAVAC', 'javac')[0]):
        try:
            with tempfile.TemporaryDirectory(prefix='ose-javac-') as temporary:
                source = Path(temporary) / 'Probe.java'
                source.write_text('class Probe {}\n')
                version = subprocess.check_output(command('JAVAC', 'javac') + ['-version'], stderr=subprocess.STDOUT, text=True, timeout=10)
                flags = ['-source', '7', '-target', '7'] if 'javac 1.7.' in version else ['--release', '7']
                if os.environ.get('OSE_JAVA_LOCAL_RELEASE'):
                    flags = ['--release', os.environ['OSE_JAVA_LOCAL_RELEASE']]
                result = subprocess.run(command('JAVAC', 'javac') + flags + ['-d', temporary, str(source)],
                                        capture_output=True, text=True, timeout=20)
                if result.returncode:
                    warnings.append('JAVAC cannot compile with ' + shlex.join(flags) + '; configure a Java 7-compatible JDK')
        except (OSError, subprocess.SubprocessError):
            warnings.append('JAVAC compatibility could not be checked')
    if 'sync' in features and sys.platform not in ('linux', 'android'):
        warnings.append('Parameter directory synchronization requires inotify (Linux or Android)')
    return warnings


def run_logged(cmd, log, env=None):
    with log.open('ab') as output:
        output.write(('\n+ ' + shlex.join(map(str, cmd)) + '\n').encode())
        output.flush()
        return subprocess.run(list(map(str, cmd)), stdout=output, stderr=subprocess.STDOUT, env=env).returncode


def environment(prefix, packages, requirements, warnings):
    python = prefix / '.venv' / 'bin' / 'python'
    log = prefix / 'installation.log'
    try:
        import venv
        venv.EnvBuilder(with_pip=False, clear=True).create(prefix / '.venv')
    except (ImportError, OSError, subprocess.SubprocessError) as error:
        warnings.append('The isolated Python environment could not be created: ' + str(error))
        return None, False
    wheels = sorted(packages.glob('pip-*.whl'))
    if not wheels:
        warnings.append('No pip wheel is available; Python dependencies remain uninstalled')
        return python, False
    bootstrap = 'import sys; sys.path.insert(0, sys.argv.pop(1)); from pip._internal.cli.main import main; raise SystemExit(main())'
    cmd = [python, '-c', bootstrap, wheels[-1], '--isolated', 'install', '--no-index', '--no-compile',
           '--disable-pip-version-check', '--find-links', packages, *requirements]
    try:
        ok = run_logged(cmd, log) == 0
    except OSError:
        ok = False
    if not ok:
        warnings.append('Some Python dependencies could not be installed offline; see ' + str(log))
    return python, ok


def write_commands(prefix, bin_dir, python, features, warnings):
    own_bin = prefix / 'bin'
    own_bin.mkdir(exist_ok=True)
    commands = {'ose': 'ose'}
    if (prefix / 'framework/sdl/sdlc/generator.py').is_file():
        commands['sdlc'] = 'sdlc'
    if 'web' in features:
        commands['ose-ws'] = 'web'
    if 'recording' in features:
        commands['sdl-recording'] = 'recording'
    # Use a real shell on the target; /bin/sh does not exist on every Android host.
    shell = shutil.which('sh') or '/bin/sh'
    for name in ('ose', 'ose-ws', 'sdlc', 'sdl-recording'):
        old = own_bin / name
        if name not in commands and (old.exists() or old.is_symlink()):
            old.unlink()
        external = bin_dir / name
        if name not in commands and external.is_symlink() and os.readlink(external) == str(own_bin / name):
            external.unlink()
    for name, action in commands.items():
        script = own_bin / name
        interpreter = shlex.quote(str(python or Path(sys.executable).resolve()))
        script.write_text('#!' + shell + '\nexec ' + interpreter + ' ' +
                          shlex.quote(str(prefix / 'launch.py')) + ' ' + action + ' "$@"\n')
        script.chmod(0o755)
    if bin_dir == own_bin:
        return
    try:
        bin_dir.mkdir(parents=True, exist_ok=True)
        for name in commands:
            link = bin_dir / name
            target = own_bin / name
            if link.is_symlink() and link.resolve() == target:
                continue
            if link.exists() or link.is_symlink():
                warnings.append('Existing command kept: ' + str(link) + '; use ' + str(target))
                continue
            link.symlink_to(target)
    except OSError as error:
        warnings.append('Command links could not be created: ' + str(error) + '; commands remain in ' + str(own_bin))


def install(args, source):
    manifest = verify(source)
    prefix = Path(os.path.abspath(Path(args.prefix).expanduser()))
    bin_dir = Path(os.path.abspath(Path(args.bin_dir).expanduser()))
    canonical = prefix.resolve()
    if source == canonical or source in canonical.parents or canonical in source.parents:
        raise ValueError('The installation destination must be separate from the extracted release')
    if prefix.is_symlink():
        raise ValueError('The installation destination cannot be a symlink')
    if prefix.exists() and any(prefix.iterdir()) and not (prefix / 'install-report.json').is_file():
        raise ValueError('Destination is not an OSE installation or an empty directory: ' + str(prefix))
    warnings = prerequisites(args.profile, args.languages, args.features)
    target = manifest['target']
    compatible = (target['platform'] == sysconfig.get_platform() and
                  target['python'] == f'{sys.version_info.major}.{sys.version_info.minor}')
    if not compatible:
        warnings.append('This release targets ' + target['platform'] + ' / Python ' + target['python'] +
                        '; native modules and wheels may be unavailable on this host')
    names = [name for name in manifest['files'] if included(name, args.profile, args.languages, args.features)]
    print('Destination: ' + str(prefix))
    print('Profile: ' + args.profile + '; languages: ' + ','.join(args.languages) + '; features: ' + ','.join(args.features))
    for warning in warnings:
        print('WARNING: ' + warning, file=sys.stderr)
    if args.check_only or args.dry_run:
        print(f'{len(names)} resources selected. No installation changes made.')
        return 0
    prefix.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='.ose-install-', dir=prefix.parent) as temporary:
        staged = Path(temporary)
        for name in names:
            # Never copy an unlisted file or follow an unverified directory tree.
            target_path = staged / name
            target_path.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source / name, target_path)
        shutil.copy2(source / 'launch.py', staged / 'launch.py')
        prefix.mkdir(exist_ok=True)
        for name in ('framework', 'packages', 'cargo-vendor', 'launch.py'):
            old = prefix / name
            if old.exists() or old.is_symlink():
                if old.is_dir() and not old.is_symlink():
                    shutil.rmtree(old)
                else:
                    old.unlink()
            if (staged / name).exists():
                shutil.move(str(staged / name), old)
    requirements = list(manifest['requirements']['base'])
    if args.profile == 'sdk':
        requirements += manifest['requirements'].get('sdk', [])
    for feature in args.features:
        requirements += manifest['requirements'].get(feature, [])
    before = len(warnings)
    packages = Path(args.packages_dir).expanduser().resolve() if args.packages_dir else prefix / 'packages'
    python, dependencies = environment(prefix, packages, requirements, warnings)
    # Source imports are also available to child component interpreters.
    if python:
        result = subprocess.run([str(python), '-c', 'import sysconfig; print(sysconfig.get_path("purelib"))'],
                                capture_output=True, text=True)
        if result.returncode == 0:
            paths = [prefix / 'framework/ose/src', prefix / 'framework/sdl/runtime/python']
            if 'web' in args.features:
                paths.append(prefix / 'framework/ose-ws/src')
            if 'filter' in args.features:
                paths += [prefix / 'framework/sdl/runtime/filter/python', prefix / 'framework/sdl/build/filter/release']
            (Path(result.stdout.strip()) / 'ose-framework.pth').write_text(''.join(str(p) + '\n' for p in paths))
    capabilities = {}
    imports = {'engine': ['ose', 'sdl_runtime'], 'web': ['ose_ws', 'fastapi', 'uvicorn', 'websockets'],
               'recording': ['sdl_recording'], 'filter': ['sdl_filter'], 'sync': ['inotify_simple']}
    for feature in ['engine', *args.features]:
        if python:
            probe = subprocess.run([str(python), '-c', ';'.join('import ' + name for name in imports[feature])],
                                   capture_output=True, text=True)
            ready = probe.returncode == 0 and (feature != 'sync' or sys.platform in ('linux', 'android'))
        else:
            ready = False
        capabilities[feature] = ready
        if not ready:
            warnings.append(feature + ' is not ready; supply the missing prerequisite and rerun the installer')
    write_commands(prefix, bin_dir, python, args.features, warnings)
    if (prefix / 'cargo-vendor').is_dir():
        cargo = prefix / 'cargo-home'
        cargo.mkdir(exist_ok=True)
        (cargo / 'config.toml').write_text('[source.crates-io]\nreplace-with="ose-vendored"\n'
            '[source.ose-vendored]\ndirectory=' + json.dumps(str(prefix / 'cargo-vendor')) + '\n[net]\noffline=true\n')
    report = {'format': 1, 'release': manifest['revision'], 'profile': args.profile,
              'languages': args.languages, 'features': args.features, 'python_dependencies': dependencies,
              'capabilities': capabilities, 'warnings': warnings, 'files': names}
    (prefix / 'install-report.json').write_text(json.dumps(report, indent=2) + '\n')
    shutil.copy2(source / 'manifest.json', prefix / 'release-manifest.json')
    for warning in warnings[before:]:
        print('WARNING: ' + warning, file=sys.stderr)
    print(f'Installed {len(names)} resources; {len(warnings)} warning(s). Commands: {prefix / "bin"}')
    return 0


def main(argv=None, source=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', nargs='?', choices=('install',), default='install')
    parser.add_argument('--prefix', default=str(Path.home() / '.local/opt/ose'))
    parser.add_argument('--bin-dir', default=str(Path.home() / '.local/bin'))
    parser.add_argument('--profile', choices=('sdk', 'runtime'), default='sdk')
    parser.add_argument('--languages', type=lambda v: selection(v, LANGUAGES), default='all')
    parser.add_argument('--features', type=lambda v: selection(v, FEATURES), default='all')
    parser.add_argument('--packages-dir', help='additional local wheel repository instead of the bundled packages')
    parser.add_argument('--offline', action='store_true', help='accepted explicitly; installation always stays offline')
    parser.add_argument('--check-only', action='store_true')
    parser.add_argument('--dry-run', action='store_true')
    args = parser.parse_args(argv)
    try:
        return install(args, Path(source).resolve() if source else Path(__file__).resolve().parent)
    except (OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError) as error:
        print('Installation failed: ' + str(error), file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
