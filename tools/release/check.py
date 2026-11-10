#!/usr/bin/env python3
"""Install a release, compile independent apps and exercise live web captures."""
import argparse
from contextlib import ExitStack
from http.cookiejar import CookieJar
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import tempfile
import time
from urllib.error import URLError
from urllib.request import build_opener, HTTPCookieProcessor, Request
import uuid


PYTHON = '''from pathlib import Path
from measurement import Measurement
def on_clock(ctx, input):
    Path('python.txt').write_text(str(Path.cwd()))
    ctx.publish_out(Measurement(value=ctx.activation_index), id=0)
'''
C = '''#include "component.h"
#include <stdio.h>
#include <unistd.h>
static int clock_input(OseContext *ctx, const Input_clock *input) {
    (void)ctx;
    (void)input;
    char path[4096];
    if (!getcwd(path, sizeof(path))) return 0;
    FILE *file = fopen("c.txt", "w");
    if (!file) return 0;
    fputs(path, file);
    return fclose(file) == 0;
}
const ComponentCallbacks component_callbacks = { .on_clock=clock_input };
'''
RUST = '''use crate::container::{Callbacks, Context, InputReference, Result};
#[derive(Default)] pub struct Handler;
impl Callbacks for Handler {
    fn on_clock(&mut self, _ctx: &mut Context, _input: &InputReference<()>) -> Result {
        std::fs::write("rust.txt", std::env::current_dir()?.to_string_lossy().as_bytes())?;
        Ok(())
    }
}
'''


def application(root, languages):
    root.mkdir()
    (root / 'measurement.sdl').write_text('message Measurement {1: required uint64 value;}\n')
    nodes, deployment = {}, {}
    for language, suffix, source in (('python', 'py', PYTHON), ('c', 'c', C), ('rust', 'rs', RUST)):
        if language not in languages:
            continue
        (root / ('handler.' + suffix)).write_text(source)
        nodes[language] = {
            'inputs': {'clock': {'kind': 'timer', 'timer': {'mode': 'periodic', 'delay': .01},
                                 'activates': {'timeout': ['out'] if language == 'python' else []}}},
            'outputs': {'out': {'root': 'Measurement'}} if language == 'python' else {}}
        deployment[language] = {'container': {'language': language, 'handler': 'handler.' + suffix}}
    (root / 'model.json').write_text(json.dumps({'version': 2, 'schemas': 'measurement.sdl', 'components': nodes, 'subscriptions': []}))
    (root / 'deployment.json').write_text(json.dumps({'version': 1, 'components': deployment}))
    (root / 'Makefile').write_text('.PHONY: build\nbuild:\n\t"$(PYTHON)" -m ose.cli build --model model.json --deployment deployment.json --build-dir build\n')


def wait_until(predicate, processes=(), timeout=30):
    deadline = time.monotonic() + timeout
    while not predicate():
        if any(p.poll() is not None for p in processes) or time.monotonic() > deadline:
            raise AssertionError('Process stopped or readiness timed out')
        time.sleep(.05)


def editor_check(commands, root, env):
    """Author an external source project through the installed offline editor."""
    project = root / 'editor source project'
    application(project, ('python',))
    with socket.socket() as listener:
        listener.bind(('127.0.0.1', 0))
        port = listener.getsockname()[1]
    output = tempfile.TemporaryFile()
    server = subprocess.Popen([str(commands / 'ose-editor'), '--root', str(project), '--port', str(port)],
                              cwd=root, env=env, stdout=output, stderr=subprocess.STDOUT)
    try:
        token_path = project / '.ose-editor/access.token'
        wait_until(token_path.is_file, (server,))
        token = token_path.read_text().strip()
        opener = build_opener()
        def request(path, body=None):
            data = json.dumps(body).encode() if body is not None else None
            message = Request(f'http://127.0.0.1:{port}/api/v1/{path}', data=data,
                              headers={'Authorization': 'Bearer ' + token, 'Content-Type': 'application/json'})
            with opener.open(message, timeout=10) as response:
                return json.load(response)
        def ready():
            try:
                return bool(request('project'))
            except (URLError, OSError):
                return False
        wait_until(ready, (server,))
        state = request('project')
        prepared = request('commands/prepare', {'revision': state['revision'], 'command': {
            'action': 'rename', 'kind': 'component', 'old': 'python', 'name': 'edited_python'}})
        state = request('commands/apply', {'revision': state['revision'], 'id': prepared['id']})
        job = request('validation', {'revision': state['revision']})
        finished = {}
        def validated():
            finished.update(request('validation/' + job['id']))
            return finished['status'] not in ('queued', 'running')
        wait_until(validated, (server,), timeout=90)
        assert finished['status'] == 'passed', finished.get('diagnostics')
        state = request('save', {'revision': state['revision']})
        assert 'edited_python' in json.loads((project / 'model.json').read_text())['components']
        assert not (project / 'build').exists(), 'Editor validation wrote application build outputs'
        with opener.open(f'http://127.0.0.1:{port}/', timeout=10) as response:
            index = response.read().decode()
        assert 'https://' not in index
        for asset in __import__('re').findall(r'(?:src|href)="(/assets/[^\"]+)"', index):
            with opener.open(f'http://127.0.0.1:{port}{asset}', timeout=10) as response:
                assert response.read(), asset
        print('Installed editor authoring passed: external source rename, unsaved validation, save and local frontend.', flush=True)
    finally:
        server.terminate()
        server.wait(timeout=20)
        output.seek(0)
        text = output.read().decode()
        output.close()
        assert server.returncode == 0, text


