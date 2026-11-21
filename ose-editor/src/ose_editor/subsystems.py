"""Subsystem source discovery and atomic composition edits over authoring drafts."""
from copy import deepcopy
import json
import os
import re

from ose.modeling.composition import definition, resolve
from ose.names import split_endpoint
from ose.modeling.model import unique_pairs

from .json_source import JsonSource, dump, loads


def read(project, path, *, version=1):
    path = project.resolve(path)
    doc = project.document(project.key(path))
    if len(doc.text.encode()) > 1024 * 1024:
        raise ValueError('configuration exceeds 1 MiB')
    value = json.loads(doc.text, object_pairs_hook=unique_pairs)
    if not isinstance(value, dict) or type(value.get('version')) is not int or value['version'] != version:
        raise ValueError(f'{project.key(path)}: expected a version {version} document')
    return value


def discover(project):
    project.discovery_errors, project.schema_inputs, project.schema_contexts = [], {}, {}
    project.model_contexts, project.subsystem_definitions = {}, {}
    visited, total, scopes = set(), 0, 0

    def error(key, exception):
        project.discovery_errors.append(dict(severity='error', origin='composition', file=key, message=str(exception)))

    def load(path, role):
        nonlocal total
        checked = project.resolve(path)
        key = project.load(checked, role)
        if key not in visited:
            visited.add(key)
            total += len(project.document(key).text.encode())
            if len(visited) > 1024 or total > 64 * 1024 * 1024:
                raise ValueError('composition exceeds 1024 documents or 64 MiB')
        return key, loads(project.document(key).text)

    def schemas(key, model):
        if key in project.schema_contexts:
            return
        context = project.schema_contexts[key] = {}
        descriptions = list(model.get('external_topics', {}).values()) + list(model.get('shared_parameters', {}).values())
        for component in model.get('components', {}).values():
            for direction in ('inputs', 'outputs', 'recording_types'):
                descriptions += list(component.get(direction, {}).values())
        sources = {model.get('schemas', 'schemas')}
        sources.update(desc['schema'] for desc in descriptions if isinstance(desc, dict) and 'schema' in desc)
        for source in sorted(sources):
            try:
                path = project.resolve(project.document(key).path.parent / source)
                # Root spellings remain stable for existing catalogue consumers.
                token = source if key == project.root_model else os.path.relpath(path, project.document(project.root_model).path.parent)
                context[source] = token
                members = [path] if path.is_file() else sorted(path.rglob('*')) if path.is_dir() else []
                keys = []
                for member in members:
                    if member.name.endswith(('.sdl', '.sdl.json', '.sdl.yaml', '.sdl.yml', '.sdl.xsd')):
                        checked = project.resolve(member)
                        known = project.documents.get(project.key(checked))
                        if known is not None and known.deleted:
                            continue
                        keys.append(project.load(checked, 'schema'))
                for member, doc in project.documents.items():
                    if doc.active and doc.role == 'schema' and (doc.path == path or path in doc.path.parents) and member not in keys:
                        keys.append(member)
                project.schema_inputs[token] = keys
                if not keys:
                    raise ValueError('Missing schema input: ' + source)
            except (ValueError, OSError, TypeError) as exception:
                error(key, exception)

    def scope(model_key, deployment_key, stack=()):
        nonlocal scopes
        scopes += 1
        if scopes > 1024:
            raise ValueError('composition exceeds 1024 model/deployment scopes')
        if len(stack) > 32:
            raise ValueError('subsystem nesting exceeds 32 levels')
        model = loads(project.document(model_key).text)
        if not isinstance(model, dict):
            return
        context = project.model_contexts.setdefault(model_key, dict(deployments=[], definitions=[]))
        if deployment_key and deployment_key not in context['deployments']:
            context['deployments'].append(deployment_key)
        schemas(model_key, model)
        deployment = loads(project.document(deployment_key).text) if deployment_key else {}
        for name, instance in model.get('subsystems', {}).items():
            try:
                path = project.resolve(project.document(model_key).path.parent / instance['source'])
                if path.is_dir():
                    path /= 'subsystem.json'
                if path in stack:
                    raise ValueError('subsystem reference cycle: ' + ' -> '.join(map(str, (*stack, path))))
                manifest_key, manifest = load(path, 'subsystem')
                child_model, _ = load(path.parent / manifest['model'], 'model')
                default_dep, _ = load(path.parent / manifest['deployment'], 'deployment')
                project.subsystem_definitions[manifest_key] = dict(model=child_model, deployment=default_dep)
                override = deployment.get('subsystems', {}).get(name, {})
                child_dep = load(project.document(deployment_key).path.parent / override['source'], 'deployment')[0] if 'source' in override else default_dep
                scope(child_model, child_dep, (*stack, path))
                if child_dep != default_dep:
                    scope(child_model, default_dep, (*stack, path))
                child_context = project.model_contexts[child_model]
                if manifest_key not in child_context['definitions']:
                    child_context['definitions'].append(manifest_key)
            except (ValueError, OSError, KeyError, TypeError, AttributeError) as exception:
                error(model_key, f'subsystem {name}: {exception}')

    for deployment_key in project.deployments or [None]:
        try:
            scope(project.model, deployment_key)
        except (ValueError, OSError, KeyError, TypeError, AttributeError) as exception:
            error(project.model, exception)
    # Definition-local profiles may be registered without selecting them in the
    # application's deployment. Keep those editable after save and reopening.
    while True:
        pending = [(model_key, deployment) for model_key, context in project.model_contexts.items()
                   for deployment in project.workspace.get('models', {}).get(model_key, {}).get('deployments', [])
                   if deployment not in context['deployments']]
        if not pending:
            break
        progressed = False
        for model_key, deployment in pending:
            try:
                if deployment not in project.documents:
                    if deployment.startswith('@'):
                        prefix, tail = deployment.split('/', 1)
                        path = project.roots[int(prefix[1:])] / tail
                    else:
                        path = project.root / deployment
                    load(path, 'deployment')
                scope(model_key, deployment)
                progressed = True
            except (ValueError, OSError, KeyError, TypeError, AttributeError, IndexError) as exception:
                error(model_key, exception)
        if not progressed:
            break


