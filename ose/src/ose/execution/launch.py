"""Managed CLI processes and parameter synchronization for application launchers."""
import asyncio
import importlib.util
import os
from pathlib import Path
import sys

from ose.execution.plan import read_plan


def require_sync(directory):
    if not directory:
        raise ValueError('--params-sync directory must not be empty')
    if importlib.util.find_spec('inotify_simple') is None:
        raise ValueError('install the optional watcher with pip install "ose[sync]" (checkout: make setup)')


class Processes:
    """Own children, detect unexpected exits, and reap them on every exit path."""
    def __init__(self):
        self.children = {}
        self.expected_exits = set()

    async def start(self, name, arguments, *, stream=None, env=None):
        environment = dict(os.environ if env is None else env)
        source = str(Path(__file__).resolve().parents[2])
        environment['PYTHONPATH'] = source + os.pathsep + environment.get('PYTHONPATH', '')
        process = await asyncio.create_subprocess_exec(sys.executable, '-m', 'ose.cli', *map(str, arguments),
            env=environment, stdout=stream, stderr=stream, start_new_session=True)
        self.children[name] = process
        return process

    def check(self):
        for name, process in self.children.items():
            if name not in self.expected_exits and process.returncode is not None:
                raise RuntimeError(f'{name} exited with status {process.returncode}; stopping the application')

    async def monitor(self):
        while True:
            self.check()
            await asyncio.sleep(.05)

    @staticmethod
    async def stop(process):
        if process.returncode is None:
            try:
                process.terminate()
            except ProcessLookupError:
                pass
        try:
            await asyncio.wait_for(process.wait(), 6)
        except asyncio.TimeoutError:
            try:
                process.kill()
            except ProcessLookupError:
                pass
            await process.wait()

    async def close(self):
        # Stop concurrently so the shutdown bound does not grow with cluster size.
        await asyncio.gather(*(self.stop(process) for process in reversed(list(self.children.values()))))


async def start_sync(processes, directory, plan_path, port, *, client='operator', initial='files',
                     token_env='OSE_PARAMETER_TOKEN', token=None, distributed=False, stream=None):
    require_sync(directory)
    plan = read_plan(plan_path)
    if client not in plan.get('parameter_clients', {}):
        raise ValueError(f'parameter sync client is not declared in the plan: {client}')
    environment = dict(os.environ)
    if token is not None:
        environment[token_env] = token
    if token_env == 'OSE_PARAMETER_TOKEN' and token_env not in environment:
        token = plan['parameter_clients'][client].get('token')
        if token:
            environment[token_env] = token
    arguments = ['parameter', 'sync', '--dir', directory, '--client', client, '--initial', initial,
                 '--token-env', token_env, '--plan', plan_path, '--host', '127.0.0.1', '--port', port]
    if distributed:
        arguments += ['--distributed', '--insecure']
    return await processes.start('params-sync', arguments, stream=stream, env=environment)


def add_sync_options(parser):
    parser.add_argument('--params-sync', nargs='?', const='parameters', metavar='DIR',
                        help='manage parameter directory sync alongside execution (real-time only; default: parameters)')
    parser.add_argument('--params-client', default=os.environ.get('OSE_PARAMETER_CLIENT', 'operator'),
                        help='parameter sync client identity (default: OSE_PARAMETER_CLIENT or operator)')
    parser.add_argument('--params-initial', choices=('files', 'app'), default='files',
                        help='initial sync source (default: files; restoration may follow component on_start)')
    parser.add_argument('--params-token-env', default='OSE_PARAMETER_TOKEN',
                        help='environment variable containing the sync client token (default: OSE_PARAMETER_TOKEN)')


def sync_options(args):
    if args.params_sync is None:
        return {}
    require_sync(args.params_sync)
    return dict(directory=str(Path(args.params_sync).resolve()), client=args.params_client,
                initial=args.params_initial, token_env=args.params_token_env)
