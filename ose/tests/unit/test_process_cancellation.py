"""Component startup must preserve cancellation when READY arrives concurrently."""
import asyncio
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

from ose.execution.process import start_component


class ComponentStartupTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.process = SimpleNamespace(stdin=object(), stdout=object())
        self.node = dict(command=['component'], cwd='.')
        self.stopped = AsyncMock()
        for target, replacement in (
            ('asyncio.create_subprocess_exec', AsyncMock(return_value=self.process)),
            ('ose.execution.process.ipc.send', AsyncMock()),
            ('ose.execution.process.stop_component', self.stopped),
        ):
            patcher = patch(target, replacement)
            patcher.start()
            self.addCleanup(patcher.stop)

    async def test_ready_and_cancellation_in_the_same_turn_stops_component(self):
        async def ready(reader):
            asyncio.get_running_loop().call_soon(startup.cancel)
            return 'R', b'\x01'

        with patch('ose.execution.process.ipc.receive', side_effect=ready):
            startup = asyncio.create_task(start_component(self.node))
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
            startup = asyncio.create_task(start_component(self.node))
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
                await start_component(self.node)
        self.assertTrue(finished.is_set())
        self.stopped.assert_awaited_once_with(self.process)

    async def test_invalid_ready_stops_component_and_preserves_error(self):
        with patch('ose.execution.process.ipc.receive', AsyncMock(return_value=('R', b'invalid'))):
            with self.assertRaisesRegex(ValueError, 'supported READY'):
                await start_component(self.node)
        self.stopped.assert_awaited_once_with(self.process)

    async def test_success_returns_process_and_negotiated_tick_capability(self):
        for body, capable in ((b'', False), (b'\x01', True), (b'\x03', True)):
            with self.subTest(body=body), \
                    patch('ose.execution.process.ipc.receive', AsyncMock(return_value=('R', body))):
                self.assertEqual(await start_component(self.node), (self.process, capable))
        self.stopped.assert_not_awaited()
