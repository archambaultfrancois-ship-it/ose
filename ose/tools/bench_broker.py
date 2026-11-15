"""Measure broker CPU and useful traffic with an isolated real-time plan.

The plan's component executables are reused; endpoints and status files live in
a temporary directory. No application instance remains running after each run.
"""
import argparse
import asyncio
import copy
import cProfile
import hashlib
import json
import math
import os
from pathlib import Path
import platform
import tempfile
import time
from unittest.mock import patch

from ose.execution import supervisor
from ose.execution.plan import read_plan
from ose.transport.tcp import Client


async def sample(plan, duration, profile, broker_cpu=None):
    result = {}
    profiler = cProfile.Profile() if profile else None
    poll = None
    original = supervisor.Broker
    affinity = os.sched_getaffinity(0) if broker_cpu is not None else None

    def pin():
        if affinity is None:
            return
        # Startup already spawned the components with the original affinity.
        # Keep only this benchmark's descendants off the broker's chosen CPU.
        parents = {}
        for entry in Path('/proc').iterdir():
            if entry.name.isdigit():
                try:
                    parents[int(entry.name)] = int((entry/'stat').read_text().rsplit(')',1)[1].split()[1])
                except (OSError, ValueError):
                    pass
        descendants = {os.getpid()}
        while True:
            expanded = descendants | {pid for pid, parent in parents.items() if parent in descendants}
            if expanded == descendants:
                break
            descendants = expanded
        for pid in descendants - {os.getpid()}:
            try:
                os.sched_setaffinity(pid, affinity - {broker_cpu})
            except ProcessLookupError:
                pass
        os.sched_setaffinity(0, {broker_cpu})

    async def observe(broker):
        client = await Client.connect('127.0.0.1', broker.port)
        try:
            while True:
                await client.request('status')
                await asyncio.sleep(.5)
        finally:
            await client.close()

    class MeasuredBroker(original):
        @property
        def enabled(self):
            return original.enabled.fget(self)

        @enabled.setter
        def enabled(self, value):
            nonlocal poll
            if value:
                pin()
                self.bench_started = time.perf_counter(), time.process_time()
                if profiler:
                    profiler.enable()
                poll = asyncio.create_task(observe(self))
            elif hasattr(self, 'bench_started'):
                if profiler:
                    profiler.disable()
                wall, cpu = self.bench_started
                result.update(wall_s=time.perf_counter()-wall, broker_cpu_s=time.process_time()-cpu,
                    publications=self.sequence, topics=dict(self.topic_counts),
                    deliveries={key: dict(engine.metrics) for key, engine in self.engines.items()},
                    admissions={key: dict(engine.buffer.metrics) for key, engine in self.engines.items()})
                result['publications_per_s'] = self.sequence / result['wall_s']
                result['sent_per_s'] = sum(e.metrics['sent'] for e in self.engines.values()) / result['wall_s']
                result['broker_cpu_per_publication_us'] = result['broker_cpu_s'] * 1e6 / max(1,self.sequence)
                del self.bench_started
                if poll:
                    poll.cancel()
            original.enabled.fset(self, value)

    with tempfile.TemporaryDirectory(prefix='ose-broker-bench-') as temporary:
        isolated = copy.deepcopy(plan)
        isolated['build_dir'] = temporary
        path = Path(temporary) / 'plan.json'
        path.write_text(json.dumps(isolated))
        try:
            with patch.object(supervisor, 'Broker', MeasuredBroker):
                await supervisor.run(path, duration)
        finally:
            if poll:
                poll.cancel()
                await asyncio.gather(poll, return_exceptions=True)
            if profiler:
                profiler.disable()
                profiler.dump_stats(str(profile))
            if affinity is not None:
                os.sched_setaffinity(0, affinity)
    return result


async def main(args):
    plan = read_plan(args.plan)
    output = dict(python=platform.python_version(), duration=args.duration, broker_cpu=args.broker_cpu,
                  plan_sha256=hashlib.sha256(args.plan.read_bytes()).hexdigest(), rows=[])
    args.output.parent.mkdir(parents=True, exist_ok=True)
    for _ in range(args.repeats):
        row = await sample(plan, args.duration, args.profile, args.broker_cpu)
        output['rows'].append(row)
        args.output.write_text(json.dumps(output, indent=2)+'\n')
        print(json.dumps({k:v for k,v in row.items() if k not in ('topics','deliveries','admissions')}), flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--plan', type=Path, required=True)
    parser.add_argument('--duration', type=float, default=8.)
    parser.add_argument('--repeats', type=int, default=3)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--profile', type=Path)
    parser.add_argument('--broker-cpu', type=int, help='pin broker to this CPU and its descendants to the other allowed CPUs (Linux)')
    args = parser.parse_args()
    if (not math.isfinite(args.duration) or args.duration <= 0 or args.repeats <= 0
            or args.profile and args.repeats != 1):
        parser.error('positive duration/repeats required; profile requires one run')
    if args.broker_cpu is not None:
        if not hasattr(os, 'sched_getaffinity'):
            parser.error('--broker-cpu requires Linux CPU affinity')
        if args.broker_cpu not in os.sched_getaffinity(0) or len(os.sched_getaffinity(0)) < 2:
            parser.error('select an allowed broker CPU and keep at least one other CPU for components')
    asyncio.run(main(args))