def flatten(project, model_key=None, deployment_key=None, model=None):
    model_key = model_key or project.model
    deployment_key = deployment_key or project.selected_deployment
    if not deployment_key:
        raise ValueError('Select a deployment to resolve subsystem composition')

    def loader(path, *, version=1):
        if model is not None and project.resolve(path) == project.document(model_key).path:
            return deepcopy(model)
        return read(project, path, version=version)

    return resolve(project.document(model_key).path, project.document(deployment_key).path, loader=loader)


def schema_token(project, path):
    path = project.resolve(path)
    base = project.document(project.root_model).path.parent
    for token in project.schema_inputs:
        if project.resolve(base / token) == path:
            return token
    return os.path.relpath(path, base)


def port_projection(project):
    result, errors = {}, []
    for key, context in project.model_contexts.items():
        try:
            model = loads(project.document(key).text)
        except (ValueError, KeyError):
            continue
        if not model.get('subsystems') or not context['deployments']:
            continue
        selected = project.selected_deployment if key == project.model else project.workspace.get('models', {}).get(key, {}).get('selected_deployment', context['deployments'][0])
        if selected not in context['deployments']:
            selected = context['deployments'][0]
        try:
            flat, _, metadata, _ = flatten(project, key, selected)
            result[key] = {}
            for name, instance in model['subsystems'].items():
                path, _ = definition(project.document(key).path.parent / instance['source'],
                                     loader=lambda path, version=1: read(project, path, version=version))
                item = dict(definition=project.key(path), **project.subsystem_definitions[project.key(path)])
                for direction in ('inputs', 'outputs'):
                    item[direction] = {}
                    for alias, endpoint in metadata['instances'][name]['interface'][direction].items():
                        component, port = split_endpoint(endpoint)
                        desc = deepcopy(flat['components'][component][direction][port])
                        desc['schema'] = schema_token(project, desc['schema'])
                        item[direction][alias] = desc
                result[key][name] = item
        except (ValueError, OSError, KeyError, TypeError, AttributeError) as exception:
            errors.append(dict(severity='error', origin='composition', file=key, message=str(exception)))
    return result, errors


