"""Atomic component edits with the same structural rules as OSE execution."""
from copy import deepcopy
import json

from ose.modeling.activation import component_inputs
from ose.modeling.validation import validate_graph


def validate(project, model=None):
    model = model if model is not None else json.loads(project.document(project.model).text)
    deployments = [json.loads(project.document(key).text) for key in project.deployments]
    # A model-only project still needs all structural checks. The placeholder
    # commands are validation data and are never executed.
    if not deployments:
        deployments = [{'version': 1, 'components': {
            name: {'command': ['editor-validation']} for name in model['components']}}]
    for deployment in deployments:
        validate_graph(model, deployment)
    for sub in model['subscriptions']:
        target, port = sub['to'].split('.')
        consumer = model['components'][target]['inputs'][port]
        if 'from_topic' in sub:
            publisher = model['external_topics'][sub['from_topic']]
        else:
            source, output = sub['from'].split('.')
            publisher = model['components'][source]['outputs'][output]
        check_contract(project, model, publisher, consumer)


def check_contract(project, model, publisher, consumer):
    if publisher['root'] != consumer['root']:
        raise ValueError('Connected roots must have the same logical name')
    validation = project.last_validation
    if validation and validation['revision'] == project.revision:
        for report in validation.get('connection_contracts', []):
            if (report['root'] == publisher['root']
                    and report['publisher_schema'] == publisher.get('schema', model['schemas'])
                    and report['consumer_schema'] == consumer.get('schema', model['schemas'])
                    and not report['compatible']):
                raise ValueError('Incompatible SDL contract: ' + report['code']
                                 + (' at ' + report['path'] if report.get('path') else ''))


def execute(project, command):
    model = json.loads(project.document(project.model).text)
    name, action = command['component'], command['action']
    if name not in model['components']:
        raise ValueError('Unknown component')
    node = model['components'][name]
    if action == 'configure_activation':
        if type(command.get('enabled')) is not bool:
            raise ValueError('Activation enabled must be boolean')
        if command.get('previous'):
            if not command['enabled']:
                raise ValueError('Replacing an activation requires enabled=true')
            change_activation(project, name, command['previous'], False)
        change_activation(project, name, command, command['enabled'])
        validate(project)
        return
    direction = 'outputs' if action == 'configure_output' else command['direction']
    if direction not in ('inputs', 'outputs'):
        raise ValueError('Choose inputs or outputs')
    port = command['port']
    if not isinstance(port, str) or not port or '.' in port:
        raise ValueError('Port names must be nonempty and contain no dots')
    ports = node[direction]
    path = ['components', name, direction, port]
    if action == 'configure_output':
        description = command['description']
        if not isinstance(description, dict):
            raise ValueError('Output description must be an object')
        previous = ports.get(port)
        if command.get('create') and previous is not None:
            raise ValueError('Port already exists')
        ports[port] = description
        validate(project, model)
        if previous is None:
            project.json_set(project.model, path, description)
        else:
            for field in previous.keys() - description.keys():
                project.json_set(project.model, [*path, field], remove=True)
            for field, value in description.items():
                if previous.get(field) != value:
                    project.json_set(project.model, [*path, field], value)
    elif action == 'duplicate_port':
        source = command['source']
        if source not in ports:
            raise ValueError('Unknown source port')
        if port in ports:
            raise ValueError('Port already exists')
        ports[port] = deepcopy(ports[source])
        validate(project, model)
        project.json_set(project.model, path, ports[port])
    elif action == 'remove_port':
        if port not in ports:
            raise ValueError('Unknown port')
        project.json_set(project.model, path, remove=True)
        if direction == 'outputs':
            for origin, desc in node['inputs'].items():
                for event, outputs in desc.get('activates', {}).items():
                    if port in outputs:
                        project.json_set(project.model, ['components', name, 'inputs', origin, 'activates', event],
                                         [output for output in outputs if output != port])
        field = 'from' if direction == 'outputs' else 'to'
        removed = []
        for index in range(len(model['subscriptions']) - 1, -1, -1):
            sub = model['subscriptions'][index]
            if sub.get(field) == name + '.' + port:
                removed.append(sub['id'])
                project.json_set(project.model, ['subscriptions', str(index)], remove=True)
        for key in project.deployments:
            deployment = json.loads(project.document(key).text)
            for identity in removed:
                if identity in deployment.get('subscriptions', {}):
                    project.json_set(key, ['subscriptions', identity], remove=True)
        validate(project)


def change_activation(project, component, link, enabled):
    model = json.loads(project.document(project.model).text)
    node = model['components'][component]
    port, event, output = link['input'], link['event'], link['output']
    declarations = component_inputs(node)
    if port not in declarations or event not in declarations[port].outputs:
        raise ValueError('Choose an activating input event; Pull inputs are passive')
    if output not in node['outputs']:
        raise ValueError('Unknown activation output')
    outputs = list(node['inputs'][port].get('activates', {}).get(event, []))
    if enabled:
        if output in outputs:
            raise ValueError('Activation link already exists')
        outputs.append(output)
    else:
        if output not in outputs:
            raise ValueError('Activation link no longer exists')
        outputs.remove(output)
    # Patch only the event's array, preserving other permissions and comments.
    if 'activates' not in node['inputs'][port]:
        project.json_set(project.model, ['components', component, 'inputs', port, 'activates'], {event: outputs})
    else:
        project.json_set(project.model, ['components', component, 'inputs', port, 'activates', event], outputs)
