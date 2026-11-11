"""Component startup must preserve cancellation when READY arrives concurrently."""
import asyncio
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from ose.execution.process import start_component


class ComponentStartupTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.process = SimpleNamespace(stdin=object(), stdout=object())
        self.node = dict(execution_model=2, command=['component'], cwd='.')
        self.inputs = object()
        self.stopped = AsyncMock()
        for target, replacement in (
            ('asyncio.create_subprocess_exec', AsyncMock(return_value=self.process)),
            ('ose.execution.process.socket.socketpair', MagicMock(return_value=(MagicMock(), MagicMock()))),
            ('ose.execution.process.ipc.send', AsyncMock()),
            ('ose.execution.process.asyncio.open_connection', AsyncMock(return_value=(object(), object()))),
            ('ose.execution.process.stop_component', self.stopped),
        ):
            patcher = patch(target, replacement)
            patcher.start()
            self.addCleanup(patcher.stop)

    async def test_ready_and_cancellation_in_the_same_turn_stops_component(self):
        async def ready(reader):
            asyncio.get_running_loop().call_soon(startup.cancel)
            return 'R', b'\x02'

        with patch('ose.execution.process.ipc.receive', side_effect=ready):
            startup = asyncio.create_task(start_component(self.node, inputs=self.inputs))
            with self.assertRaises(asyncio.CancelledError):
                await startup
        self.stopped.assert_awaited_once_with(self.process)

    async def test_cancellation_finishes_readiness_reader_before_stopping_component(self):
        entered, finished = asyncio.Event(), asyncio.Event()

        async def ready(reader):
            entered.set()
            try:
                await asyncio.Future()
            finally:
                finished.set()

        async def stopped(process):
            self.assertTrue(finished.is_set())

        self.stopped.side_effect = stopped
        with patch('ose.execution.process.ipc.receive', side_effect=ready):
            startup = asyncio.create_task(start_component(self.node, inputs=self.inputs))
            await asyncio.wait_for(entered.wait(), 1)
            startup.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await startup
        self.stopped.assert_awaited_once_with(self.process)

    async def test_readiness_timeout_stops_component_and_finishes_reader(self):
        finished = asyncio.Event()
        wait = asyncio.wait

        async def ready(reader):
            try:
                await asyncio.Future()
            finally:
                finished.set()

        async def short_wait(tasks, *, timeout):
            self.assertEqual(timeout, 15)
            return await wait(tasks, timeout=.01)

        with patch('ose.execution.process.ipc.receive', side_effect=ready), \
                patch('ose.execution.process.asyncio.wait', side_effect=short_wait):
            with self.assertRaises(asyncio.TimeoutError):
                await start_component(self.node, inputs=self.inputs)
        self.assertTrue(finished.is_set())
        self.stopped.assert_awaited_once_with(self.process)

    async def test_invalid_ready_stops_component_and_preserves_error(self):
        for body in (b'', b'\x01', b'\x03', b'invalid'):
            with self.subTest(body=body), patch('ose.execution.process.ipc.receive', AsyncMock(return_value=('R', body))):
                with self.assertRaisesRegex(ValueError, 'requires a CH/2 component SDK'):
                    await start_component(self.node, inputs=self.inputs)
        self.assertEqual(self.stopped.await_count, 4)

    async def test_success_returns_the_ch_two_process(self):
        with patch('ose.execution.process.ipc.receive', AsyncMock(return_value=('R', b'\x02'))):
            self.assertIs(await start_component(self.node, inputs=self.inputs), self.process)
        self.stopped.assert_not_awaited()
