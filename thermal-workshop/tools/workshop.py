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


def prepare(load='nominal', analyzer=None, native_dir=None):
    profile = PROFILES[load]
    model = json.loads((ROOT / 'model.json').read_text())
    deployment = json.loads((ROOT / 'deployment.json').read_text())
    model['schemas'] = str(ROOT / 'schemas')
    workload = {key: value for key, value in profile.items() if key != 'period'}
    model['shared_parameters']['workload']['initial'] = dict(workload, label=load)
    for name, component in model['components'].items():
        if name.startswith('plant_'):
            component['inputs']['clock']['timer']['delay'] = profile['period']
        elif name == 'fusion':
            component['inputs']['clock']['timer']['delay'] = profile['period'] * 2
        elif name == 'historian':
            component['inputs']['clock']['timer']['delay'] = profile['period'] * 5
    for name, component in deployment['components'].items():
        if analyzer is not None and name.startswith('analyzer_'):
            suffix = 'c' if analyzer == 'c' else 'py'
            component['container'] = dict(language=analyzer, handler=f'components/analyzer.{suffix}')
        component['container']['handler'] = str(ROOT / component['container']['handler'])
        component['cwd'] = str(ROOT)
        if native_dir is not None:
            environment = component.setdefault('env', {})
            inherited = environment.get('PYTHONPATH', os.environ.get('PYTHONPATH', ''))
            environment['PYTHONPATH'] = os.pathsep.join(filter(None, (str(Path(native_dir).resolve()), inherited)))
    return model, deployment


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
    for name, value in zip(('model', 'deployment'), prepare(args.load, args.analyzer, native_dir)):
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
