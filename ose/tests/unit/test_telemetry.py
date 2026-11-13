"""Timing boundaries, bounded sampling, Pull observations and process reuse."""
import asyncio
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from ose.delivery.buffers import Buffer
from ose.delivery.policies import SubscriptionPolicy
from ose.delivery.stores import MemoryStore
from ose.execution.activation import InputScheduler
from ose.execution.input_ipc import InputServices, request_data
from ose.execution.realtime import RealtimeRuntime
from ose.execution.telemetry import Durations, ExecutionTelemetry, ProcessResources
from ose.top import Observer, render
from tests.unit.test_top import status


NODE = dict(inputs=dict(clock=dict(kind='timer', timer=dict(mode='periodic', delay=.01)),
                        push=dict(root='Value'), pull=dict(root='Value', mode='pull')), outputs={})


class TelemetryTests(unittest.TestCase):
    def setUp(self):
        self.now = 0.
        self.telemetry = ExecutionTelemetry(NODE, lambda: self.now)

    def test_percentiles_are_bounded_and_expire(self):
        values = Durations(lambda: self.now)
        for i in range(1000):
            values.add(i)
        sample = values.snapshot()
        self.assertEqual(sample['samples'], 256)
        self.assertEqual(sample['p95_ms'], 987)
        self.assertEqual(sample['max_ms'], 999)
        self.now = 6
        self.assertIsNone(values.snapshot()['p95_ms'])
        self.assertEqual(values.snapshot()['samples'], 0)

    def scheduler(self):
        buffers = {port: Buffer(SubscriptionPolicy(), MemoryStore()) for port in ('push', 'pull')}
        self.addCleanup(lambda: [buffer.store.close() for buffer in buffers.values()])
        scheduler = InputScheduler(NODE, buffers)
        scheduler.start()
        return scheduler

    def test_timer_lag_and_coalescing_use_deadline_not_callback_duration(self):
        scheduler = self.scheduler()
        reference = scheduler.select(35_000_000)
        self.telemetry.activate(reference)
        snapshot = self.telemetry.snapshot()['timers']['clock']
        self.assertEqual(snapshot['p95_ms'], 25)
        self.assertEqual(snapshot['coalesced'], 2)
        scheduler.complete(reference, 100_000_000)
        self.assertEqual(self.telemetry.snapshot()['timers']['clock']['max_ms'], 25)
        self.now = 6
        snapshot = self.telemetry.snapshot()['timers']['clock']
        self.assertIsNone(snapshot['max_ms'])
        self.assertEqual(snapshot['coalesced'], 2)

    def test_push_and_pull_measure_local_wait_and_do_not_count_repeated_gets(self):
        scheduler = self.scheduler()
        record = dict(id='one', epoch='source', sequence=1, topic='source.out',
                      body=b'payload', catalogue='SDL2|Value{1:uint32:count;}')
        scheduler.admit('push', record, 1_000_000)
        scheduler.admit('pull', record, 2_000_000)
        reference = scheduler.select(5_000_000)
        self.telemetry.activate(reference)
        services = InputServices(scheduler, lambda: 8_000_000, on_read=self.telemetry.read)
        for operation in ('get', 'get', 'pull'):
            self.assertEqual(services.handle(request_data(operation, 'pull'))[:1], b'\x01')
        sample = self.telemetry.snapshot()['input_wait']
        self.assertEqual(sample['push']['p95_ms'], 4)
        self.assertEqual(sample['pull']['p95_ms'], 6)
        self.assertEqual(sample['pull']['samples'], 1)
        # A failed callback still observed the data, but does not consume it.
        scheduler.complete(reference, 8_000_000, success=False)
        self.assertEqual(scheduler.buffers['pull'].store.size(), 1)
        reference = scheduler.select(9_000_000)
        self.telemetry.activate(reference)
        services.handle(request_data('get', 'pull'))
        self.assertEqual(self.telemetry.snapshot()['input_wait']['pull']['samples'], 2)
        scheduler.complete(reference, 9_000_000)

    def test_publication_in_progress_and_failed_attempts_are_visible(self):
        with self.assertRaises(RuntimeError), self.telemetry.publishing():
            self.now = .05
            self.assertAlmostEqual(self.telemetry.snapshot()['publication_wait']['current_ms'], 50)
            raise RuntimeError('transport failed')
        sample = self.telemetry.snapshot()['publication_wait']
        self.assertIsNone(sample['current_ms'])
        self.assertAlmostEqual(sample['p95_ms'], 50)
        self.assertEqual(sample['calls'], 1)

    def test_observer_reports_supervision_publication_wait_and_staleness(self):
        data = status()
        data['component_states'] = {'sink': dict(enabled=False, active=False, pending=False)}
        data['components']['sink'].update(running=False, **self.telemetry.snapshot())
        data['components']['sink']['resources'] = {'runtime': {'rss_bytes': 4096}}
        self.assertEqual(Observer().sample(data)['components'][0]['state'], 'DISABLED')
        for desired, state in ((True, 'ENABLING'), (False, 'DISABLING')):
            data['component_states']['sink'].update(enabled=desired, pending=True)
            self.assertEqual(Observer().sample(data)['components'][0]['state'], state)
        data['component_states']['sink'].update(enabled=True, active=True, pending=False)
        data['components']['sink'].update(running=True)
        data['components']['sink']['publication_wait']['current_ms'] = 100
        row = Observer().sample(data)['components'][0]
        self.assertEqual(row['state'], 'WAIT PUB')
        self.assertIsNotNone(row['resources']['runtime']['rss_bytes'])
        data['observation']['components']['sink']['report_age_ms'] = 4000
        row = Observer().sample(data)['components'][0]
        self.assertEqual(row['state'], 'STALE')
        for key in ('resources', 'timers', 'input_wait', 'publication_wait'):
            self.assertEqual(row[key], {})

    def test_timer_counter_is_recent_and_resets_on_generation_change(self):
        data, observer = status(), Observer()
        data['components']['sink'].update(**self.telemetry.snapshot())
        observer.sample(data, now=0)
        data['components']['sink']['timers']['clock']['coalesced'] = 3
        row = observer.sample(data, now=1)['components'][0]
        self.assertEqual(row['timers']['clock']['recent_coalesced'], 3)
        data['observation']['components']['sink']['generation'] = 'replacement'
        row = observer.sample(data, now=2)['components'][0]
        self.assertIsNone(row['timers']['clock']['recent_coalesced'])

    def test_render_keeps_process_details_accessible_on_small_terminal(self):
        data = status()
        data['components']['sink'].update(**self.telemetry.snapshot())
        row = Observer().sample(data)['components'][0]
        report = Observer().sample(data)
        report['components'][0]['timers'] = {str(i): row['timers']['clock'] for i in range(30)}
        first = render(report, details=True, width=120, height=15)
        last = render(report, details=True, width=120, height=15, detail_offset=100)
        self.assertIn('CALL/s', first)
        self.assertNotIn('TICK/s', first)
        self.assertIn('CPU %', first)
        self.assertIn('runtime PID', last)
        self.assertIn('q: quit', last)
        self.assertLessEqual(len(last.splitlines()), 15)