def name(value):
    if not isinstance(value, str) or not re.fullmatch(r'[A-Za-z][A-Za-z0-9_]*', value):
        raise ValueError('Use a name starting with a letter, followed by ASCII letters, digits or underscores')
    return value


def new_document(project, path, role, value):
    path = project.resolve(path)
    if project.reserved(path):
        raise ValueError('Source path already exists: ' + project.key(path))
    return project.load(path, role, create=True, text=JsonSource(dump(value)).formatted() + '\n')


def set_member(project, key, collection, name, value):
    if collection not in loads(project.document(key).text):
        project.json_set(key, [collection], {})
    project.json_set(key, [collection, name], value)


def connection_model(project):
    model = json.loads(project.document(project.model).text)
    if not model.get('subsystems'):
        return model
    ports, errors = port_projection(project)
    if errors:
        raise ValueError(errors[0]['message'])
    for instance, value in ports.get(project.model, {}).items():
        model['components'][instance] = {direction: value[direction] for direction in ('inputs', 'outputs')}
        for desc in model['components'][instance]['inputs'].values():
            desc.pop('activates', None)
    return model


def require_private(project, endpoint, direction=None):
    for key in project.model_contexts.get(project.model, {}).get('definitions', []):
        interface = loads(project.document(key).text)['interface']
        for kind, field in (('inputs', 'to'), ('outputs', 'from')):
            if direction and direction != kind:
                continue
            if any(binding[field] == endpoint or not direction and binding[field].startswith(endpoint + '.')
                   for binding in interface.get(kind, {}).values()):
                raise ValueError('Remove the public port exposure before deleting its internal target')


def check(project):
    discover(project)
    if project.discovery_errors:
        raise ValueError(project.discovery_errors[0]['message'])
    for key in project.deployments:
        flatten(project, deployment_key=key)


def execute(project, command):
    action = command['action']
    model = project.application_model()
    if action in ('instantiate_subsystem', 'create_subsystem'):
        instance = name(command['name'])
        if instance in model.get('components', {}) or instance in model.get('subsystems', {}):
            raise ValueError('Component or subsystem name already exists')
        if action == 'create_subsystem':
            directory = project.resolve(project.document(project.model).path.parent / command['directory'])
            schema = os.path.relpath(project.resolve(project.document(project.model).path.parent / model['schemas']), directory)
            new_document(project, directory / 'model.json', 'model', dict(version=2, schemas=schema, components={}, subscriptions=[]))
            new_document(project, directory / 'deployment.json', 'deployment', dict(version=1, components={}))
            manifest = new_document(project, directory / 'subsystem.json', 'subsystem', dict(version=1,
                model='model.json', deployment='deployment.json', interface=dict(inputs={}, outputs={}, parameters={})))
            path = project.document(manifest).path
        else:
            path = project.resolve(project.document(project.model).path.parent / command['source'])
            if path.is_dir():
                path /= 'subsystem.json'
        set_member(project, project.model, 'subsystems', instance, {'source': os.path.relpath(path, project.document(project.model).path.parent)})
        check(project)
    elif action == 'remove_subsystem':
        instance = command['name']
        if instance not in model.get('subsystems', {}):
            raise ValueError('Unknown subsystem instance')
        require_private(project, instance)
        project.json_set(project.model, ['subsystems', instance], remove=True)
        removed = []
        for index in range(len(model['subscriptions']) - 1, -1, -1):
            sub = model['subscriptions'][index]
            if sub['to'].startswith(instance + '.') or sub.get('from', '').startswith(instance + '.'):
                removed.append(sub['id'])
                project.json_set(project.model, ['subscriptions', str(index)], remove=True)
        for key in project.deployments:
            data = loads(project.document(key).text)
            if instance in data.get('subsystems', {}):
                project.json_set(key, ['subsystems', instance], remove=True)
            for identity in removed:
                if identity in data.get('subscriptions', {}):
                    project.json_set(key, ['subscriptions', identity], remove=True)
    elif action == 'expose_subsystem_port':
        manifest = command['definition']
        if manifest not in project.subsystem_definitions:
            raise ValueError('Choose a loaded subsystem definition')
        direction = command['direction']
        if direction not in ('inputs', 'outputs', 'parameters'):
            raise ValueError('Choose inputs, outputs or parameters')
        alias = name(command['name'])
        field = {'inputs': 'to', 'outputs': 'from', 'parameters': 'path'}[direction]
        if direction not in loads(project.document(manifest).text)['interface']:
            project.json_set(manifest, ['interface', direction], {})
        project.json_set(manifest, ['interface', direction, alias], {field: command['target']})
        definition_model = project.subsystem_definitions[manifest]['model']
        definition_dep = project.subsystem_definitions[manifest]['deployment']
        value = loads(project.document(manifest).text)
        resolve(project.document(definition_model).path, project.document(definition_dep).path,
                interface=value['interface'], loader=lambda path, version=1: read(project, path, version=version))
    elif action == 'extract_subsystem':
        extract(project, command)
    else:
        raise ValueError('Unknown subsystem command')


