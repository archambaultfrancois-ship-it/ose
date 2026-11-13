"""Revisioned source project, shared commands, external-change checks and save journal."""
import base64
from copy import deepcopy
from dataclasses import dataclass
from decimal import Decimal
import difflib
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import stat
import tempfile
import threading

from .json_source import JsonSource, dump, loads, web
from .sdl_source import SdlSource
from .diagnostics import source_diagnostic


class Conflict(ValueError):
    pass


def digest(data):
    return hashlib.sha256(data).hexdigest()


def atomic_write(path, data, mode=0o600):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix='.ose-editor-write-', dir=path.parent)
    try:
        with os.fdopen(fd, 'wb') as output:
            output.write(data)
            output.flush()
            os.fsync(output.fileno())
        os.chmod(name, mode)
        os.replace(name, path)
        directory = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        if os.path.exists(name):
            os.unlink(name)


@dataclass
class Document:
    path: Path
    text: str
    baseline: bytes | None
    role: str
    active: bool = True
    created: bool = False
    deleted: bool = False

    @property
    def dirty(self):
        return self.baseline != self.text.encode('utf-8')

    @property
    def format(self):
        name = self.path.name
        return 'sdl' if name.endswith('.sdl') else 'yaml' if name.endswith(('.yaml', '.yml')) else 'xsd' if name.endswith('.xsd') else 'json'

    def parsed(self):
        if self.format == 'json':
            return JsonSource(self.text)
        if self.format == 'sdl':
            return SdlSource(self.text)
        from .formats import DescriptorSource
        return DescriptorSource(self.text, self.format)


