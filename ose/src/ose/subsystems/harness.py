"""Assemble ordinary OSE graphs around one explicit subsystem under test."""
from ose.names import split_endpoint
import asyncio
from copy import deepcopy
import json
from pathlib import Path
import sys
import tempfile
import uuid
import xml.etree.ElementTree as ET

from ose.modeling.composition import definition, resolve, identifier
from ose.modeling.compiler import build, compile_plan
from ose.modeling.model import fields, load
from ose.parameters.values import Values
from ose.subsystems import assertions
from ose.timing import seconds_ns


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, ensure_ascii=True) + '\n')
    return path


def document_path(base, value):
    if not isinstance(value, str) or not value:
        raise ValueError('harness document paths must be nonempty strings')
    return (base / value).resolve()


def read_harness(manifest_path):
    harness_path = manifest_path.parent / 'tests/harness.json'
    harness = (load(harness_path) if harness_path.exists() else
               dict(version=1, profiles={'standalone': {}}, cases={}))
    fields(harness, ('version', 'profiles', 'cases'), ('version', 'profiles', 'cases'))
    if not isinstance(harness['profiles'], dict) or not isinstance(harness['cases'], dict):
        raise ValueError('harness profiles and cases must be objects')
    return harness_path, harness


def settings(target, profile):
    manifest_path, manifest = definition(target)
    harness_path, harness = read_harness(manifest_path)
    if profile not in harness['profiles']:
        raise ValueError(f'unknown integration profile: {profile}')
    selected = harness['profiles'][profile]
    fields(selected, ('model', 'deployment', 'inject', 'collect', 'cases'))
    if ('model' in selected) != ('deployment' in selected):
        raise ValueError('an integration profile needs both model and deployment')
    case_names = selected.get('cases', list(harness['cases']))
    if (not isinstance(case_names, list) or any(not isinstance(n, str) or n not in harness['cases'] for n in case_names)
            or len(set(case_names)) != len(case_names)):
        raise ValueError('profile cases must list distinct declared test cases')
    return manifest_path, manifest, harness_path, harness, selected


def assemble(target, profile, directory, sdl_root, *, execution_profile=None, compile_components=True):
    manifest_path, manifest, harness_path, harness, selected = settings(target, profile)
    directory = Path(directory).resolve()
    directory.mkdir(parents=True, exist_ok=True)
    if 'model' in selected:
        model_path = document_path(harness_path.parent, selected['model'])
        deployment_path = document_path(harness_path.parent, selected['deployment'])
        original = load(model_path, version=2)
        sut = original.get('subsystems', {}).get('sut', {})
        if 'source' not in sut or definition(model_path.parent / sut['source'])[0] != manifest_path:
            raise ValueError('profile model must instantiate the selected subsystem as sut')
    else:
        own_model = load(manifest_path.parent / manifest['model'], version=2)
        original = dict(version=2, schemas=str((manifest_path.parent / manifest['model']).parent /
                                              own_model['schemas']), components={}, subscriptions=[],
                        subsystems={'sut': {'source': str(manifest_path)}})
        model_path = write_json(directory / 'assembly.model.json', original)
        deployment_path = write_json(directory / 'assembly.deployment.json', dict(version=1, components={}))
    model, deployment, metadata, _ = resolve(model_path, deployment_path)
    public = metadata['instances']['sut']['interface']
    incoming = {split_endpoint(s['to'])[1] for s in original['subscriptions'] if s['to'].startswith('sut.')}
    inject = selected.get('inject', [name for name in public['inputs'] if name not in incoming])
    collect = selected.get('collect', list(public['outputs']))
    for label, names, available in (('inject', inject, public['inputs']), ('collect', collect, public['outputs'])):
        if (not isinstance(names, list) or any(not isinstance(n, str) or n not in available for n in names)
                or len(set(names)) != len(names)):
            raise ValueError(f'{label} must list distinct public {label} ports')
    injected, collected = {}, {}
    for alias in inject:
        endpoint = public['inputs'][alias]
        component, port = split_endpoint(endpoint)
        desc = model['components'][component]['inputs'][port]
        topic = 'harness/' + identifier((alias,), 'inject')
        if topic in model.get('external_topics', {}):
            raise ValueError('harness external topic collision')
        model.setdefault('external_topics', {})[topic] = {k: desc[k] for k in ('root', 'schema')}
        # Match existing shared reception settings for multi-producer inputs.
        qos = next((deepcopy(s.get('qos', {})) for s in model['subscriptions'] if s['to'] == endpoint), {})
        model['subscriptions'].append(dict(id=identifier((alias,), 'inject-subscription'),
            from_topic=topic, to=endpoint, qos=qos))
        injected[alias] = topic
    for alias in collect:
        endpoint = public['outputs'][alias]
        component, port = split_endpoint(endpoint)
        desc = model['components'][component]['outputs'][port]
        name = identifier((alias,), 'collector')
        if name in model['components']:
            raise ValueError('harness collector identifier collision')
        path = directory / f'{name}.jsonl'
        model['components'][name] = dict(inputs={'input': dict(desc)}, outputs={}, parameters=dict(
            sdl_root=str(Path(sdl_root).resolve()), path=str(path), output=alias,
            max_messages=10000, max_bytes=16 * 1024 * 1024))
        deployment['components'][name] = dict(command=[sys.executable, '-m', 'ose.subsystems.collector'],
                                               cwd=str(directory))
        model['subscriptions'].append(dict(id=identifier((alias,), 'collect-subscription'),
            **{'from': endpoint, 'to': name + '.input'}, qos={'capacity': 10000}))
        collected[alias] = path
        metadata['components'][name] = dict(path=['collector', alias], model=str(model_path),
                                            deployment=str(deployment_path), name=name)
    # Materialize the exact expanded input documents for review and reproduction.
    flat_model = write_json(directory / 'resolved.model.json', model)
    flat_deployment = write_json(directory / 'resolved.deployment.json', deployment)
    documents = (model, deployment, metadata, public)
    if compile_components:
        plan_path = build(flat_model, flat_deployment, sdl_root, directory,
                          profile=execution_profile, _documents=documents)
        plan = json.loads(plan_path.read_text())
    else:
        plan = compile_plan(flat_model, flat_deployment, sdl_root, directory,
                            profile=execution_profile, _documents=documents)
        plan_path = write_json(directory / 'plan.json', plan)
    return dict(plan=plan, plan_path=plan_path, inputs=injected, outputs=collected,
                public=public, cases={key: harness['cases'][key] for key in
                    selected.get('cases', list(harness['cases']))}, base=harness_path.parent)