def extract(project, command):
    model = project.application_model()
    instance = name(command['name'])
    selected = command['components']
    if not isinstance(selected, list) or not selected or len(set(selected)) != len(selected) or any(c not in model['components'] for c in selected):
        raise ValueError('Select existing local components to extract')
    if instance in model['components'] or instance in model.get('subsystems', {}):
        raise ValueError('Component or subsystem name already exists')
    selected = set(selected)
    base = project.document(project.model).path.parent
    directory = project.resolve(base / command['directory'])
    interface = dict(inputs={}, outputs={}, parameters={})
    aliases = {}

    def expose(endpoint, direction):
        pair = (endpoint, direction)
        if pair not in aliases:
            alias = re.sub(r'[^A-Za-z0-9_]', '_', endpoint)
            alias = name(alias if re.match(r'[A-Za-z]', alias) else 'port_' + alias)
            if alias in interface[direction]:
                raise ValueError('Generated public port names collide; rename the selected ports first')
            aliases[pair] = alias
            interface[direction][alias] = {('to' if direction == 'inputs' else 'from'): endpoint}
        return instance + '.' + aliases[pair]

    internal, remaining = [], []
    for original in model['subscriptions']:
        sub = deepcopy(original)
        source = sub.get('from', '').split('.')[0] in selected
        target = sub['to'].split('.')[0] in selected
        if source and target:
            internal.append(sub)
        else:
            if source:
                sub['from'] = expose(sub['from'], 'outputs')
            if target:
                sub['to'] = expose(sub['to'], 'inputs')
            remaining.append(sub)
    # Keep currently unconnected message ports usable through the new boundary.
    for component in sorted(selected):
        for direction in ('inputs', 'outputs'):
            for port, desc in model['components'][component][direction].items():
                endpoint = component + '.' + port
                field = 'to' if direction == 'inputs' else 'from'
                if desc.get('kind') != 'timer' and not any(sub.get(field) == endpoint for sub in internal):
                    expose(endpoint, direction)

    # A parent definition may already expose the selected implementation.
    # Forward those aliases through the newly introduced public boundary.
    for key in project.model_contexts.get(project.model, {}).get('definitions', []):
        for direction, field in (('inputs', 'to'), ('outputs', 'from')):
            for alias, binding in loads(project.document(key).text)['interface'].get(direction, {}).items():
                endpoint = binding[field]
                if endpoint.split('.')[0] in selected:
                    project.json_set(key, ['interface', direction, alias, field], expose(endpoint, direction))

    child = dict(version=2, schemas=os.path.relpath(project.resolve(base / model['schemas']), directory),
                 components={c: deepcopy(model['components'][c]) for c in model['components'] if c in selected}, subscriptions=internal)
    for node in child['components'].values():
        for direction in ('inputs', 'outputs', 'recording_types'):
            for desc in node.get(direction, {}).values():
                if 'schema' in desc:
                    desc['schema'] = os.path.relpath(project.resolve(base / desc['schema']), directory)
    bindings = {}
    for node in child['components'].values():
        for binding in node.get('parameter_bindings', {}).values():
            path = binding['path']
            if path in bindings.values():
                continue
            alias = name('parameter_' + re.sub(r'[^A-Za-z0-9_]', '_', path))
            if alias in bindings:
                raise ValueError('Generated parameter aliases collide')
            bindings[alias] = path
            interface['parameters'][alias] = {'path': path}
            block = deepcopy(model['shared_parameters'][path])
            if 'schema' in block:
                block['schema'] = os.path.relpath(project.resolve(base / block['schema']), directory)
            child.setdefault('shared_parameters', {})[path] = block
    new_document(project, directory / 'model.json', 'model', child)
    internal_ids = {sub['id'] for sub in internal}
    default_dep = None
    for index, key in enumerate(project.deployments):
        data = loads(project.document(key).text)
        depbase = project.document(key).path.parent
        deployment = dict(version=1, components={})
        for component in sorted(selected):
            execution = deepcopy(data['components'][component])
            def relocate(value):
                relative = os.path.relpath(base, directory)
                return value.replace('{project}', '{project}' + ('/' + relative if relative != '.' else ''))
            for field in ('command',):
                if field in execution:
                    execution[field] = [relocate(v) for v in execution[field]]
            if 'build' in execution:
                execution['build'] = [[relocate(v) for v in argv] for argv in execution['build']]
            if 'env' in execution:
                execution['env'] = {k: relocate(v) for k, v in execution['env'].items()}
            for owner, field in ((execution, 'cwd'), (execution.get('container', {}), 'handler')):
                if owner is not execution and field not in owner:
                    continue
                value = owner.get(field, '.').replace('{project}', str(base))
                owner[field] = value if value.startswith(('{build}', '{generated}', '{sdl_root}')) else os.path.relpath((depbase / value).resolve(), directory)
            deployment['components'][component] = execution
            project.json_set(key, ['components', component], remove=True)
        routes = {identity: value for identity, value in data.get('subscriptions', {}).items() if identity in internal_ids}
        if routes:
            deployment['subscriptions'] = routes
            for identity in routes:
                project.json_set(key, ['subscriptions', identity], remove=True)
        process_aliases = {c: alias for c, alias in data.get('process_names', {}).get('components', {}).items() if c in selected}
        if process_aliases:
            deployment['process_names'] = {'components': process_aliases}
            for component in process_aliases:
                project.json_set(key, ['process_names', 'components', component], remove=True)
        filename = 'deployment.json' if index == 0 else f'deployment-{index + 1}.json'
        new_document(project, directory / filename, 'deployment', deployment)
        default_dep = default_dep or filename
        set_member(project, key, 'subsystems', instance, {'source': os.path.relpath(directory / filename, depbase)})
    if default_dep is None:
        raise ValueError('Register a deployment before extracting components')
    new_document(project, directory / 'subsystem.json', 'subsystem', dict(version=1, model='model.json', deployment=default_dep, interface=interface))
    for component in selected:
        project.json_set(project.model, ['components', component], remove=True)
    project.json_set(project.model, ['subscriptions'], remaining)
    set_member(project, project.model, 'subsystems', instance, dict(source=os.path.relpath(directory / 'subsystem.json', base),
                     **({'parameter_bindings': bindings} if bindings else {})))
    check(project)
