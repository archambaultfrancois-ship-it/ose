"""Resolve reusable subsystem definitions into the ordinary OSE component graph.

Interfaces are aliases, never runtime relays. Executable identities carry the
instance path so every runtime service exposes readable component names.
"""
from copy import deepcopy
import json
from pathlib import Path
import re

from ose.modeling.model import fields, load
from ose.names import split_endpoint, endpoint_name


MODEL_FIELDS = ('version', 'schemas', 'components', 'subscriptions',
                'external_topics', 'shared_parameters', 'subsystems')
DEPLOYMENT_FIELDS = ('version', 'components', 'subscriptions', 'max_frame',
                     'max_datagram', 'broker', 'execution', 'parameter_clients',
                     'process_names', 'subsystems')


def definition(path, *, loader=load):
    path = Path(path).resolve()
    if path.is_dir():
        path /= 'subsystem.json'
    value = loader(path)
    fields(value, ('version', 'model', 'deployment', 'interface'),
           ('version', 'model', 'deployment', 'interface'))
    for key in ('model', 'deployment'):
        if not isinstance(value[key], str) or not value[key]:
            raise ValueError(f'{path}: {key} must be a nonempty path')
    fields(value['interface'], ('inputs', 'outputs', 'parameters'))
    for key in ('inputs', 'outputs', 'parameters'):
        entries = value['interface'].get(key, {})
        if not isinstance(entries, dict) or len(entries) > 256:
            raise ValueError(f'{path}: interface {key} must contain at most 256 entries')
    return path, value


def identifier(parts, kind):
    name = '.'.join(parts)
    return name if kind in ('component', 'subscription', 'client') else 'harness_' + kind.replace('-', '_') + '_' + name


def _name(value):
    if not isinstance(value, str) or not re.fullmatch(r'[A-Za-z][A-Za-z0-9_]*', value):
        raise ValueError(f'invalid subsystem/interface name: {value!r}')
    return value


def _path(base, value):
    if not isinstance(value, str) or not value:
        raise ValueError('expected a nonempty file path')
    return str((base / value).resolve())