class ResourceTests(unittest.TestCase):
    def test_cpu_rss_disappearing_process_and_pid_reuse(self):
        with tempfile.TemporaryDirectory() as directory:
            now = [0.]
            resources = ProcessResources(lambda: now[0], directory)
            resources.ticks, resources.page_size = 100, 4096
            path = Path(directory) / '42'
            path.mkdir()
            def write(ticks, start=7):
                fields = ['0'] * 22
                fields[0], fields[11], fields[12], fields[19], fields[21] = 'S', str(ticks), '50', str(start), '10'
                (path / 'stat').write_text('42 (name with ) spaces) ' + ' '.join(fields))
            write(100)
            self.assertIsNone(resources.snapshot(42)['component']['cpu_percent'])
            now[0] = 2
            write(250)
            sample = resources.snapshot(42)['component']
            self.assertEqual(sample['cpu_percent'], 75)
            self.assertEqual(sample['rss_bytes'], 40960)
            now[0] = 3
            write(1, start=8)
            self.assertIsNone(resources.snapshot(42)['component']['cpu_percent'])
            (path / 'stat').unlink()
            self.assertIsNone(resources.snapshot(42)['component']['rss_bytes'])
            self.assertEqual(resources.previous, {})
            write(20, start=8)
            self.assertIsNone(resources.snapshot(42)['component']['cpu_percent'])

    def test_proc_unavailable_is_unknown_not_zero(self):
        with tempfile.TemporaryDirectory() as directory:
            sample = ProcessResources(proc=directory).snapshot(123)['component']
        self.assertIsNone(sample['cpu_percent'])
        self.assertIsNone(sample['rss_bytes'])


class PublicationBoundaryTests(unittest.IsolatedAsyncioTestCase):
    async def test_runtime_reports_pending_publication_and_clears_it_on_cancellation(self):
        runtime = RealtimeRuntime()
        runtime.node, runtime.name, runtime.metrics, runtime.tick_index = NODE, 'source', {}, 0
        runtime.telemetry = ExecutionTelemetry(NODE)
        runtime.reports = SimpleNamespace(mark=lambda: None)
        runtime.input_recording = SimpleNamespace(observe=lambda *args: None)
        runtime.process = runtime.input_services = runtime.codec = runtime.timings = None
        entered, resume = asyncio.Event(), asyncio.Event()
        runtime._input_now = lambda: 0
        async def publish(*args, **kwargs):
            entered.set()
            await resume.wait()
        runtime.publish = publish
        reference = SimpleNamespace(port='clock', event='timeout', time_ns=10_000_000, due_ns=10_000_000, elapsed=1, record=None)
        async def calculate(*args, **kwargs):
            return [('out', b'value', None)]
        with patch('ose.execution.realtime.calculate', calculate):
            task = asyncio.create_task(runtime._callback(reference))
            await asyncio.wait_for(entered.wait(), 1)
            self.assertIsNotNone(runtime.telemetry.snapshot()['publication_wait']['current_ms'])
            self.assertEqual(runtime.metrics.get('completed_ticks', 0), 0)
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task
        self.assertIsNone(runtime.telemetry.snapshot()['publication_wait']['current_ms'])
        self.assertEqual(runtime.telemetry.snapshot()['publication_wait']['calls'], 1)
