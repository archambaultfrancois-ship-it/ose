"""Deterministic authoring workload; never included in the runtime release."""
import json
from pathlib import Path


def create(root):
    root = Path(root)
    (root / 'schemas').mkdir(parents=True)
    (root / 'overrides').mkdir()
    schema = 'message Packet { uint64 sequence; }\nmessage Settings { repeated uint64 values; fl32[3][4] matrix; }\n'
    schema += '\n'.join('message Record%d {\n%s\n}' % (i, '\n'.join('    fl64 value%d;' % j for j in range(50))) for i in range(40))
    (root / 'schemas/model.sdl').write_text(schema)
    (root / 'overrides/packet.sdl').write_text('message Packet { uint64 sequence; optional string label; }\n')
    components, subscriptions = {}, []
    for index in range(30):
        name = f'component_{index:02d}'
        components[name] = {'inputs': {f'in{i}': {'root': 'Packet', 'required': False} for i in range(5)} if index else {},
                            'outputs': {f'out{i}': {'root': 'Packet'} for i in range(4)},
                            'trigger': {'mode': 'periodic', 'period_ms': 100} if not index else {'mode': 'on_change'}}
        if index:
            for port in range(4):
                subscriptions.append({'id': f'link_{index}_{port}', 'from': f'component_{index - 1:02d}.out{port}', 'to': name + f'.in{port}'})
    for port in components['component_00']['outputs'].values():
        port['schema'] = 'overrides/packet.sdl'
    for index in (5, 10, 15, 20):
        subscriptions.append({'id': f'fanout_{index}', 'from': 'component_00.out0', 'to': f'component_{index:02d}.in4'})
    parameters = {f'settings/{i}': {'root': 'Settings', 'initial': {'values': list(range(2048)) if i == 0 else [], 'matrix': [[0] * 4 for _ in range(3)]}} for i in range(50)}
    model = {'version': 1, 'schemas': 'schemas', 'components': components, 'subscriptions': subscriptions, 'shared_parameters': parameters}
    (root / 'model.json').write_text(json.dumps(model, indent=2))
    for name in ('local', 'test', 'cluster'):
        deployment = {'version': 1, 'components': {name: {'container': {'language': 'python', 'handler': 'components/handler.py'}} for name in components}}
        (root / f'deployment.{name}.json').write_text(json.dumps(deployment, indent=2))
    (root / '.ose-editor').mkdir()
    (root / '.ose-editor/workspace.json').write_text(json.dumps({'version': 1, 'groups': {
        f'group_{i}': {'members': [f'component_{j:02d}' for j in range(i * 6, i * 6 + 6)], 'collapsed': False} for i in range(5)}}))
    return model