def check(bundle):
    with tempfile.TemporaryDirectory(prefix="ose independent release ") as temporary, ExitStack() as stack:
        root = Path(temporary)
        prefix, commands = root / 'installed files', root / 'commands'
        env = {key: value for key, value in os.environ.items()
               if key not in ('PYTHONPATH', 'PYTHONHOME', 'SDL_ROOT', 'FRAMEWORK_ROOT', 'OSE_APP_ROOT', 'OSE_INSTALL_PYTHON') and not key.startswith('OSE_WS_')}
        # Installation must not depend on an index, even if pip is configured globally.
        env['PIP_INDEX_URL'] = 'http://127.0.0.1:1/unavailable'
        utilities = root / 'shell utilities without Python'
        utilities.mkdir()
        for name in ('sh', 'mktemp', 'tail', 'tar', 'gzip', 'rm', 'dirname', 'pwd', 'sha256sum'):
            executable = shutil.which(name)
            if executable:
                (utilities / name).symlink_to(executable)
        forbidden = utilities / 'python3'
        sentinel = root / 'system-python-was-used'
        forbidden.write_text('#!' + shutil.which('sh') + '\n: > "$HOST_PYTHON_SENTINEL"\nexit 97\n')
        forbidden.chmod(0o755)
        bootstrap_env = dict(env, PATH=str(utilities), HOST_PYTHON_SENTINEL=str(sentinel),
                             OSE_INSTALL_PYTHON=str(forbidden), PYTHONHOME='/unavailable/python',
                             PYTHONPATH='/unavailable/python-modules')
        bootstrap_env.pop('LD_LIBRARY_PATH', None)
        subprocess.run([shutil.which('sh'), bundle, '--prefix', prefix, '--bin-dir', commands,
                        '--offline'], env=bootstrap_env, cwd=root, check=True, timeout=180)
        report = json.loads((prefix / 'install-report.json').read_text())
        assert report['python_dependencies'], (prefix / 'installation.log').read_text()
        assert report['python_runtime']['bundled'], report
        assert not sentinel.exists(), 'Installation used system Python'

        def run(name, *args, cwd=None, timeout=120):
            result = subprocess.run([str(commands / name), *map(str, args)], cwd=cwd or root,
                                    env=env, capture_output=True, text=True, timeout=timeout)
            assert result.returncode == 0, result.stdout + result.stderr
            return result

        python = prefix / '.venv/bin/python'
        probe = '''import sys, pathlib, ssl, sqlite3, fastapi, mcap, lz4.frame, sdl_filter, inotify_simple, yaml
root = pathlib.Path(sys.executable).parents[1]
assert pathlib.Path(sys.prefix) == root
assert all(pathlib.Path(p).is_relative_to(root.parent) for p in sys.path), sys.path
for row in pathlib.Path('/proc/self/maps').read_text().splitlines():
    path = row.split(maxsplit=5)[-1]
    if any(name in path for name in ('libpython', 'libssl.so', 'libcrypto.so', 'libsqlite3.so', 'liblz4.so', '/lib-dynload/')):
        assert pathlib.Path(path).is_relative_to(root), path
'''
        subprocess.run([python, '-I', '-c', probe], env=bootstrap_env, cwd=root, check=True)
        one, two, runtime = root / 'first app', root / 'another app', root / 'runtime outputs'
        runtime.mkdir()
        application(one, ('python', 'c', 'rust'))
        application(two, ('python',))
        editor_check(commands, root, env)
        run('ose', 'app', '--root', one, 'build')
        plan = one / 'build/plan.json'
        data = json.loads(plan.read_text())
        assert data['sdl_root'] == str(prefix / 'framework/sdl')
        assert all(str(prefix) in node['command'][0] or str(one) in ' '.join(node['command'])
                   for node in data['components'].values())
        run('ose', 'run', '--plan', plan, '--working-directory', runtime, '--mode', 'simulated', '--duration', '.1')
        for language in ('python', 'c', 'rust'):
            assert (runtime / (language + '.txt')).read_text() == str(runtime)
        run('ose', 'app', '--root', two, 'run', '--duration', '.1', cwd=runtime)
        assert (two / 'build/last-status.json').is_file()

        # Ensure third-party Rust dependencies resolve using only the bundled vendor tree.
        cargo_env = dict(env, CARGO_HOME=str(prefix / 'cargo-home'))
        cargo_env.setdefault('RUSTUP_HOME', str(Path.home() / '.rustup'))
        subprocess.run(['cargo', 'metadata', '--offline', '--locked', '--format-version', '1', '--manifest-path',
                        prefix / 'framework/sdl/runtime/recording/Cargo.toml'], env=cargo_env,
                       check=True, stdout=subprocess.DEVNULL, timeout=30)
        cargo_env['CARGO_TARGET_DIR'] = str(root / 'sdk tests')
        for crate in ('rust', 'client/rust'):
            subprocess.run(['cargo', 'test', '--offline', '--manifest-path',
                            prefix / 'framework/ose/sdk' / crate / 'Cargo.toml'],
                           env=cargo_env, cwd=root, check=True, timeout=120)

        log = stack.enter_context((root / 'live.log').open('w+'))

        def start(name, *args):
            process = subprocess.Popen([str(commands / name), *map(str, args)], cwd=runtime,
                                       env=env, stdout=log, stderr=subprocess.STDOUT)
            def stop():
                if process.poll() is None:
                    process.terminate()
                try:
                    process.wait(timeout=15)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()
                if process.returncode:
                    log.flush()
                    log.seek(0)
                    raise AssertionError(log.read())
            stack.callback(stop)
            return process

        broker = start('ose', 'run', '--plan', plan, '--working-directory', runtime)
        endpoint = one / 'build/endpoint.json'
        wait_until(endpoint.is_file, [broker])
        config = one / 'web.json'
        run('ose-ws', 'init', '--config', config)
        settings = json.loads(config.read_text())
        settings['recording']['min_free_disk_mib'] = 0
        config.write_text(json.dumps(settings))
        with socket.socket() as reservation:
            reservation.bind(('127.0.0.1', 0))
            port = reservation.getsockname()[1]
        web = start('ose-ws', '--config', config, '--port', port)
        base = f'http://127.0.0.1:{port}'
        client = build_opener(HTTPCookieProcessor(CookieJar()))
        csrf = None

        def request(path, body=None):
            headers = {}
            if body is not None:
                headers = {'Content-Type': 'application/json', 'Idempotency-Key': uuid.uuid4().hex}
                if csrf:
                    headers['X-CSRF-Token'] = csrf
            with client.open(Request(base + path, data=None if body is None else json.dumps(body).encode(), headers=headers), timeout=5) as response:
                return json.load(response)

        def ready():
            try:
                return request('/health/ready')['ready']
            except (URLError, TimeoutError):
                return False

        wait_until(ready, [broker, web])
        token = (one / '.ose-ws/access.token').read_text().strip()
        csrf = request('/api/v1/auth/login', {'token': token})['csrf']
        wait_until(lambda: request('/api/v1/application')['connected'], [broker, web])
        wait_until(lambda: any(p['topic'] == 'python.out' and p['available']
                               for p in request('/api/v1/recording-points')['items']), [broker, web])

        def operation(path, body):
            result = request('/api/v1' + path, body)
            deadline = time.monotonic() + 10
            while result['status'] == 'pending':
                assert time.monotonic() < deadline, result
                time.sleep(.05)
                result = request('/api/v1/operations/' + result['id'])
            assert result['status'] == 'complete', result
            return result['result']

        capture = operation('/recordings', {'selection': ['python.out'], 'filters': {'python.out': 'value >= 0'}})['id']
        operation('/recordings/' + capture + '/start', {})
        time.sleep(.3)
        operation('/recordings/' + capture + '/stop', {})
        files = request('/api/v1/recordings/' + capture + '/files')['items']
        assert files
        captured_paths = []
        for artifact in files:
            path = runtime / '.ose-ws/captures' / capture / artifact['name']
            assert path.stat().st_size > 0
            captured_paths.append(str(path))
        capture_probe = '''import json, sys
from sdl_recording import RecordingReader
records = [record for path in json.loads(sys.argv[1]) for record in RecordingReader(path).iter_records()]
assert records, 'Capture has no messages'
assert all(record.metadata.get('ose.app_id') == '0' for record in records), 'Capture lost application ID zero'
'''
        capture_env = dict(env, PYTHONPATH=str(prefix / 'framework/sdl/runtime/python'))
        subprocess.run([python, '-c', capture_probe, json.dumps(captured_paths)],
                       env=capture_env, cwd=root, check=True, timeout=30)
        assert (runtime / '.ose-ws/state.sqlite3').is_file()
        assert not (one / '.ose-ws/state.sqlite3').exists()
    print('Release passed: offline installation without system Python, private native libraries, independent v2 C/Rust/Python applications, SDK tests, simulation, real-time execution, web login and filtered capture with application IDs.')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('bundle', type=Path)
    check(parser.parse_args().bundle.resolve())