class Resolver:
    def __init__(self, loader=load):
        self.loader = loader
        self.components, self.subscriptions, self.blocks, self.topics = {}, [], {}, {}
        self.executions, self.routes, self.clients, self.aliases = {}, {}, {}, {}
        self.metadata = dict(components={}, subscriptions={}, instances={}, parameter_contracts=[])
        self.active, self.documents, self.document_bytes = [], 0, 0

    def read(self, path, version=1):
        self.documents += 1
        value = self.loader(path, version=version)
        self.document_bytes += (Path(path).stat().st_size if self.loader is load
                                else len(json.dumps(value).encode()))
        if self.documents > 1024 or self.document_bytes > 64 * 1024 * 1024:
            raise ValueError('composition exceeds 1024 documents or 64 MiB')
        return value

    def scope(self, model_path, deployment_path, parts=(), interface=None,
              parameter_bindings=None, initial=None, overrides=None):
        if len(parts) > 32:
            raise ValueError('subsystem nesting exceeds 32 levels')
        model_path, deployment_path = Path(model_path).resolve(), Path(deployment_path).resolve()
        model = self.read(model_path, 2)
        deployment = self.read(deployment_path)
        if overrides:
            if not isinstance(overrides, dict):
                raise ValueError('component deployment overrides must be an object')
            for name, changes in overrides.items():
                if name not in deployment['components']:
                    raise ValueError(f'unknown component deployment override: {name}')
                fields(changes, ('env', 'placement'))
                execution = deployment['components'][name]
                if 'env' in changes:
                    if not isinstance(changes['env'], dict):
                        raise ValueError('deployment env override must be an object')
                    execution['env'] = {**execution.get('env', {}), **changes['env']}
                if 'placement' in changes:
                    execution['placement'] = changes['placement']
        fields(model, MODEL_FIELDS, ('version', 'schemas', 'components', 'subscriptions'))
        fields(deployment, DEPLOYMENT_FIELDS, ('version', 'components'))
        process_names = deployment.get('process_names', {})
        fields(process_names, ('prefix', 'components'))
        if not isinstance(process_names.get('components', {}), dict):
            raise ValueError('process_names components must be an object')
        if parts and (any(k in deployment for k in ('broker', 'max_frame', 'max_datagram'))
                      or deployment.get('execution', 'local') != 'local' or 'prefix' in process_names):
            raise ValueError('nested deployments cannot define application-wide broker, limits, execution or process prefix')
        for key in ('components', 'subsystems', 'external_topics', 'shared_parameters'):
            if not isinstance(model.get(key, {}), dict):
                raise ValueError(f'{model_path}: {key} must be an object')
        for key in ('components', 'subsystems', 'subscriptions', 'parameter_clients'):
            if not isinstance(deployment.get(key, {}), dict):
                raise ValueError(f'{deployment_path}: {key} must be an object')
        children = model.get('subsystems', {})
        if set(children) & set(model['components']):
            raise ValueError(f'{model_path}: component and subsystem names collide')
        if set(deployment['components']) != set(model['components']):
            raise ValueError(f'{deployment_path}: deployment must cover exactly local components')
        if set(deployment.get('subsystems', {})) - set(children):
            raise ValueError(f'{deployment_path}: unknown subsystem deployment')
        if not isinstance(model['subscriptions'], list):
            raise ValueError(f'{model_path}: subscriptions must be a list')
        base, depbase = model_path.parent, deployment_path.parent
        schema = _path(base, model['schemas'])

        def contract(desc):
            desc = deepcopy(desc)
            if desc.get('kind') != 'timer':
                desc['schema'] = _path(base, desc.get('schema', model['schemas']))
            return desc

        parameter_bindings = {} if parameter_bindings is None else parameter_bindings
        initial = {} if initial is None else initial
        if not isinstance(parameter_bindings, dict) or not isinstance(initial, dict):
            raise ValueError('public parameter bindings and initial overrides must be objects')
        exposed_params = (interface or {}).get('parameters', {})
        if not isinstance(exposed_params, dict):
            raise ValueError('interface parameters must be an object')
        if set(parameter_bindings) - set(exposed_params) or set(initial) - set(exposed_params):
            raise ValueError(f'{model_path}: unknown public parameter')
        if set(parameter_bindings) & set(initial):
            raise ValueError('a public parameter cannot have both binding and initial override')
        block_names = {key: '/'.join((*parts, key)) if parts else key
                       for key in model.get('shared_parameters', {})}
        for alias, target in exposed_params.items():
            _name(alias)
            fields(target, ('path',), ('path',))
            key = target['path']
            if key not in block_names:
                raise ValueError(f'{model_path}: unknown exposed parameter: {key}')
            if alias in parameter_bindings:
                block_names[key] = parameter_bindings[alias]
        if len({target['path'] for target in exposed_params.values()}) != len(exposed_params):
            raise ValueError('a shared parameter block can have only one public alias')
        for key, desc in model.get('shared_parameters', {}).items():
            fields(desc, ('root', 'schema', 'initial'), ('root', 'initial'))
            resolved = contract(desc)
            for alias, target in exposed_params.items():
                if target['path'] == key and alias in initial:
                    resolved['initial'] = deepcopy(initial[alias])
            actual = block_names[key]
            if actual in self.blocks:
                if actual not in parameter_bindings.values():
                    raise ValueError(f'parameter path collision: {actual}')
                self.metadata['parameter_contracts'].append(dict(source=resolved, target=actual))
            else:
                self.blocks[actual] = resolved
        local = {}
        for name, node in model['components'].items():
            if not name or '.' in name or ':' in name:
                raise ValueError('local component names must be nonempty and contain no dots or colons')
            actual = identifier((*parts, name), 'component') if parts else name
            if actual in self.components:
                raise ValueError(f'component identifier collision: {actual}')
            local[name] = actual
            if not isinstance(node, dict):
                raise ValueError(f'{model_path}: components must be objects')
            node = deepcopy(node)
            for direction in ('inputs', 'outputs', 'recording_types'):
                if direction in node:
                    node[direction] = {key: contract(desc) for key, desc in node[direction].items()}
            for binding in node.get('parameter_bindings', {}).values():
                if binding['path'] not in block_names:
                    raise ValueError(f'{model_path}: unknown parameter binding: {binding["path"]}')
                binding['path'] = block_names[binding['path']]
            self.components[actual] = node
            self.metadata['components'][actual] = dict(path=list((*parts, name)),
                model=str(model_path), deployment=str(deployment_path), name=name, schema=schema)
            execution = deepcopy(deployment['components'][name])
            # Resolve file paths in the document which owns the execution. Leave
            # build/generated substitutions for the actual compiler destination.
            def expand(value):
                return value.replace('{project}', str(base))
            for key in ('command',):
                if key in execution:
                    execution[key] = [expand(v) for v in execution[key]]
            if 'build' in execution:
                execution['build'] = [[expand(v) for v in argv] for argv in execution['build']]
            if 'env' in execution:
                execution['env'] = {k: expand(v) for k, v in execution['env'].items()}
            if 'container' in execution:
                value = expand(execution['container']['handler'])
                execution['container']['handler'] = (value if value.startswith(('{build}', '{generated}', '{sdl_root}'))
                                                     else str(depbase / value))
            value = expand(execution.get('cwd', '.'))
            execution['cwd'] = (value if value.startswith(('{build}', '{generated}', '{sdl_root}'))
                                else str(depbase / value))
            self.executions[actual] = execution
        for name, alias in deployment.get('process_names', {}).get('components', {}).items():
            if name not in local:
                raise ValueError(f'unknown process alias component: {name}')
            self.aliases[local[name]] = alias
        external = {}
        for topic, desc in model.get('external_topics', {}).items():
            actual = '/'.join((*parts, topic)) if parts else topic
            if actual in self.topics:
                raise ValueError(f'external topic collision: {actual}')
            external[topic] = actual
            self.topics[actual] = contract(desc)
        child_interfaces = {}
        for name, instance in children.items():
            _name(name)
            fields(instance, ('source', 'parameters', 'parameter_bindings'), ('source',))
            manifest_path, manifest = definition(_path(base, instance['source']), loader=self.read)
            if manifest_path in self.active:
                cycle = ' -> '.join(map(str, [*self.active, manifest_path]))
                raise ValueError(f'subsystem reference cycle: {cycle}')
            child_dep = deployment.get('subsystems', {}).get(name, {})
            fields(child_dep, ('source', 'components'))
            selected_deployment = (_path(depbase, child_dep['source']) if 'source' in child_dep
                                   else _path(manifest_path.parent, manifest['deployment']))
            bindings = instance.get('parameter_bindings', {})
            if not isinstance(bindings, dict) or any(not isinstance(v, str) or v not in block_names for v in bindings.values()):
                raise ValueError(f'{model_path}: unknown parent parameter binding')
            self.active.append(manifest_path)
            try:
                child_interfaces[name] = self.scope(
                    _path(manifest_path.parent, manifest['model']), selected_deployment,
                    (*parts, name), manifest['interface'],
                    {k: block_names[v] for k, v in bindings.items()}, instance.get('parameters'),
                    child_dep.get('components'))[2]
            except (ValueError, OSError) as error:
                raise ValueError(f'subsystem {".".join((*parts, name))} ({manifest_path}): {error}') from error
            finally:
                self.active.pop()
            self.metadata['instances']['.'.join((*parts, name))] = dict(
                source=str(manifest_path), interface=child_interfaces[name])

        def endpoint(value, direction):
            if not isinstance(value, str) or len(split_endpoint(value)) != 2:
                raise ValueError(f'{model_path}: invalid endpoint: {value!r}')
            name, port = split_endpoint(value)
            if name in local and port in self.components[local[name]][direction]:
                if self.components[local[name]][direction][port].get('kind') == 'timer':
                    raise ValueError('a timer cannot be exposed or connected')
                return endpoint_name(local[name], port)
            if name in child_interfaces and port in child_interfaces[name][direction]:
                return child_interfaces[name][direction][port]
            raise ValueError(f'{model_path}: unknown or private {direction} endpoint: {value}')

        ids = set()
        for sub in model['subscriptions']:
            fields(sub, ('id', 'from', 'from_topic', 'to', 'qos'), ('id', 'to'))
            key = sub['id']
            if not isinstance(key, str) or not key or key in ids:
                raise ValueError(f'{model_path}: subscription IDs must be unique and nonempty')
            ids.add(key)
            if ('from' in sub) == ('from_topic' in sub):
                raise ValueError('subscription needs exactly one from or from_topic')
            actual = identifier((*parts, key), 'subscription') if parts else key
            if actual in self.metadata['subscriptions']:
                raise ValueError(f'subscription identifier collision: {actual}')
            resolved = deepcopy(sub)
            resolved.update(id=actual, to=endpoint(sub['to'], 'inputs'))
            if 'from' in sub:
                resolved['from'] = endpoint(sub['from'], 'outputs')
            else:
                if sub['from_topic'] not in external:
                    raise ValueError(f'unknown external topic: {sub["from_topic"]}')
                resolved['from_topic'] = external[sub['from_topic']]
            self.subscriptions.append(resolved)
            if key in deployment.get('subscriptions', {}):
                self.routes[actual] = deepcopy(deployment['subscriptions'][key])
            self.metadata['subscriptions'][actual] = dict(path=list((*parts, key)), model=str(model_path))
        if set(deployment.get('subscriptions', {})) - ids:
            raise ValueError(f'{deployment_path}: unknown subscription in deployment')
        for name, permissions in deployment.get('parameter_clients', {}).items():
            actual = identifier((*parts, name), 'client') if parts else name
            if actual in self.clients:
                raise ValueError('parameter client identity collision')
            permissions = deepcopy(permissions)
            for access in ('read', 'write'):
                if access in permissions:
                    if any(key not in block_names for key in permissions[access]):
                        raise ValueError('unknown parameter client block')
                    permissions[access] = [block_names[key] for key in permissions[access]]
            self.clients[actual] = permissions
        public = dict(inputs={}, outputs={}, parameters={})
        if interface is not None:
            fields(interface, ('inputs', 'outputs', 'parameters'))
            for direction, key in (('inputs', 'to'), ('outputs', 'from')):
                for alias, target in interface.get(direction, {}).items():
                    _name(alias)
                    fields(target, (key,), (key,))
                    public[direction][alias] = endpoint(target[key], direction)
            public['parameters'] = {name: block_names[target['path']] for name, target in exposed_params.items()}
        return schema, deployment, public


def resolve(model_path, deployment_path, *, interface=None, loader=load):
    """Return flat documents, source map and optional root public interface.

    An optional loader(path, version=...) supplies draft documents; the default
    reads source files. Drafts use the same resolver and structural rules.
    """
    model_path, deployment_path = Path(model_path).resolve(), Path(deployment_path).resolve()
    original = loader(model_path, version=2)
    if 'subsystems' not in original and interface is None:
        return original, loader(deployment_path), None, None
    resolver = Resolver(loader)
    schema, deployment, public = resolver.scope(model_path, deployment_path, interface=interface)
    model = dict(version=2, schemas=schema, components=resolver.components,
                 subscriptions=resolver.subscriptions)
    if resolver.blocks:
        model['shared_parameters'] = resolver.blocks
    if resolver.topics:
        model['external_topics'] = resolver.topics
    deployment = deepcopy(deployment)
    deployment.pop('subsystems', None)
    deployment['components'] = resolver.executions
    deployment['subscriptions'] = resolver.routes
    deployment['parameter_clients'] = resolver.clients
    deployment['process_names'] = {**deployment.get('process_names', {}), 'components': resolver.aliases}
    return model, deployment, resolver.metadata, public
