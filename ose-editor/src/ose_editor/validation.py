"""Bounded, cancellable validation of immutable drafts through public CLI tools."""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
import json
import os
from pathlib import Path
import re
import secrets
import signal
import subprocess
import sys
import tempfile
import threading
import time

from .json_source import JsonSource, dump, loads, web
from .diagnostics import source_diagnostic


class Validator:
    def __init__(self, project, sdl_root):
        self.project = project
        self.sdl_root = Path(sdl_root).resolve()
        if not (self.sdl_root / 'sdlc/generator.py').is_file():
            raise ValueError('SDL compiler is unavailable; select the SDK profile or provide --sdl-root')
        self.pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix='ose-editor-validation')
        self.jobs, self.processes = {}, {}
        self.guard = threading.RLock()

    def update(self, job, **values):
        with self.guard:
            job.update(values)

    def snapshot(self, identifier):
        with self.guard:
            return deepcopy(self.jobs[identifier])

    def close(self):
        for job in list(self.jobs.values()):
            self.cancel(job['id'])
        self.pool.shutdown(wait=True, cancel_futures=True)

    def start(self, revision, deployment=None, schema=None):
        with self.project.mutex:
            self.project.check_revision(revision)
            snapshot = self.project.snapshot()
            paths = {key: doc.path for key, doc in self.project.documents.items() if doc.active}
            inputs = dict(self.project.schema_inputs)
        for job in list(self.jobs.values()):
            if job['status'] in ('queued', 'running'):
                self.cancel(job['id'])
        job = {'id': secrets.token_hex(12), 'revision': revision, 'deployment': deployment or self.project.selected_deployment,
               'schema': schema, 'status': 'queued', 'diagnostics': [], 'resolved': {}, 'contracts': {}, 'connection_contracts': [], 'catalogues': {}, 'started': time.time()}
        self.jobs[job['id']] = job
        while len(self.jobs) > 20:
            oldest = next(iter(self.jobs))
            if self.jobs[oldest]['status'] in ('queued', 'running'):
                break
            self.jobs.pop(oldest)
        self.pool.submit(self.run, job, snapshot, paths, inputs)
        return self.snapshot(job['id'])

    def cancel(self, identifier):
        with self.guard:
            if identifier not in self.jobs:
                raise ValueError('Unknown validation job')
            job = self.jobs[identifier]
            if job['status'] in ('queued', 'running'):
                job['status'] = 'cancelled'
            process = self.processes.get(identifier)
            if process is not None and process.poll() is None:
                try:
                    os.killpg(process.pid, signal.SIGTERM)
                except ProcessLookupError:
                    pass
        return self.snapshot(job['id'])

    def execute(self, job, command, env):
        if job['status'] == 'cancelled':
            return None
        with self.guard:
            process = subprocess.Popen(list(map(str, command)), stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env, text=True, start_new_session=True)
            self.processes[job['id']] = process
        try:
            out, error = process.communicate(timeout=90)
        except subprocess.TimeoutExpired:
            os.killpg(process.pid, signal.SIGKILL)
            process.communicate()
            raise ValueError('Validation exceeded its 90 second time limit') from None
        finally:
            with self.guard:
                self.processes.pop(job['id'], None)
        if job['status'] == 'cancelled':
            return None
        if process.returncode:
            lines = error.strip().splitlines()
            message = next((line.split(': ', 1)[-1] for line in reversed(lines) if re.match(r'\w*(?:Error|Exception):', line)), lines[-1] if lines else out.strip())
            raise ValueError(message or 'Validation failed')
        return out

    def run(self, job, snapshot, paths, inputs):
        if job['status'] == 'cancelled':
            return
        self.update(job, status='running')
        active_file = self.project.model
        staged_files = {}
        try:
            with tempfile.TemporaryDirectory(prefix='ose-editor-validate-') as directory:
                stage = Path(directory)
                roots = {root: stage / str(i) for i, root in enumerate(self.project.roots)}
                def staged(path):
                    for root, target in roots.items():
                        if path == root or root in path.parents:
                            return target / path.relative_to(root)
                    raise ValueError('Validation dependency is outside the configured roots')
                for key, text in snapshot['documents'].items():
                    destination = staged(paths[key])
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    destination.write_bytes(text.encode())
                staged_files = {str(staged(path)): key for key, path in paths.items()}
                model_key = self.project.model
                model_path = staged(paths[model_key])
                model = JsonSource(snapshot['documents'][model_key])
                def fix_paths(node, pointer=()):
                    edits = []
                    for key, child in node.children.items():
                        if key in ('schemas', 'schema') and isinstance(child.value, str):
                            original = self.project.resolve(paths[model_key].parent / child.value)
                            edits.append(((*pointer, key), str(staged(original))))
                        edits.extend(fix_paths(child, (*pointer, key)))
                    return edits
                model_text = model.text
                for pointer, value in fix_paths(model.root):
                    model_text = JsonSource(model_text).set(pointer, json.dumps(value))
                model_path.write_text(model_text)
                env = dict(os.environ)
                # The matching installed engine is found through the launcher's
                # PYTHONPATH or this checkout's configured development environment.
                env['SDL_ROOT'] = str(self.sdl_root)
                env['PYTHONPATH'] = str(self.sdl_root / 'runtime/python') + os.pathsep + env.get('PYTHONPATH', '')
                authored_model = loads(snapshot['documents'][model_key])
                if (not isinstance(authored_model, dict) or type(authored_model.get('version')) is not int
                        or authored_model['version'] != 2):
                    raise ValueError('Application model v2 is required; migrate v1 triggers and callbacks')
                for source, members in inputs.items():
                    if job['schema'] and source != job['schema']:
                        continue
                    original = self.project.resolve(paths[model_key].parent / source)
                    output = stage / 'exports' / secrets.token_hex(4)
                    active_file = self.project.key(original)
                    self.update(job, progress='Checking schema input: ' + source)
                    result = self.execute(job, [sys.executable, self.sdl_root / 'sdlc/generator.py', '-json', '-python', staged(original), output], env)
                    if result is None:
                        return
                    resolved = [web(loads(file.read_text())) for file in sorted((output / 'json').rglob('*.sdl.json'))]
                    with self.guard:
                        job['resolved'][source] = resolved
                    request = stage / 'inspect.json'
                    request.write_text(dump({'mode': 'catalogue', 'directory': str(output / 'python'), 'parameters': {
                        path: block for path, block in authored_model.get('shared_parameters', {}).items() if block.get('schema', authored_model.get('schemas')) == source}}))
                    inspected = self.execute(job, [sys.executable, '-m', 'ose_editor.runtime_inspect', request], env)
                    if inspected is None:
                        return
                    with self.guard:
                        job['catalogues'][source] = json.loads(inspected)
                active_file = model_key
                if not job['schema']:
                    # Compare distinct contract pairs, including ports which are
                    # not connected yet. Reuse the public directional SDL check
                    # rather than guessing compatibility from logical names.
                    publishers = list(authored_model.get('external_topics', {}).values())
                    consumers = []
                    for component in authored_model.get('components', {}).values():
                        publishers.extend(component.get('outputs', {}).values())
                        consumers.extend(desc for desc in component.get('inputs', {}).values()
                                         if desc.get('kind') != 'timer')
                    pairs = sorted({(publisher.get('schema', authored_model['schemas']),
                                     consumer.get('schema', authored_model['schemas']), publisher['root'])
                                    for publisher in publishers for consumer in consumers
                                    if publisher['root'] == consumer['root']})
                    candidates = [{'id': str(i), 'root': root,
                                   'publisher': job['catalogues'][publisher]['catalogue'],
                                   'consumer': job['catalogues'][consumer]['catalogue']}
                                  for i, (publisher, consumer, root) in enumerate(pairs)]
                    request = stage / 'connection-contracts.json'
                    request.write_text(json.dumps({'mode': 'compare', 'subscriptions': candidates}))
                    compared = self.execute(job, [sys.executable, '-m', 'ose_editor.runtime_inspect', request], env)
                    if compared is None:
                        return
                    reports = json.loads(compared)
                    self.update(job, connection_contracts=[
                        {**reports[str(i)], 'publisher_schema': publisher, 'consumer_schema': consumer}
                        for i, (publisher, consumer, root) in enumerate(pairs)])
                    pair_reports = {pair: reports[str(i)] for i, pair in enumerate(pairs)}
                    contracts = {}
                    for sub in authored_model.get('subscriptions', []):
                        target, port = sub['to'].split('.')
                        consumer = authored_model['components'][target]['inputs'][port]
                        if 'from' in sub:
                            source_component, output_port = sub['from'].split('.')
                            publisher = authored_model['components'][source_component]['outputs'][output_port]
                        else:
                            publisher = authored_model['external_topics'][sub['from_topic']]
                        if publisher['root'] == consumer['root']:
                            pair = (publisher.get('schema', authored_model['schemas']),
                                    consumer.get('schema', authored_model['schemas']), publisher['root'])
                            contracts[sub['id']] = pair_reports[pair]
                    self.update(job, contracts=contracts)
                if not job['schema']:
                    if not job['deployment']:
                        raise ValueError('Select a deployment to validate the complete application')
                    deployment_key = job['deployment']
                    if deployment_key not in snapshot['deployments']:
                        raise ValueError('Unknown deployment')
                    data = JsonSource(snapshot['documents'][deployment_key])
                    text = data.text
                    for name, component in data.root.value.get('components', {}).items():
                        handler = component.get('container', {}).get('handler')
                        if handler and Path(handler).is_absolute():
                            # Validation never reads or executes handlers. Keep their
                            # basename/extension and root containment checks intact.
                            mapped = staged(self.project.resolve(handler))
                            text = JsonSource(text).set(['components', name, 'container', 'handler'], json.dumps(str(mapped)))
                    deployment_path = staged(paths[deployment_key])
                    deployment_path.write_text(text)
                    self.update(job, progress='Checking OSE model and deployment')
                    if self.execute(job, [sys.executable, '-m', 'ose.cli', 'validate', '--model', model_path, '--deployment', deployment_path, '--sdl-root', self.sdl_root], env) is None:
                        return
                # Publish success together with the contract cache: a client
                # seeing 'passed' may immediately prepare a connection.
                with self.project.mutex, self.guard:
                    if job['status'] != 'cancelled':
                        job['status'] = 'passed'
                        if job['revision'] == self.project.revision:
                            self.project.last_validation = deepcopy(job)
        except (ValueError, OSError, KeyError, TypeError) as error:
            if job['status'] != 'cancelled':
                message = str(error)
                for staged_name, key in sorted(staged_files.items(), key=lambda item: -len(item[0])):
                    if staged_name in message:
                        active_file = key
                        message = message.replace(staged_name, key)
                for original in self.project.roots:
                    message = message.replace(str(original), '.')
                file = job.get('deployment') if 'deployment' in message.lower() and job.get('deployment') else active_file
                diagnostic = source_diagnostic(ValueError(message), file, snapshot['documents'].get(file, ''), 'SDL/OSE', job['revision'])
                self.update(job, status='failed', diagnostics=[diagnostic])
        finally:
            self.update(job, duration_ms=round((time.time() - job['started']) * 1000), stale=job['revision'] != self.project.revision)
            with self.project.mutex:
                if not job['stale'] and job['status'] != 'cancelled':
                    self.project.last_validation = self.snapshot(job['id'])
