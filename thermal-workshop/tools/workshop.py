#!/usr/bin/env python3
"""Prepare a bounded load profile, then use the public OSE CLI."""
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
PROFILES = {
    'nominal': dict(period=.1, min_samples=64, max_samples=256, burst=1, cpu_iterations=0, log_every=20),
    'busy': dict(period=.02, min_samples=256, max_samples=2048, burst=2, cpu_iterations=1000, log_every=100),
    'stress': dict(period=.01, min_samples=1024, max_samples=8192, burst=4, cpu_iterations=5000, log_every=200),
}


def prepare(load='nominal', analyzer=None, native_dir=None, *, directory):
    """Materialize a profiled hierarchy, retaining composition for the tools.

    The returned application documents belong in directory. Definitions and
    their models/deployments are copied there; authored sources stay untouched.
    """
    profile = PROFILES[load]
    directory = Path(directory).resolve()
    workload = {key: value for key, value in profile.items() if key != 'period'}
    definitions = {}

    def write(source, value):
        target = directory / source.relative_to(ROOT)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(value, indent=2) + '\n')
        return target

    def prepare_definition(source):
        source = source.resolve()
        if source.is_dir():
            source /= 'subsystem.json'
        if source not in definitions:
            # Register before descending; the compiler reports reference cycles.
            definitions[source] = directory / source.relative_to(ROOT)
            definition = json.loads(source.read_text())
            model_path = (source.parent / definition['model']).resolve()
            deployment_path = (source.parent / definition['deployment']).resolve()
            model, deployment = prepare_scope(model_path, deployment_path)
            write(model_path, model)
            write(deployment_path, deployment)
            write(source, definition)
        return definitions[source]

    def prepare_scope(model_path, deployment_path):
        model = json.loads(model_path.read_text())
        deployment = json.loads(deployment_path.read_text())
        model['schemas'] = str((model_path.parent / model['schemas']).resolve())
        if 'workload' in model.get('shared_parameters', {}):
            model['shared_parameters']['workload']['initial'] = dict(workload, label=load)
        for name, component in model['components'].items():
            multiplier = {'plant': 1, 'fusion': 2, 'historian': 5}.get(name)
            if multiplier is not None:
                component['inputs']['clock']['timer']['delay'] = profile['period'] * multiplier
        for name, component in deployment['components'].items():
            if analyzer is not None and name == 'analyzer':
                suffix = 'c' if analyzer == 'c' else 'py'
                component['container'] = dict(language=analyzer, handler=str(ROOT / f'components/analyzer.{suffix}'))
            handler = component['container']['handler']
            component['container']['handler'] = str((deployment_path.parent / handler).resolve())
            component['cwd'] = str((deployment_path.parent / component.get('cwd', '.')).resolve())
            if native_dir is not None:
                environment = component.setdefault('env', {})
                inherited = environment.get('PYTHONPATH', os.environ.get('PYTHONPATH', ''))
                environment['PYTHONPATH'] = os.pathsep.join(filter(None, (str(Path(native_dir).resolve()), inherited)))
        for instance in model.get('subsystems', {}).values():
            instance['source'] = str(prepare_definition(model_path.parent / instance['source']))
        return model, deployment

    return prepare_scope(ROOT / 'model.json', ROOT / 'deployment.json')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=('validate', 'build', 'run', 'simulate'))
    parser.add_argument('--load', choices=tuple(PROFILES), default='nominal')
    parser.add_argument('--duration', type=float, default=10)
    parser.add_argument('--workers', type=int, default=1)
    parser.add_argument('--analyzer', choices=('python', 'c'), help='override the deployed analyzer implementation')
    parser.add_argument('--build-dir', type=Path, default=ROOT / 'build')
    args = parser.parse_args()
    build_dir = args.build_dir.resolve()
    native_dir = build_dir / 'native'
    if args.action != 'validate':
        from build_native import build as build_native
        build_native(native_dir)
    prepared = build_dir / 'prepared'
    prepared.mkdir(parents=True, exist_ok=True)
    for name, value in zip(('model', 'deployment'), prepare(args.load, args.analyzer, native_dir, directory=prepared)):
        (prepared / (name + '.json')).write_text(json.dumps(value, indent=2) + '\n')
    environment = dict(os.environ)
    framework = ROOT.parent
    environment['SDL_ROOT'] = str(framework / 'sdl')
    environment['PYTHONPATH'] = os.pathsep.join([str(framework / 'ose/src'), str(framework / 'build/python-deps'),
        environment.get('PYTHONPATH', '')]).rstrip(os.pathsep)
    command = [sys.executable, '-m', 'ose.cli']
    build = ['validate' if args.action == 'validate' else 'build', '--model', str(prepared / 'model.json'),
        '--deployment', str(prepared / 'deployment.json'), '--build-dir', str(build_dir)]
    subprocess.run(command + build, cwd=ROOT, env=environment, check=True)
    if args.action in ('run', 'simulate'):
        options = ['run', '--plan', str(build_dir / 'plan.json'), '--duration', str(args.duration)]
        if args.action == 'simulate':
            options += ['--mode', 'simulated', '--workers', str(args.workers)]
        subprocess.run(command + options, cwd=ROOT, env=environment, check=True)


if __name__ == '__main__':
    main()