class Project:
    max_document_bytes = 4 * 1024 * 1024

    def __init__(self, root, model='model.json', deployments=(), source_roots=(), create=False):
        self.root = Path(root).expanduser().resolve()
        if not self.root.is_dir():
            raise ValueError('Project root must be an existing directory')
        self.roots = [self.root, *(Path(p).expanduser().resolve() for p in source_roots)]
        self.state = self.root / '.ose-editor'
        if self.state.is_symlink():
            raise ValueError('Editor state directory cannot be a symlink')
        self.state.mkdir(mode=0o700, exist_ok=True)
        self.lock = (self.state / 'writer.lock').open('a+')
        try:
            fcntl.flock(self.lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            self.lock.close()
            raise Conflict('This project already has an editor writer') from None
        self.mutex = threading.RLock()
        try:
            self._open(model, deployments, create)
        except BaseException:
            self.close()
            raise

    def _open(self, model, deployments, create):
        self.documents = {}
        self.revision = secrets.token_hex(8)
        self.history, self.redo_history, self.prepared = [], [], {}
        self.last_valid = {}
        self.last_validation = None
        self.recovery_warning = None
        self.workspace = {'version': 1, 'positions': {}, 'groups': {}, 'views': {}, 'pinned': [], 'ids': {}, 'deployments': []}
        workspace = self.state / 'workspace.json'
        if workspace.is_file():
            try:
                data = json.loads(workspace.read_text())
                if data.get('version') == 1:
                    self.workspace.update(data)
            except (ValueError, OSError):
                pass
        self.workspace_baseline = deepcopy(self.workspace)
        self.recovery = self._read_recovery()
        self.model = self.load(self.resolve(model), 'model', create=create)
        candidates = list(deployments)
        if not candidates:
            # Remembered files may have been removed by a source refactoring.
            candidates = [path for path in self.workspace.get('deployments', []) if self.resolve(path).exists()]
            if not candidates:
                candidates = [p.name for p in sorted(self.root.glob('*deployment*.json'))]
        if not candidates and create:
            candidates = ['deployment.json']
        self.deployments = []
        for path in candidates:
            self.deployments.append(self.load(self.resolve(path), 'deployment', create=create))
        self.workspace['deployments'] = self.deployments[:]
        self.selected_deployment = self.workspace.get('selected_deployment') if self.workspace.get('selected_deployment') in self.deployments else self.deployments[0] if self.deployments else None
        self.workspace['selected_deployment'] = self.selected_deployment
        self.discover()
        self.draft_recovery = self._read_draft()

    def close(self):
        if not self.lock.closed:
            fcntl.flock(self.lock, fcntl.LOCK_UN)
            self.lock.close()

    def resolve(self, path):
        candidate = Path(path)
        result = (self.root / candidate).resolve() if not candidate.is_absolute() else candidate.resolve()
        if not any(result == root or root in result.parents for root in self.roots):
            raise ValueError('Path is outside the configured source roots: ' + str(path))
        if self.state == result or self.state in result.parents:
            raise ValueError('Editor state is not an application source')
        return result

    def key(self, path):
        for index, root in enumerate(self.roots):
            if path == root or root in path.parents:
                return ('' if index == 0 else f'@{index}/') + path.relative_to(root).as_posix()
        raise ValueError('Unregistered source root')

    def load(self, path, role, create=False, text=None):
        key = self.key(path)
        if key in self.documents and not self.documents[key].active and self.documents[key].baseline is None and text is not None:
            self.documents[key].text = text
        if key not in self.documents:
            if path.exists():
                if not path.is_file() or path.stat().st_size > self.max_document_bytes:
                    raise ValueError('Source is not a regular file within the size limit: ' + key)
                data = path.read_bytes()
                self.documents[key] = Document(path, data.decode('utf-8'), data, role)
            elif create:
                initial = {'version': 2, 'schemas': 'schemas', 'components': {}, 'subscriptions': []} if role == 'model' else {'version': 1, 'components': {}} if role == 'deployment' else None
                self.documents[key] = Document(path, text if text is not None else json.dumps(initial, indent=2) + '\n' if initial else '', None, role, created=True)
            else:
                raise ValueError('Source file does not exist: ' + key)
        self.documents[key].active = True
        return key

    def discover(self):
        self.discovery_errors, self.schema_inputs = [], {}
        try:
            model = loads(self.documents[self.model].text)
            if not isinstance(model, dict):
                return
            inputs = {model.get('schemas', 'schemas')}
            descriptions = list(model.get('external_topics', {}).values()) + list(model.get('shared_parameters', {}).values())
            for component in model.get('components', {}).values():
                for direction in ('inputs', 'outputs', 'recording_types'):
                    descriptions += list(component.get(direction, {}).values())
            inputs.update(desc['schema'] for desc in descriptions if isinstance(desc, dict) and 'schema' in desc)
            for source in inputs:
                try:
                    path = self.resolve(self.documents[self.model].path.parent / source)
                    members = [path] if path.is_file() else sorted(path.rglob('*')) if path.is_dir() else []
                    keys = []
                    for member in members:
                        if member.name.endswith(('.sdl', '.sdl.json', '.sdl.yaml', '.sdl.yml', '.sdl.xsd')):
                            checked = self.resolve(member)
                            known = self.documents.get(self.key(checked))
                            if known is not None and known.deleted:
                                continue
                            keys.append(self.load(checked, 'schema'))
                    # Newly authored source files may not exist on disk yet.
                    for key, document in self.documents.items():
                        if document.active and document.role == 'schema' and (document.path == path or path in document.path.parents) and key not in keys:
                            keys.append(key)
                    self.schema_inputs[source] = keys
                    if not keys:
                        self.discovery_errors.append({'severity': 'error', 'origin': 'project', 'file': self.model, 'message': 'Missing schema input: ' + source})
                except (ValueError, OSError, TypeError) as error:
                    self.discovery_errors.append({'severity': 'error', 'origin': 'project', 'message': str(error)})
        except (ValueError, TypeError, AttributeError, KeyError):
            pass  # Syntax and malformed draft diagnostics belong to projection().

    def check_revision(self, revision):
        if revision != self.revision:
            raise Conflict('Project changed in another view; reload before applying this operation')
        if self.recovery:
            raise Conflict('An interrupted save must be recovered before editing')
        if self.draft_recovery:
            raise Conflict('Restore or discard the recovered unsaved draft before editing')

    def snapshot(self):
        return {'documents': {key: doc.text for key, doc in self.documents.items() if doc.active},
                'workspace': deepcopy(self.workspace), 'deployments': self.deployments[:], 'selected': self.selected_deployment}

    def restore(self, snapshot):
        for key, doc in self.documents.items():
            doc.active = key in snapshot['documents']
            doc.deleted = not doc.active and doc.created and doc.baseline is not None
            if doc.active:
                doc.text = snapshot['documents'][key]
        self.workspace = deepcopy(snapshot['workspace'])
        self.deployments = snapshot['deployments'][:]
        self.selected_deployment = snapshot['selected']

    def change(self):
        self.revision = secrets.token_hex(8)
        self.prepared.clear()
        self.discover()

    def prepare(self, command, revision):
        with self.mutex:
            self.check_revision(revision)
            before = self.snapshot()
            try:
                self.execute(command)
                after = self.snapshot()
                changes = []
                for key in sorted(set(before['documents']) | set(after['documents'])):
                    old, new = before['documents'].get(key, ''), after['documents'].get(key, '')
                    if old != new:
                        changes.append({'file': key, 'diff': ''.join(difflib.unified_diff(old.splitlines(True), new.splitlines(True), fromfile=key, tofile=key)),
                                        'identity_changes': self.identity_changes(key, old, new)})
                handlers = self.handlers()
                token = secrets.token_hex(16)
                self.prepared[token] = (self.revision, before, after)
                # Bound previews from abandoned tabs.
                if len(self.prepared) > 32:
                    self.prepared.pop(next(iter(self.prepared)))
                return {'id': token, 'revision': self.revision, 'changes': changes,
                        'handlers_to_review': handlers if command.get('action', '').startswith(('rename', 'sdl', 'remove', 'configure_', 'duplicate_port')) else [],
                        'external_consumers': 'Unregistered external consumers are not assessed.'}
            finally:
                self.restore(before)

    def apply(self, token, revision):
        with self.mutex:
            self.check_revision(revision)
            base, before, after = self.prepared[token]
            if base != self.revision:
                raise Conflict('The prepared operation is stale')
            self.history.append((before, after))
            self.history = self.history[-100:]
            self.redo_history.clear()
            self.restore(after)
            self.change()
            self.persist_draft()
            return self.projection()

    def undo(self, revision, redo=False):
        with self.mutex:
            self.check_revision(revision)
            source, target = (self.redo_history, self.history) if redo else (self.history, self.redo_history)
            if not source:
                raise ValueError('No operation to ' + ('redo' if redo else 'undo'))
            before, after = source.pop()
            self.restore(after if redo else before)
            target.append((before, after))
            self.change()
            self.persist_draft()
            return self.projection()

    def document(self, key):
        if key not in self.documents or not self.documents[key].active:
            raise ValueError('Unknown source document')
        self.resolve(self.documents[key].path)
        return self.documents[key]

    def reserved(self, path):
        key = self.key(path)
        return path.exists() or key in self.documents and (self.documents[key].active or self.documents[key].baseline is not None)

    def json_set(self, key, path, value=None, literal=None, remove=False):
        doc = self.document(key)
        parsed = doc.parsed()
        doc.text = parsed.delete(path) if remove else parsed.set(path, literal if literal is not None else dump(value))

    def execute(self, command):
        action = command['action']
        if action in ('configure_input', 'configure_output', 'configure_activation', 'duplicate_port', 'remove_port', 'add_component', 'remove_component', 'connect', 'duplicate_subscription', 'set_reception', 'rename'):
            self.application_model()
        if action == 'source':
            text = command['text']
            if len(text.encode()) > self.max_document_bytes:
                raise ValueError('Source exceeds the document limit')
            self.document(command['file']).text = text
        elif action in ('set', 'delete', 'rename_key'):
            doc = self.document(command['file'])
            parsed = doc.parsed()
            path = command.get('path', [])
            if action == 'rename_key':
                doc.text = parsed.rename(path, command['name'])
            else:
                doc.text = parsed.delete(path) if action == 'delete' else parsed.set(path, command['literal'])
        elif action in ('sdl_edit', 'sdl_add', 'sdl_delete', 'sdl_stabilize_ids'):
            doc = self.document(command['file'])
            parsed = doc.parsed()
            if not isinstance(parsed, SdlSource):
                raise ValueError('This command requires textual SDL')
            path = command.get('path', '')
            if action == 'sdl_stabilize_ids':
                doc.text = parsed.patches([(node['properties']['id']['start'], node['properties']['id']['end'], str(node['effective_id']) + ': ')
                                           for node in parsed.index.values() if node['kind'] == 'field' and not node['properties']['id']['value']])
            elif action == 'sdl_edit':
                doc.text = parsed.edit(path, command['property'], command['value'])
            elif action == 'sdl_add':
                doc.text = parsed.add(path, command['kind'], command['name'], command.get('value', ''))
            else:
                doc.text = parsed.delete(path)
        elif action == 'comment':
            doc = self.document(command['file'])
            parsed = SdlSource(doc.text)
            item = parsed.comments()[int(command['index'])]
            value = command['value']
            if not (value.startswith('//') and '\n' not in value or value.startswith('/*') and value.endswith('*/') and '*/' not in value[2:-2]):
                raise ValueError('Use a complete SDL comment')
            doc.text = parsed.patches([(item['start'], item['end'], value)])
        elif action == 'rename':
            self.rename(command)
        elif action in ('configure_output', 'configure_activation', 'duplicate_port', 'remove_port'):
            from ose_editor.component_commands import execute
            execute(self, command)
        elif action == 'configure_input':
            from ose.modeling.activation import component_inputs, activation_graph
            from ose_editor.component_commands import validate
            model = json.loads(self.document(self.model).text)
            name, port, description = command['component'], command['port'], command['description']
            if name not in model['components'] or not isinstance(port, str) or not port or '.' in port:
                raise ValueError('Choose an existing component and a nonempty input name without dots')
            if not isinstance(description, dict):
                raise ValueError('Input description must be an object')
            if description.get('kind') == 'timer' and any(sub['to'] == name + '.' + port for sub in model['subscriptions']):
                raise ValueError('Disconnect incoming subscriptions before converting this input to a timer')
            node = model['components'][name]
            previous = node['inputs'].get(port)
            if command.get('create') and previous is not None:
                raise ValueError('Port already exists')
            node['inputs'][port] = description
            component_inputs(node)
            activation_graph(model)
            validate(self, model)
            path = ['components', name, 'inputs', port]
            if previous is None:
                self.json_set(self.model, path, description)
            else:
                for field in previous.keys() - description.keys():
                    self.json_set(self.model, [*path, field], remove=True)
                for field, value in description.items():
                    if field not in previous or previous[field] != value:
                        self.json_set(self.model, [*path, field], value)
        elif action == 'add_component':
            name = command['name']
            model = loads(self.document(self.model).text)
            if not name or '.' in name or name in model.get('components', {}):
                raise ValueError('Use a unique component name without dots')
            source = command.get('copy')
            if source:
                value = deepcopy(model['components'][source])
            else:
                value = {'inputs': {'clock': {'kind': 'timer', 'timer': {'mode': 'periodic', 'delay': .1}}}, 'outputs': {}}
            self.json_set(self.model, ['components', name], value)
            for key in self.deployments:
                deployment = loads(self.document(key).text)
                execution = deepcopy(deployment['components'][source]) if source and source in deployment.get('components', {}) else {'container': {'language': 'python', 'handler': f'components/{name}/handler.py'}}
                self.json_set(key, ['components', name], execution)
        elif action == 'remove_component':
            name = command['name']
            self.json_set(self.model, ['components', name], remove=True)
            model = loads(self.document(self.model).text)
            removed = []
            for index in range(len(model.get('subscriptions', [])) - 1, -1, -1):
                sub = model['subscriptions'][index]
                if sub.get('from', '').startswith(name + '.') or sub['to'].startswith(name + '.'):
                    removed.append(sub['id'])
                    self.json_set(self.model, ['subscriptions', str(index)], remove=True)
            for key in self.deployments:
                data = loads(self.document(key).text)
                if name in data.get('components', {}):
                    self.json_set(key, ['components', name], remove=True)
                for sub in removed:
                    if sub in data.get('subscriptions', {}):
                        self.json_set(key, ['subscriptions', sub], remove=True)
                if name in data.get('process_names', {}).get('components', {}):
                    self.json_set(key, ['process_names', 'components', name], remove=True)
        elif action == 'duplicate_subscription':
            model = loads(self.document(self.model).text)
            subs = model.get('subscriptions', [])
            name = command['name']
            if not isinstance(name, str) or not name.strip():
                raise ValueError('Use a nonempty subscription ID')
            if any(sub['id'] == name for sub in subs):
                raise ValueError('Subscription ID already exists')
            source = next((sub for sub in subs if sub['id'] == command['copy']), None)
            if source is None:
                raise ValueError('Unknown subscription to duplicate')
            destination = command.get('to', '')
            if not isinstance(destination, str) or destination.count('.') != 1:
                raise ValueError('Use a destination input in component.port form')
            component, port = destination.split('.')
            if port not in model.get('components', {}).get(component, {}).get('inputs', {}):
                raise ValueError('Unknown destination input')
            subscription = {**deepcopy(source), 'id': name, 'to': destination}
            self._inherit_reception(subscription, subs)
            self.json_set(self.model, ['subscriptions', '-'], subscription)
            for key in self.deployments:
                deployment = loads(self.document(key).text)
                if source['id'] in deployment.get('subscriptions', {}):
                    self.json_set(key, ['subscriptions', name], deepcopy(deployment['subscriptions'][source['id']]))
            self._check_connection(subscription)
        elif action == 'connect':
            model = loads(self.document(self.model).text)
            subs = model.get('subscriptions', [])
            source_key = 'from_topic' if command.get('external') else 'from'
            subscription = {'id': command['name'], source_key: command['from'], 'to': command['to']}
            if any(sub['id'] == subscription['id'] for sub in subs):
                raise ValueError('Subscription ID already exists')
            self._inherit_reception(subscription, subs)
            self.json_set(self.model, ['subscriptions', '-'], subscription)
            self._check_connection(subscription)
        elif action == 'set_reception':
            from ose.delivery.policies import SubscriptionPolicy
            model = loads(self.document(self.model).text)
            target = command['to']
            incoming = [(i, sub) for i, sub in enumerate(model.get('subscriptions', [])) if sub['to'] == target]
            if not incoming:
                raise ValueError('Connect an input before editing its reception policy')
            settings = command['reception']
            if not isinstance(settings, dict) or set(settings) - {'buffer', 'capacity', 'overflow', 'wait_timeout_ms'}:
                raise ValueError('Reception settings are buffer, capacity, overflow and wait_timeout_ms')
            reception = SubscriptionPolicy.resolve(settings).reception()
            for index, sub in incoming:
                self.json_set(self.model, ['subscriptions', str(index), 'qos'], {**sub.get('qos', {}), **reception})
            self._check_reception(target)
        elif action == 'workspace':
            for key in ('positions', 'groups', 'views', 'pinned'):
                if key in command:
                    self.workspace[key] = command[key]
        elif action == 'select_deployment':
            if command['file'] not in self.deployments:
                raise ValueError('Unknown deployment')
            self.selected_deployment = command['file']
            self.workspace['selected_deployment'] = command['file']
        elif action == 'deployment':
            path = self.resolve(command['name'])
            if self.reserved(path):
                raise ValueError('Deployment path already exists')
            text = self.document(command['copy']).text if command.get('copy') else '{\n  "version": 1,\n  "components": {}\n}\n'
            key = self.load(path, 'deployment', create=True, text=text)
            self.deployments.append(key)
            self.workspace['deployments'] = self.deployments[:]
        elif action == 'create_document':
            path = self.resolve(command['name'])
            if self.reserved(path):
                raise ValueError('Source path already exists')
            self.load(path, 'schema', create=True, text=command.get('text', ''))
        elif action == 'export_schema':
            from .conversion import export_schema
            export_schema(self, command['file'], command['format'], command['name'])
        else:
            raise ValueError('Unknown command: ' + action)

    @staticmethod
    def _inherit_reception(subscription, subscriptions):
        from ose.delivery.policies import SubscriptionPolicy
        existing = next((sub for sub in subscriptions if sub['to'] == subscription['to']), None)
        if existing is not None:
            reception = SubscriptionPolicy.resolve(existing.get('qos')).reception()
            subscription['qos'] = {**subscription.get('qos', {}), **reception}

    def _check_reception(self, target):
        from ose.delivery.policies import SubscriptionPolicy, reception_policies
        model = loads(self.document(self.model).text)
        deployments = [loads(self.document(key).text) for key in self.deployments] or [{}]
        for deployment in deployments:
            resolved = []
            for sub in model.get('subscriptions', []):
                if sub['to'] != target:
                    continue
                transport = deployment.get('subscriptions', {}).get(sub['id'], {}).get('transport', 'tcp')
                policy = SubscriptionPolicy.resolve(sub.get('qos'), transport)
                resolved.append({'to': target, 'policy': policy.to_dict()})
            reception_policies(resolved)

    def application_model(self):
        model = loads(self.document(self.model).text)
        if not isinstance(model, dict) or type(model.get('version')) is not int or model['version'] != 2:
            raise ValueError('Application model v2 is required; migrate v1 triggers and callbacks before using structured editing')
        return model

    def _check_connection(self, subscription):
        model = self.application_model()
        target, input_name = subscription['to'].split('.')
        consumer = model['components'][target]['inputs'][input_name]
        if consumer.get('kind') == 'timer':
            raise ValueError('Timer inputs cannot receive a subscription')
        from ose.modeling.activation import activation_graph
        # Match OSE's JSON number types; the editor's Decimal values are
        # retained for source edits, but timer validation expects int/float.
        activation_graph(json.loads(self.document(self.model).text))
        if 'from' in subscription:
            source, output = subscription['from'].split('.')
            publisher = model['components'][source]['outputs'][output]
        else:
            publisher = model['external_topics'][subscription['from_topic']]
        from ose_editor.component_commands import check_contract
        check_contract(self, model, publisher, consumer)
        self._check_reception(subscription['to'])

    def rename(self, command):
        kind, old, new = command['kind'], command.get('old', ''), command['name']
        if not new or kind in ('component', 'port') and '.' in new:
            raise ValueError('Name must be nonempty and contain no dots for components/ports')
        model = loads(self.document(self.model).text)
        if kind == 'component':
            self.document(self.model).text = JsonSource(self.document(self.model).text).rename(['components', old], new)
            for i, sub in enumerate(model.get('subscriptions', [])):
                for endpoint in ('from', 'to'):
                    if sub.get(endpoint, '').startswith(old + '.'):
                        self.json_set(self.model, ['subscriptions', str(i), endpoint], new + sub[endpoint][len(old):])
            for key in self.deployments:
                data = loads(self.document(key).text)
                for path in (['components', old], ['process_names', 'components', old]):
                    try:
                        self.document(key).text = JsonSource(self.document(key).text).rename(path, new)
                    except KeyError:
                        pass
            if old in self.workspace['positions']:
                self.workspace['positions'][new] = self.workspace['positions'].pop(old)
            for group in self.workspace['groups'].values():
                group['members'] = [new if item == old else item for item in group.get('members', [])]
        elif kind == 'port':
            component, port = old.split('.')
            direction = command['direction']
            self.document(self.model).text = JsonSource(self.document(self.model).text).rename(['components', component, direction, port], new)
            if direction == 'outputs':
                for origin, desc in model['components'][component]['inputs'].items():
                    for event, outputs in desc.get('activates', {}).items():
                        if port in outputs:
                            self.json_set(self.model, ['components', component, 'inputs', origin, 'activates', event],
                                          [new if output == port else output for output in outputs])
            for i, sub in enumerate(model.get('subscriptions', [])):
                field = 'from' if direction == 'outputs' else 'to'
                if sub.get(field) == old:
                    self.json_set(self.model, ['subscriptions', str(i), field], component + '.' + new)
            from ose_editor.component_commands import validate
            validate(self)
        elif kind == 'subscription':
            for i, sub in enumerate(model.get('subscriptions', [])):
                if sub['id'] == new:
                    raise ValueError('Subscription ID already exists')
                if sub['id'] == old:
                    self.json_set(self.model, ['subscriptions', str(i), 'id'], new)
            for key in self.deployments:
                try:
                    self.document(key).text = JsonSource(self.document(key).text).rename(['subscriptions', old], new)
                except KeyError:
                    pass
        elif kind == 'parameter':
            self.document(self.model).text = JsonSource(self.document(self.model).text).rename(['shared_parameters', old], new)
            for name, component in model['components'].items():
                for alias, binding in component.get('parameter_bindings', {}).items():
                    if binding['path'] == old:
                        self.json_set(self.model, ['components', name, 'parameter_bindings', alias, 'path'], new)
            for key in self.deployments:
                data = loads(self.document(key).text)
                for identity, permissions in data.get('parameter_clients', {}).items():
                    for access in ('read', 'write'):
                        for index, path in enumerate(permissions.get(access, [])):
                            if path == old:
                                self.json_set(key, ['parameter_clients', identity, access, str(index)], new)
        else:
            self.rename_sdl(command)

    def rename_sdl(self, command):
        key, path = command['file'], command['path']
        origin = SdlSource(self.document(key).text)
        node = origin.index[path]
        old, new = node['name'], command['name']
        if node['kind'] == 'package':
            if not re.fullmatch(r'[A-Za-z][A-Za-z0-9_]*(?:\.[A-Za-z][A-Za-z0-9_]*)*', new):
                raise ValueError('Use a valid qualified package name')
            affected = set().union(*(set(members) for members in self.schema_inputs.values() if key in members))
            for file in affected:
                doc = self.document(file)
                if doc.format != 'sdl':
                    raise ValueError('Package refactoring across descriptor formats is not yet available')
                parsed = SdlSource(doc.text)
                patches = []
                for other in parsed.index.values():
                    for prop_name, prop in other['properties'].items():
                        if other['kind'] == 'package' and prop_name == 'name' and prop['value'] == old:
                            patches.append((prop['start'], prop['end'], new))
                        elif prop_name in ('type', 'target', 'expression', 'dimensions'):
                            for match in re.finditer(r'(?<![\w.])' + re.escape(old) + r'(?=\.)', prop['value']):
                                patches.append((prop['start'] + match.start(), prop['start'] + match.end(), new))
                doc.text = parsed.patches(patches)
            source = JsonSource(self.document(self.model).text)
            def package_roots(item, pointer=()):
                if isinstance(item.value, dict) and str(item.value.get('root', '')).startswith(old + '.'):
                    schema = item.value.get('schema', source.root.value.get('schemas'))
                    if affected.intersection(self.schema_inputs.get(schema, [])):
                        yield (*pointer, 'root'), new + item.value['root'][len(old):]
                for name, child in item.children.items():
                    yield from package_roots(child, (*pointer, name))
            for pointer, root in list(package_roots(source.root)):
                self.json_set(self.model, pointer, root)
            return
        if not re.fullmatch(r'[^\W\d]\w*', new):
            raise ValueError('Use a valid SDL declaration name')
        full_old = '.'.join(filter(None, (origin.package, old)))
        full_new = '.'.join(filter(None, (origin.package, new)))
        scopes = [set(members) for members in self.schema_inputs.values() if key in members]
        affected = set().union(*scopes) if scopes else {key}
        for file in affected:
            doc = self.document(file)
            if doc.format != 'sdl':
                raise ValueError('Cross-format renames require editing the descriptor references in the preview first')
            parsed = SdlSource(doc.text)
            patches = []
            for other in parsed.index.values():
                if file == key and other['path'] == path:
                    prop = other['properties']['name']
                    patches.append((prop['start'], prop['end'], new))
                for prop_name in ('type', 'target', 'expression', 'dimensions'):
                    if prop_name not in other['properties']:
                        continue
                    prop = other['properties'][prop_name]
                    pattern = r'(?<![\w.])(?:\.?\w+\.)*\w+(?![\w.])'
                    for match in re.finditer(pattern, prop['value']):
                        ref = match[0]
                        resolved = ref[1:] if ref.startswith('.') else ref if '.' in ref else '.'.join(filter(None, (parsed.package, ref)))
                        if resolved == full_old:
                            replacement = new if '.' not in ref else ('.' + new if ref.startswith('.') and not origin.package else full_new)
                            patches.append((prop['start'] + match.start(), prop['start'] + match.end(), replacement))
            doc.text = parsed.patches(patches)
        # Roots in the model are complete logical names, scoped by schema input.
        source = JsonSource(self.document(self.model).text)
        def roots(node, pointer=()):
            if isinstance(node.value, dict) and node.value.get('root') == full_old:
                schema = node.value.get('schema', loads(self.document(self.model).text).get('schemas'))
                if key in self.schema_inputs.get(schema, []):
                    yield (*pointer, 'root')
            for child_key, child in node.children.items():
                yield from roots(child, (*pointer, child_key))
        for pointer in list(roots(source.root)):
            self.json_set(self.model, pointer, full_new)

    @staticmethod
    def identity_changes(key, old, new):
        if not key.endswith('.sdl'):
            return []
        try:
            a, b = SdlSource(old), SdlSource(new)
            def identities(source):
                result = {}
                for root in source.nodes:
                    if root['kind'] != 'message':
                        continue
                    ordinal = 0
                    def fields(nodes, chain):
                        nonlocal ordinal
                        for node in nodes:
                            name = '.'.join([*chain, node['name']])
                            result[(name, 'field ID')] = node['effective_id']
                            if node.get('anonymous'):
                                ordinal += 1
                                result[(name, 'anonymous type')] = root['name'] + '$' + str(ordinal)
                            fields(node['children'], [*chain, node['name']])
                    fields(root['children'], [root['name']])
                return result
            previous, current = identities(a), identities(b)
            return [{'field': field, 'kind': kind, 'before': previous[(field, kind)], 'after': value}
                    for (field, kind), value in current.items() if (field, kind) in previous and previous[(field, kind)] != value]
        except ValueError:
            return []

    def handlers(self):
        result = set()
        for key in self.deployments:
            try:
                for component in loads(self.document(key).text).get('components', {}).values():
                    handler = component.get('container', {}).get('handler')
                    if handler:
                        result.add(handler)
            except (ValueError, AttributeError):
                continue
        return sorted(result)

    def projection(self):
        result, diagnostics = {}, list(self.discovery_errors)
        if self.recovery_warning:
            diagnostics.append({'severity': 'warning', 'origin': 'recovery', 'message': self.recovery_warning})
        for key, doc in self.documents.items():
            if not doc.active:
                continue
            item = {'id': key, 'role': doc.role, 'format': doc.format, 'dirty': doc.dirty, 'writable': os.access(doc.path if doc.path.exists() else doc.path.parent if doc.path.parent.exists() else self.root, os.W_OK), 'stale': False}
            try:
                parsed = doc.parsed()
                if isinstance(parsed, SdlSource):
                    item.update(nodes=parsed.nodes, package=parsed.package, comments=parsed.comments())
                else:
                    item.update(value=web(parsed.root.value), tree=parsed.tree())
                self.last_valid[key] = deepcopy(item)
            except (ValueError, KeyError, TypeError, IndexError, AttributeError) as error:
                item.update(deepcopy(self.last_valid.get(key, {})))
                item.update(stale=True, dirty=doc.dirty, error=str(error))
                diagnostics.append(source_diagnostic(error, key, doc.text, revision=self.revision))
            result[key] = item
        validation = self.last_validation if self.last_validation and self.last_validation.get('revision') == self.revision else None
        if not result.get(self.model, {}).get('stale'):
            try:
                self.application_model()
            except ValueError as error:
                diagnostics.append({'severity': 'error', 'origin': 'OSE', 'file': self.model, 'message': str(error)})
        return {'name': self.root.name, 'revision': self.revision, 'model': self.model, 'deployments': self.deployments,
                'selected_deployment': self.selected_deployment, 'documents': result, 'schema_inputs': self.schema_inputs,
                'workspace': deepcopy(self.workspace), 'diagnostics': diagnostics, 'validation': validation,
                'dirty': any(doc.dirty for doc in self.documents.values() if doc.active) or any(doc.deleted for doc in self.documents.values()) or self.workspace != self.workspace_baseline,
                'can_undo': bool(self.history), 'can_redo': bool(self.redo_history), 'recovery': bool(self.recovery), 'draft_recovery': bool(self.draft_recovery)}

    def differences(self):
        return [{'file': key, 'diff': ''.join(difflib.unified_diff((doc.baseline or b'').decode().splitlines(True), ([] if doc.deleted else doc.text.splitlines(True)), fromfile=key, tofile=key))}
                for key, doc in self.documents.items() if doc.active and doc.dirty or doc.deleted]

    def save(self, revision, allow_invalid=False, replace=atomic_write):
        with self.mutex:
            self.check_revision(revision)
            projection = self.projection()
            failed_validation = self.last_validation and self.last_validation.get('revision') == self.revision and self.last_validation.get('status') == 'failed'
            if (any(item['severity'] == 'error' for item in projection['diagnostics']) or failed_validation) and not allow_invalid:
                raise ValueError('Draft has errors; explicitly choose Save draft')
            conflicts = []
            for key, doc in self.documents.items():
                if not doc.active and not doc.deleted:
                    continue
                self.resolve(doc.path)
                if (doc.dirty or doc.deleted) and doc.path.exists() and not os.access(doc.path, os.W_OK):
                    raise ValueError('Source is read-only: ' + key)
                current = doc.path.read_bytes() if doc.path.exists() else None
                if current != doc.baseline:
                    conflicts.append({'file': key, 'baseline': (doc.baseline or b'').decode(), 'draft': doc.text, 'disk': (current or b'').decode()})
            if conflicts:
                error = Conflict('Files changed outside the editor')
                error.conflicts = conflicts
                raise error
            entries = []
            for key, doc in self.documents.items():
                if doc.active and doc.dirty or doc.deleted:
                    entries.append({'path': str(doc.path), 'old': base64.b64encode(doc.baseline).decode() if doc.baseline is not None else None,
                                    'new': None if doc.deleted else base64.b64encode(doc.text.encode()).decode(), 'mode': stat.S_IMODE(doc.path.stat().st_mode) if doc.path.exists() else 0o600})
            if entries:
                journal = {'version': 1, 'entries': entries}
                atomic_write(self.state / 'save-journal.json', json.dumps(journal).encode())
                try:
                    for entry in entries:
                        if entry['new'] is None:
                            Path(entry['path']).unlink()
                        else:
                            replace(Path(entry['path']), base64.b64decode(entry['new']), entry['mode'])
                except BaseException:
                    self.recovery = journal
                    raise
                (self.state / 'save-journal.json').unlink()
                for doc in self.documents.values():
                    if doc.active:
                        doc.baseline = doc.text.encode()
                    elif doc.deleted:
                        doc.baseline = None
                        doc.deleted = False
            atomic_write(self.state / 'workspace.json', json.dumps(self.workspace, indent=2).encode())
            self.workspace_baseline = deepcopy(self.workspace)
            (self.state / 'draft.json').unlink(missing_ok=True)
            self.change()
            return self.projection()

    def persist_draft(self):
        data = {'version': 1, 'files': {key: {'path': str(doc.path), 'text': doc.text, 'role': doc.role,
            'baseline': base64.b64encode(doc.baseline).decode() if doc.baseline is not None else None,
            'created': doc.created, 'active': doc.active, 'deleted': doc.deleted} for key, doc in self.documents.items() if doc.active or doc.deleted},
            'workspace': self.workspace, 'deployments': self.deployments, 'selected': self.selected_deployment}
        encoded = json.dumps(data).encode()
        try:
            if len(encoded) <= 64 * 1024 * 1024:
                atomic_write(self.state / 'draft.json', encoded)
                self.recovery_warning = None
            else:
                (self.state / 'draft.json').unlink(missing_ok=True)
                self.recovery_warning = 'Unsaved draft exceeds the 64 MiB recovery limit; save sources to retain it.'
        except OSError:
            self.recovery_warning = 'Unsaved draft recovery could not be written; save sources to retain this revision.'

    def _read_draft(self):
        path = self.state / 'draft.json'
        if not path.is_file():
            return None
        if path.stat().st_size > 64 * 1024 * 1024:
            raise ValueError('Draft recovery exceeds the 64 MiB limit')
        data = json.loads(path.read_text())
        if data.get('version') != 1:
            raise ValueError('Unsupported draft recovery version')
        for item in data['files'].values():
            self.resolve(item['path'])
        return data

    def recover_draft(self, revision, restore):
        with self.mutex:
            if revision != self.revision or self.recovery:
                raise Conflict('Reload the project and resolve any interrupted save first')
            data = self.draft_recovery
            if not data:
                raise ValueError('No recovered draft is available')
            if restore:
                for key, item in data['files'].items():
                    path = self.resolve(item['path'])
                    current = path.read_bytes() if path.exists() else None
                    baseline = base64.b64decode(item['baseline']) if item['baseline'] is not None else None
                    if current != baseline:
                        raise Conflict('Recovered draft baseline changed on disk: ' + key)
                before = self.snapshot()
                for key, item in data['files'].items():
                    path = self.resolve(item['path'])
                    registered = self.load(path, item['role'], create=True, text=item['text'])
                    if registered != key:
                        raise ValueError('Recovered source roots no longer match this project')
                    doc = self.documents[key]
                    doc.text, doc.active, doc.created, doc.deleted = item['text'], item['active'], item['created'], item['deleted']
                self.workspace = deepcopy(data['workspace'])
                self.deployments = data['deployments']
                self.selected_deployment = data['selected']
                self.history.append((before, self.snapshot()))
            self.draft_recovery = None
            (self.state / 'draft.json').unlink(missing_ok=True)
            self.change()
            if restore:
                self.persist_draft()
            return self.projection()

    def _read_recovery(self):
        path = self.state / 'save-journal.json'
        if not path.exists():
            return None
        data = json.loads(path.read_text())
        if data.get('version') != 1:
            raise ValueError('Unsupported save recovery version')
        for entry in data['entries']:
            self.resolve(entry['path'])
        return data

    def recover(self, choice):
        with self.mutex:
            if choice not in ('restore', 'complete') or not self.recovery:
                raise ValueError('Choose restore or complete for an interrupted save')
            for entry in self.recovery['entries']:
                path = self.resolve(entry['path'])
                current = path.read_bytes() if path.exists() else None
                old = base64.b64decode(entry['old']) if entry['old'] is not None else None
                new = base64.b64decode(entry['new']) if entry['new'] is not None else None
                if current not in (old, new):
                    raise Conflict('Recovery file changed externally: ' + str(path))
            for entry in self.recovery['entries']:
                path = self.resolve(entry['path'])
                value = entry['new'] if choice == 'complete' else entry['old']
                if value is None:
                    if path.exists():
                        path.unlink()
                else:
                    atomic_write(path, base64.b64decode(value), entry['mode'])
            (self.state / 'save-journal.json').unlink()
            self.recovery = None
            if choice == 'complete':
                self.draft_recovery = None
                (self.state / 'draft.json').unlink(missing_ok=True)
            for doc in self.documents.values():
                if doc.path.exists():
                    doc.baseline = doc.path.read_bytes()
                    doc.text = doc.baseline.decode()
            self.change()
            return self.projection()