def case_files(assembly, name, *, verify=True):
    if name not in assembly['cases']:
        raise ValueError(f'unknown test case: {name}')
    case = assembly['cases'][name]
    fields(case, ('scenario', 'expectations'), ('scenario',))
    scenario_path = document_path(assembly['base'], case['scenario'])
    scenario = load(scenario_path)
    fields(scenario, ('version', 'duration', 'events'), ('version', 'duration', 'events'))
    horizon = seconds_ns(scenario['duration'], positive=True)
    if not isinstance(scenario['events'], list) or len(scenario['events']) > 10000:
        raise ValueError('a harness scenario accepts at most 10000 events')
    for event in scenario['events']:
        parameter = isinstance(event, dict) and 'parameter' in event
        key = 'parameter' if parameter else 'input'
        fields(event, ('at', key, 'value', *(() if parameter else ('app_id',))), ('at', key, 'value'))
        if not 0 <= seconds_ns(event['at']) < horizon:
            raise ValueError('scenario event must be within [0, duration)')
        available = assembly['public']['parameters'] if parameter else assembly['inputs']
        if not isinstance(event[key], str) or event[key] not in available:
            raise ValueError(f'scenario references an unavailable {key}: {event[key]}')
    expectations = None
    if verify:
        if 'expectations' not in case:
            raise ValueError('test requires expectations; use run for observation only')
        expectations = load(document_path(assembly['base'], case['expectations']))
        assertions.validate(expectations, assembly['outputs'], scenario['duration'])
    return scenario, expectations


def prepare_scenario(assembly, scenario, directory):
    from ose.codec.sdl2 import SDL2
    from ose.delivery.messages import validate_app_id
    codec = SDL2(assembly['plan']['sdl_root'])
    events, size = [], 0
    for index, event in enumerate(scenario['events']):
        if 'parameter' in event:
            key = assembly['public']['parameters'][event['parameter']]
            desc = assembly['plan']['shared_parameters'][key]
            Values(codec.api, desc['catalogue'], desc['root']).encode(event['value'])
            events.append(dict(at=event['at'], parameter=key, value=event['value']))
            continue
        topic = assembly['inputs'][event['input']]
        desc = assembly['plan']['external_topics'][topic]
        payload = Values(codec.api, desc['catalogue'], desc['root']).encode(event['value'])
        body = codec.wrap(desc['catalogue'], payload)
        size += len(body)
        if size > 64 * 1024 * 1024:
            raise ValueError('scenario envelopes exceed 64 MiB')
        path = directory / f'input-{index}.s2ps'
        path.write_bytes(body)
        item = dict(at=event['at'], topic=topic, envelope=str(path))
        if 'app_id' in event:
            item['app_id'] = validate_app_id(event['app_id'])
        events.append(item)
    return write_json(directory / 'scenario.json', dict(version=1, events=events))


