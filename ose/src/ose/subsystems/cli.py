"""CLI for explicit subsystem targets and integration harnesses."""
import json
import os
from pathlib import Path
import subprocess
import sys

from ose.subsystems.harness import (assemble, execute_case, junit, settings,
                                    validate_harness, write_json)


def add_parser(commands):
    command = commands.add_parser('subsystem', help='develop and test reusable subsystems')
    actions = command.add_subparsers(dest='subsystem_action', required=True)
    for name in ('init', 'validate', 'build', 'run', 'test'):
        action = actions.add_parser(name, help={
            'init': 'create an autonomous subsystem and example harness',
            'validate': 'validate subsystem contracts, profiles and test cases without building handlers',
            'build': 'compile the selected subsystem harness',
            'run': 'simulate one case and collect outputs without assertions',
            'test': 'simulate cases, verify assertions and report results'}[name])
        action.add_argument('target', help='subsystem directory or subsystem.json')
        if name == 'init':
            continue
        action.add_argument('--integration-profile', default=None if name == 'validate' else 'standalone',
                            help='harness assembly (default: standalone; validate checks all profiles)')
        action.add_argument('--profile', choices=('local', 'cluster'), help='derive deployment profile')
        action.add_argument('--sdl-root', default=os.environ.get('SDL_ROOT',
            str(Path(__file__).resolve().parents[4] / 'sdl')), help='SDL checkout')
        if name != 'validate':
            action.add_argument('--build-dir', help='artifact directory (default: TARGET/build/subsystem)')
        if name in ('run', 'test'):
            action.add_argument('--case', help='one named case (default: all for test, nominal for run)')
            action.add_argument('--workers', type=int, default=1, help='parallel simulation workers')
            action.add_argument('--microsteps', action='store_true', help='settle activating feedback in logical microsteps')
            action.add_argument('--max-microsteps', type=int, help='maximum logical stages per virtual timestamp')
        if name == 'run':
            action.add_argument('--mode', choices=('simulated',), default='simulated',
                                help='harness execution mode (currently simulated)')
        if name == 'test':
            action.add_argument('--report', choices=('junit',), help='export a CI report')
            action.add_argument('--output', help='JUnit report path (requires --report junit)')


def initialize(target):
    root = Path(target).resolve()
    if root.exists() and (not root.is_dir() or any(root.iterdir())):
        raise ValueError('init requires a new or empty directory')
    root.mkdir(parents=True, exist_ok=True)
    (root / 'components').mkdir()
    (root / 'schemas').mkdir()
    (root / '.gitignore').write_text('/build/\n__pycache__/\n')
    write_json(root / 'subsystem.json', dict(version=1, model='model.json', deployment='deployment.json',
        interface=dict(inputs={'in': {'to': 'processor.input'}}, outputs={'out': {'from': 'processor.out'}})))
    (root / 'schemas/value.sdl').write_text('message Value { 1: required fl64 value; }\n')
    write_json(root / 'model.json', dict(version=2, schemas='schemas/value.sdl',
        components={'processor': dict(inputs={'input': dict(root='Value', activates={'message': ['out']})},
                                      outputs={'out': {'root': 'Value'}})}, subscriptions=[]))
    write_json(root / 'deployment.json', dict(version=1, components={
        'processor': {'container': {'language': 'python', 'handler': 'components/processor.py'}}}))
    (root / 'components/processor.py').write_text(
        'def on_input(ctx, input):\n    ctx.publish_out(input.message)\n')
    write_json(root / 'tests/harness.json', dict(version=1, profiles={'standalone': {}}, cases={
        'nominal': dict(scenario='nominal/scenario.json', expectations='nominal/expectations.json')}))
    write_json(root / 'tests/nominal/scenario.json', dict(version=1, duration=.5,
        events=[dict(at=at, input='in', value={'value': value})
                for at, value in ((0, 20), (.1, 25), (.2, 90))]))
    write_json(root / 'tests/nominal/expectations.json', dict(version=1, assertions=[
        dict(output='out', count=3, sequence=[{'value': v} for v in (20, 25, 90)])]))
    return root


def run(args):
    try:
        if args.subsystem_action == 'init':
            print(initialize(args.target))
            return 0
        if args.subsystem_action == 'validate':
            validate_harness(args.target, args.sdl_root, integration_profile=args.integration_profile,
                             execution_profile=args.profile)
            print(json.dumps({'valid': True}))
            return 0
        manifest, _, _, harness, selected = settings(args.target, args.integration_profile)
        destination = Path(args.build_dir).resolve() if args.build_dir else manifest.parent / 'build/subsystem'
        if args.subsystem_action == 'build':
            assembly = assemble(args.target, args.integration_profile, destination, args.sdl_root,
                                execution_profile=args.profile)
            print(assembly['plan_path'])
            return 0
        if args.workers <= 0:
            raise ValueError('workers must be positive')
        from ose.execution.simulation.options import microstep_limit
        microstep_limit(args.microsteps, args.max_microsteps)
        if args.subsystem_action == 'test' and bool(args.report) != bool(args.output):
            raise ValueError('--report junit and --output must be supplied together')
        available = selected.get('cases', list(harness['cases']))
        cases = [args.case or (available[0] if available else 'nominal')] if args.subsystem_action == 'run' else (
            [args.case] if args.case else available)
        if not cases:
            raise ValueError('no test cases selected')
        if any(name not in available for name in cases):
            raise ValueError('unknown test case')
        results = []
        for name in cases:
            try:
                result = execute_case(args.target, args.integration_profile, name, destination, args.sdl_root,
                    workers=args.workers, verify=args.subsystem_action == 'test', execution_profile=args.profile,
                    microsteps=args.microsteps, max_microsteps=args.max_microsteps)
            except (ValueError, OSError, subprocess.CalledProcessError) as error:
                result = dict(case=name, integration_profile=args.integration_profile,
                              status='configuration_error', error=str(error))
            results.append(result)
        write_json(destination / 'results.json', dict(results=results))
        if args.subsystem_action == 'test' and args.report:
            junit(results, args.output)
        print(json.dumps(dict(results=results), indent=2))
        if any(r['status'] == 'configuration_error' for r in results):
            return 2
        if any(r['status'] == 'error' for r in results):
            return 3
        return 1 if any(r['status'] == 'failed' for r in results) else 0
    except (ValueError, OSError, subprocess.CalledProcessError) as error:
        print(f'ose subsystem: {error}', file=sys.stderr)
        return 2
