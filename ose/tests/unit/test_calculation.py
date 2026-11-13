"""Callback transactions shared by both real-time endpoints and simulation."""
import asyncio
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from ose.delivery.buffers import Buffer
from ose.delivery.policies import SubscriptionPolicy
from ose.delivery.stores import MemoryStore
from ose.execution.activation import InputScheduler
from ose.execution.process import calculate


class CalculationTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.node = dict(execution_model=2,
                         inputs=dict(trigger=dict(root='Value'), inbox=dict(root='Value', mode='pull')),
                         outputs=dict(out=dict(catalogue='schema', root='Value')))
        self.buffers = {port: Buffer(SubscriptionPolicy(capacity=1), MemoryStore())
                        for port in self.node['inputs']}
        self.addCleanup(lambda: [buffer.store.close() for buffer in self.buffers.values()])
        self.scheduler = InputScheduler(self.node, self.buffers)
        self.scheduler.start()
        for index, port in enumerate(self.buffers, 1):
            self.scheduler.admit(port, dict(id=f'source:{index}', epoch='source', sequence=index,
                                           topic='source.out', body=b'value'), 0)
        self.reference = self.scheduler.select(0)
        self.services = SimpleNamespace(scheduler=self.scheduler)
        def validate(catalogue, root, envelope):
            if envelope == b'invalid':
                raise ValueError('invalid SDL')
        self.codec = SimpleNamespace(wrap=lambda catalogue, payload: payload, validate_only=validate)

    async def calculate(self, events, **options):
        with patch('ose.execution.process.input_events', events):
            return await calculate(None, self.node, self.reference, 1, self.services,
                                   self.codec, lambda: 10, **options)

    def assert_retained(self):
        self.assertIsNone(self.scheduler.active)
        for buffer in self.buffers.values():
            self.assertEqual(buffer.store.size(), 1)
            self.assertEqual(buffer.reserved, set())

    async def test_success_commits_withdrawals_before_returning_staged_outputs(self):
        async def events(*args, **kwargs):
            self.scheduler.inbox('inbox').pull()
            yield 'out', b'value', 0
            self.assertEqual(self.buffers['inbox'].store.size(), 1)
        outputs = await self.calculate(events)
        self.assertEqual(outputs, [('out', b'value', 0)])
        self.assertTrue(all(buffer.store.size() == 0 for buffer in self.buffers.values()))
        self.assertEqual(self.scheduler.time_ns, 10)

    async def test_failed_callback_discards_staged_outputs_and_keeps_withdrawals(self):
        async def events(*args, **kwargs):
            self.scheduler.inbox('inbox').pull()
            yield 'out', b'value', None
            raise EOFError('missing FINISHED')
        with self.assertRaisesRegex(EOFError, 'missing FINISHED'):
            await self.calculate(events)
        self.assert_retained()

    async def test_invalid_second_output_cancels_the_whole_callback(self):
        async def events(*args, **kwargs):
            self.scheduler.inbox('inbox').pull()
            yield 'out', b'value', None
            yield 'out', b'invalid', None
        with self.assertRaisesRegex(ValueError, 'invalid SDL'):
            await self.calculate(events)
        self.assert_retained()

    async def test_output_budget_counts_all_publications_and_releases_the_iterator(self):
        closed = []
        async def events(*args, **kwargs):
            try:
                self.scheduler.inbox('inbox').pull()
                yield 'out', b'value', None
                yield 'out', b'value', None
            finally:
                closed.append(True)
        with self.assertRaisesRegex(ValueError, 'callback outputs exceed'):
            await self.calculate(events, limit=39)  # Each publication costs 5 + 3 + 12 bytes.
        self.assertEqual(closed, [True])
        self.assert_retained()

    async def test_cancelled_callback_closes_the_iterator_and_keeps_inputs(self):
        entered, closed = asyncio.Event(), asyncio.Event()
        async def events(*args, **kwargs):
            try:
                self.scheduler.inbox('inbox').pull()
                yield 'out', b'value', None
                entered.set()
                await asyncio.Event().wait()
            finally:
                closed.set()
        task = asyncio.create_task(self.calculate(events))
        await asyncio.wait_for(entered.wait(), 1)
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertTrue(closed.is_set())
        self.assert_retained()