def execute_case(target, profile, case, destination, sdl_root, *, workers=1, verify=True,
                 execution_profile=None, microsteps=False, max_microsteps=None):
    directory = Path(destination).resolve() / ('case-' + uuid.uuid4().hex)
    assembly = assemble(target, profile, directory, sdl_root, execution_profile=execution_profile)
    scenario, expectations = case_files(assembly, case, verify=verify)
    scenario_path = prepare_scenario(assembly, scenario, directory)
    if assembly['plan'].get('execution', 'local') != 'local':
        raise ValueError('subsystem tests require a local deployment; use --profile local')
    result = dict(case=case, integration_profile=profile, directory=str(directory), status='error')
    try:
        from ose.execution.simulation import run
        summary = asyncio.run(run(assembly['plan_path'], scenario['duration'], workers=workers,
                                  scenario=scenario_path, trace=directory / 'trace.jsonl',
                                  microsteps=microsteps, max_microsteps=max_microsteps))
        if summary.get('stop_reason') == 'interrupted':
            result.update(status='interrupted', summary=summary)
            write_json(directory / 'result.json', result)
            raise KeyboardInterrupt
        records, total_bytes = [], 0
        for path in assembly['outputs'].values():
            total_bytes += path.stat().st_size
            if total_bytes > 64 * 1024 * 1024:
                raise RuntimeError('total collected output exceeds 64 MiB')
            with path.open() as stream:
                for line in stream:
                    records.append(json.loads(line))
                    if len(records) > 10000:
                        raise RuntimeError('total collected output exceeds 10000 messages')
        records.sort(key=lambda record: (record['time_ns'], record['output']))
        write_json(directory / 'outputs.json', records)
        if summary.get('stop_reason') not in ('duration', 'quiescent'):
            raise RuntimeError(f'simulation did not complete: {summary.get("stop_reason")}')
        collectors = {identifier((alias,), 'collect-subscription') for alias in assembly['outputs']}
        with (directory / 'trace.jsonl').open() as stream:
            for line in stream:
                event = json.loads(line)
                if (event['event'] == 'admission' and event['subscription'] in collectors
                        and event['result'] != 'accepted'):
                    raise RuntimeError(f'incomplete collection: {event["result"]}')
        for alias in assembly['outputs']:
            name = identifier((alias,), 'collector')
            if summary['inputs'][name]['input']['depth']:
                raise RuntimeError('incomplete collection: collector still has queued messages')
        verdicts = assertions.evaluate(expectations, records, assembly['outputs'], scenario['duration']) if verify else []
        result.update(status='passed' if all(r['passed'] for r in verdicts) else 'failed',
                      assertions=verdicts, summary=summary)
    except (ValueError, RuntimeError, OSError, EOFError) as error:
        result['error'] = str(error)
    write_json(directory / 'result.json', result)
    return result


def junit(results, destination):
    suite = ET.Element('testsuite', name='ose.subsystems', tests=str(len(results)),
        failures=str(sum(r['status'] == 'failed' for r in results)),
        errors=str(sum(r['status'] in ('error', 'configuration_error') for r in results)))
    for result in results:
        case = ET.SubElement(suite, 'testcase', name=result['case'], classname=result['integration_profile'])
        if result['status'] != 'passed':
            node = ET.SubElement(case, 'failure' if result['status'] == 'failed' else 'error')
            node.text = json.dumps(result, ensure_ascii=True)
    destination = Path(destination).resolve()
    destination.parent.mkdir(parents=True, exist_ok=True)
    ET.ElementTree(suite).write(destination, encoding='utf-8', xml_declaration=True)


def validate_harness(target, sdl_root, *, integration_profile=None, execution_profile=None):
    manifest_path, _ = definition(target)
    _, harness = read_harness(manifest_path)
    profiles = harness['profiles']
    selected = [integration_profile] if integration_profile is not None else list(profiles)
    if not selected:
        raise ValueError('a harness requires at least one integration profile')
    for profile in selected:
        with tempfile.TemporaryDirectory(prefix='ose-subsystem-validate-') as directory:
            assembly = assemble(target, profile, directory, sdl_root,
                                execution_profile=execution_profile, compile_components=False)
            for name in assembly['cases']:
                case = assembly['cases'][name]
                scenario, _ = case_files(assembly, name, verify='expectations' in case)
                prepare_scenario(assembly, scenario, Path(directory))
