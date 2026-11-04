"""Event-driven control retains bounded state, prompt logs and cancellation."""
import asyncio
import unittest

from ose.execution.logs import ComponentLogs, log_body
from ose.execution.observation import ReportSchedule, ObservationDemand
from ose.recording.component import RemoteRecording
from ose.recording.taps import TapHub


class ReportTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.now = 0.
        self.schedule = ReportSchedule(interval=10, heartbeat=20, clock=lambda: self.now)
        self.logs = ComponentLogs(on_change=self.schedule.wake)

    async def test_coalescing_logs_and_idle_heartbeat(self):
        self.assertIs(await self.schedule.next(self.logs), True)
        for unused in range(1000):
            self.schedule.mark()
        pending = asyncio.create_task(self.schedule.next(self.logs))
        await asyncio.sleep(0)
        self.assertFalse(pending.done())
        self.logs.emit(log_body('ERROR', 'immediate'))
        self.assertIs(await asyncio.wait_for(pending, 1), False)
        self.assertEqual(self.logs.drain()[0]['message'], 'immediate')
        self.now = 10
        self.assertIs(await self.schedule.next(self.logs), True)
        pending = asyncio.create_task(self.schedule.next(self.logs))
        await asyncio.sleep(0)
        self.assertFalse(pending.done())
        self.now = 30
        self.schedule.wake()
        self.assertIs(await asyncio.wait_for(pending, 1), True)
        pending = asyncio.create_task(self.schedule.next(self.logs))
        await asyncio.sleep(0)
        self.schedule.close()
        self.assertIsNone(await asyncio.wait_for(pending, 1))

    async def test_log_bursts_drain_in_bounded_batches_without_full_snapshots(self):
        await self.schedule.next(self.logs)
        for n in range(200):
            self.logs.emit(log_body('INFO', f'entry {n}'))
        self.assertEqual(len(self.logs.pending), 128)
        self.assertEqual(self.logs.dropped, 72)
        received = []
        while self.logs.pending:
            self.assertIs(await self.schedule.next(self.logs), False)
            batch = self.logs.drain()
            self.assertLessEqual(len(batch), 32)
            received.extend(batch)
        self.assertEqual(len(received), 128)
        self.assertEqual(self.logs.bytes, 0)

    async def test_changes_while_a_report_is_in_flight_are_not_lost(self):
        await self.schedule.next(self.logs)
        self.schedule.mark()  # Mutation while the caller is sending the snapshot.
        self.now = 10
        self.assertIs(await self.schedule.next(self.logs), True)
        self.assertFalse(self.schedule.dirty)

    async def test_quiet_presence_logs_and_immediate_demand_then_expiration(self):
        self.schedule.configure(dict(version=0, seconds=0))
        self.assertIs(await self.schedule.next(self.logs), True)  # Initial diagnostic.
        self.now = 20
        self.schedule.mark()
        self.assertIs(await self.schedule.next(self.logs), False)
        self.assertTrue(self.schedule.dirty)
        waiting = asyncio.create_task(self.schedule.next(self.logs))
        await asyncio.sleep(0)
        self.logs.emit(log_body('ERROR', 'quiet log'))
        self.assertIs(await asyncio.wait_for(waiting, 1), False)
        self.assertEqual(self.logs.drain()[0]['message'], 'quiet log')
        waiting = asyncio.create_task(self.schedule.next(self.logs))
        await asyncio.sleep(0)
        self.schedule.event({'event': 'reporting', 'reporting': dict(version=1, seconds=5)}, b'')
        self.assertIs(await asyncio.wait_for(waiting, 1), True)
        # Renewals do not bypass coalescing; obsolete replies cannot end a new lease.
        self.schedule.configure(dict(version=1, seconds=5))
        self.assertFalse(self.schedule.dirty)
        self.schedule.configure(dict(version=0, seconds=0))
        self.assertEqual(self.schedule.version, 1)
        self.schedule.mark()
        self.now = 40
        self.assertIs(await self.schedule.next(self.logs), False)
        self.schedule.configure(dict(version=2, seconds=5))
        self.assertIs(await self.schedule.next(self.logs), True)

    async def test_reporting_validation_and_legacy_negotiation(self):
        self.schedule.configure(None)
        self.assertFalse(self.schedule.managed)
        for state in (False, [], {}, dict(version=True, seconds=0),
                      dict(version=0, seconds=float('nan')), dict(version=0, seconds=-1)):
            with self.subTest(state=state), self.assertRaises(ValueError):
                self.schedule.configure(state)


class DemandTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.hub = ObservationDemand(lease=.05, timeout=.03)
        self.addAsyncCleanup(self.hub.close)
        self.sent = asyncio.Queue()
        class Peer:
            async def send(peer, header):
                await self.sent.put((peer, header))
        self.peer = Peer()
        self.hub.register(self.peer)

    async def test_concurrent_readers_share_refresh_and_require_current_epoch(self):
        pending = [asyncio.create_task(self.hub.refresh([self.peer])) for _ in range(10)]
        peer, header = await self.sent.get()
        version = header['reporting']['version']
        self.hub.received(peer, version - 1)
        await asyncio.sleep(0)
        self.assertTrue(all(not task.done() for task in pending))
        self.hub.received(peer, version)
        await asyncio.gather(*pending)
        self.assertTrue(self.sent.empty())
        await self.hub.refresh([self.peer])
        self.assertTrue(self.sent.empty())
        await asyncio.sleep(.06)
        waiting = asyncio.create_task(self.hub.refresh([self.peer]))
        _, header = await self.sent.get()
        self.assertGreater(header['reporting']['version'], version)
        self.hub.forget(self.peer)
        await asyncio.wait_for(waiting, .02)

    async def test_timeout_and_close_do_not_strand_readers(self):
        await asyncio.wait_for(self.hub.refresh([self.peer]), .2)
        self.assertEqual(self.hub.members[self.peer], -1)
        await asyncio.sleep(.06)
        waiting = asyncio.create_task(self.hub.refresh([self.peer]))
        await asyncio.sleep(0)
        await self.hub.close()
        await asyncio.wait_for(waiting, .1)
        self.assertFalse(self.hub.notifications)


class ActivationTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.point = dict(topic='source.record.dsp.value', root='Value', catalogue='SDL2|Value{}',
                          generation='generation', available=True)
        self.hub = TapHub(lambda: {self.point['topic']: self.point}, 1024 * 1024)
        self.addCleanup(self.hub.close)

    async def open(self, peer):
        result, _ = await self.hub.request(peer, dict(op='recording_open',
            points={self.point['topic']: self.point['generation']}))
        return result['session']

    async def test_watches_wake_on_open_stop_disconnect_and_resynchronize(self):
        version, active = await self.hub.activation()
        self.assertEqual(active, set())
        waiting = asyncio.create_task(self.hub.activation(version))
        await asyncio.sleep(0)
        self.assertFalse(waiting.done())
        first, second = object(), object()
        token = await self.open(first)
        newer, active = await asyncio.wait_for(waiting, 1)
        self.assertGreater(newer, version)
        self.assertEqual(active, {self.point['topic']})
        await self.open(second)
        await self.hub.request(first, dict(op='recording_stop', session=token))
        # A second recorder keeps the same point enabled.
        latest, active = await self.hub.activation(version)
        self.assertEqual(active, {self.point['topic']})
        waiting = asyncio.create_task(self.hub.activation(latest))
        await asyncio.sleep(0)
        self.hub.disconnected(second)
        newest, active = await asyncio.wait_for(waiting, 1)
        self.assertGreater(newest, latest)
        self.assertEqual(active, set())
        self.assertEqual((await self.hub.activation())[0], newest)

    async def test_invalid_versions_and_watch_cancellation(self):
        for version in (True, -1, '1'):
            with self.subTest(version=version), self.assertRaises(ValueError):
                await self.hub.activation(version)
        version, _ = await self.hub.activation()
        waiting = asyncio.create_task(self.hub.activation(version))
        await asyncio.sleep(0)
        waiting.cancel()
        await asyncio.gather(waiting, return_exceptions=True)
        await self.open(object())
        self.assertGreater((await self.hub.activation(version))[0], version)

    async def test_remote_watch_waits_instead_of_polling_and_restarts_with_full_state(self):
        replies = asyncio.Queue()
        calls = []
        async def request(op, **arguments):
            calls.append((op, arguments))
            return await replies.get(), b''
        remote = RemoteRecording(None, request)
        await replies.put({'version': 0, 'active': []})
        task = asyncio.create_task(remote.poll())
        try:
            await asyncio.sleep(.12)  # More than two former 50 ms polling periods.
            self.assertEqual(len(calls), 2)
            self.assertEqual(calls[1][1], {'watch': True, 'after': 0})
            await replies.put({'version': 1, 'active': ['dsp.value']})
            while len(calls) < 3:
                await asyncio.sleep(0)
            self.assertEqual(remote.active, {'dsp.value'})
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            await replies.put({'version': 0, 'active': []})  # A replacement broker.
            task = asyncio.create_task(remote.poll())
            while len(calls) < 5:
                await asyncio.sleep(0)
            self.assertIsNone(calls[3][1]['after'])
            self.assertEqual(remote.active, set())
        finally:
            remote.close()
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)

    async def test_close_stops_a_watch_even_if_cancellation_is_absorbed(self):
        started = asyncio.Event()
        async def request(op, **arguments):
            started.set()
            try:
                await asyncio.Future()
            except asyncio.CancelledError:
                return {'version': 0, 'active': ['dsp.value']}, b''
        remote = RemoteRecording(None, request)
        task = asyncio.create_task(remote.poll())
        await started.wait()
        remote.close()
        task.cancel()
        await asyncio.wait_for(task, 1)
        self.assertEqual(remote.active, set())
