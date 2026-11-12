"""Loopback development cluster, using the workers of a compiled distributed plan."""
import asyncio
import json
from pathlib import Path
import signal

from ose.execution.launch import Processes, start_sync
from ose.execution.plan import read_plan


class LocalCluster:
    def __init__(self, plan_path, *, port=0, logs=None, lease_seconds=2, params_sync=None, disabled=()):
        from ose.execution.distributed import check_plan
        self.path = Path(plan_path).resolve()
        self.plan = read_plan(self.path)
        check_plan(self.plan)
        self.port, self.lease_seconds = port, lease_seconds
        self.logs = Path(logs or Path(self.plan['build_dir']) / 'distributed-logs').resolve()
        from ose.execution.components import disabled_components
        self.disabled = disabled_components(self.plan, disabled)
        self.params_sync = params_sync
        self.processes, self.streams = Processes(), []

    async def launch(self, name, arguments):
        stream = (self.logs / (name + '.log')).open('ab')
        self.streams.append(stream)
        return await self.processes.start(name, arguments, stream=stream)

    async def worker(self, name):
        return await self.launch(name, ['worker', '--plan', self.path, '--node', name,
                                       '--port', self.port, '--insecure'])

    async def restart(self, name):
        if name not in self.plan['components']:
            raise ValueError(f'unknown worker: {name}')
        self.processes.expected_exits.add(name)
        try:
            await self.processes.stop(self.processes.children[name])
            await self.worker(name)
        finally:
            self.processes.expected_exits.discard(name)

    def pids(self):
        return {name: process.pid for name, process in self.processes.children.items()}

    async def status(self):
        from ose.transport.secure import connect
        client = await connect('127.0.0.1', self.port, 1024 * 1024, None, 'coordinator')
        try:
            result, _ = await client.request('status')
            return {key: value for key, value in result.items() if key not in ('op', 'request', 'content')}
        finally:
            await client.close()

    async def __aenter__(self):
        try:
            self.logs.mkdir(parents=True, exist_ok=True)
            for name in ('coordinator', 'params-sync', *self.plan['components']):
                (self.logs / (name + '.log')).write_bytes(b'')
            await self.launch('coordinator', ['serve', '--plan', self.path, '--port', self.port,
                                            '--insecure', '--lease-seconds', self.lease_seconds,
                                            *[arg for name in sorted(self.disabled) for arg in ('--disable-component', name)]])
            # Read this child's startup announcement, not a stale endpoint or a
            # probed free port. Port zero is bound atomically by the coordinator.
            async def endpoint():
                while True:
                    self.processes.check()
                    lines = (self.logs / 'coordinator.log').read_text().splitlines()
                    for line in lines:
                        try:
                            message = json.loads(line)
                        except ValueError:
                            continue
                        if message.get('running'):
                            self.port = message['port']
                            return
                    await asyncio.sleep(.05)
            await asyncio.wait_for(endpoint(), 20)
            for name in self.plan['components']:
                await self.worker(name)
            async def ready():
                while True:
                    self.processes.check()
                    if (await self.status())['enabled']:
                        return
                    await asyncio.sleep(.1)
            await asyncio.wait_for(ready(), 20)
            if self.params_sync:
                stream = (self.logs / 'params-sync.log').open('ab')
                self.streams.append(stream)
                await start_sync(self.processes, plan_path=self.path, port=self.port,
                                 distributed=True, stream=stream, **self.params_sync)
            self.initial_pids = self.pids()
            (Path(self.plan['build_dir']) / 'distributed-endpoint.json').write_text(json.dumps(
                dict(host='127.0.0.1', port=self.port, distributed=True, insecure=True)))
            return self
        except BaseException:
            await self.__aexit__(None, None, None)
            raise

    async def __aexit__(self, *exc):
        await self.processes.close()
        for stream in self.streams:
            stream.close()


async def run(plan_path, duration=300, *, port=0, logs=None, lease_seconds=2, params_sync=None, exercise=None, disabled=()):
    """Run until duration/interrupt/failure; an optional application exercise may replace the timer."""
    loop, task = asyncio.get_running_loop(), asyncio.current_task()
    previous = {sig: signal.getsignal(sig) for sig in (signal.SIGINT, signal.SIGTERM)}
    for sig in previous:
        loop.add_signal_handler(sig, task.cancel)
    try:
        async with LocalCluster(plan_path, port=port, logs=logs, lease_seconds=lease_seconds,
                                params_sync=params_sync, disabled=disabled) as cluster:
            monitor = asyncio.create_task(cluster.processes.monitor())
            running = asyncio.create_task(exercise(cluster) if exercise else asyncio.sleep(duration))
            try:
                done, _ = await asyncio.wait([monitor, running], return_when=asyncio.FIRST_COMPLETED)
                for completed in done:
                    completed.result()
                status = await asyncio.wait_for(cluster.status(), 5)
                build = Path(cluster.plan['build_dir'])
                for name, document in (('distributed-status', status), ('distributed-processes',
                        dict(initial=cluster.initial_pids, final=cluster.pids()))):
                    (build / (name + '.json')).write_text(json.dumps(document, indent=2) + '\n')
                print(json.dumps(status, indent=2), flush=True)
                return status
            finally:
                monitor.cancel()
                running.cancel()
                await asyncio.gather(monitor, running, return_exceptions=True)
    except asyncio.TimeoutError as error:
        raise RuntimeError('local cluster startup/status timeout; inspect distributed logs') from error
    except asyncio.CancelledError:
        raise KeyboardInterrupt from None
    finally:
        for sig, handler in previous.items():
            loop.remove_signal_handler(sig)
            signal.signal(sig, handler)
