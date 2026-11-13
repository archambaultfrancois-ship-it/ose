"""Worker authorization is bounded by renewal request time, including suspend."""
import asyncio
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from ose.execution.distributed import Worker


class WorkerLeaseTests(unittest.IsolatedAsyncioTestCase):
    def worker(self):
        return SimpleNamespace(closing=False, lease_seconds=4, epoch='current', last_contact=-1,
                               directory={'enabled': False}, activity=asyncio.Event(), engines={})

    async def test_delayed_reply_cannot_revive_authorization(self):
        worker, now = self.worker(), [0.]
        async def control(op):
            now[0] = 4.  # Includes a suspended host or a delayed control reply.
            return dict(epoch='current', enabled=True), None
        worker.control = control
        with patch('ose.execution.distributed.lease_clock', lambda: now[0]):
            with self.assertRaisesRegex(RuntimeError, 'authorization expired'):
                await Worker._heartbeat(worker)
        self.assertEqual(worker.last_contact, -1)
        self.assertFalse(worker.directory['enabled'])
        self.assertFalse(worker.activity.is_set())

    async def test_round_trip_uses_request_start_and_does_not_shift_renewal_period(self):
        for latency, expected_sleep in ((.25, .75), (1.5, 0)):
            with self.subTest(latency=latency):
                worker, now, sleeps = self.worker(), [10.], []
                async def control(op):
                    now[0] += latency
                    return dict(epoch='current', enabled=True), None
                async def sleep(delay):
                    sleeps.append(delay)
                    worker.closing = True
                worker.control = control
                with patch('ose.execution.distributed.lease_clock', lambda: now[0]), \
                        patch('ose.execution.distributed.asyncio.sleep', sleep):
                    await Worker._heartbeat(worker)
                self.assertEqual(worker.last_contact, 10.)
                self.assertEqual(sleeps, [expected_sleep])
                self.assertTrue(worker.activity.is_set())

    async def test_watchdog_rejects_expired_authorization_without_waiting_for_reply(self):
        worker, now = self.worker(), [0.]
        worker.last_contact = 0
        async def sleep(delay):
            now[0] = 4.
        with patch('ose.execution.distributed.lease_clock', lambda: now[0]), \
                patch('ose.execution.distributed.asyncio.sleep', sleep):
            with self.assertRaisesRegex(RuntimeError, 'authorization expired'):
                await Worker._watchdog(worker)
