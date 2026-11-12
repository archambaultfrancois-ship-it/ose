"""Managed launches preserve credential precedence and force bounded shutdown."""
import asyncio
import os
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock, patch

from ose.execution.launch import Processes, start_sync


class ManagedProcessTests(unittest.IsolatedAsyncioTestCase):
    async def test_sync_uses_plan_token_unless_environment_is_explicit(self):
        plan = {'parameter_clients': {'maintenance': {'token': 'test-plan'}}}
        for explicit, expected in (({}, 'test-plan'), ({'OSE_PARAMETER_TOKEN': 'test-explicit'}, 'test-explicit'),
                                   ({'OSE_PARAMETER_TOKEN': ''}, '')):
            with self.subTest(explicit=explicit), patch.dict(os.environ, explicit, clear=True), \
                    patch('ose.execution.launch.require_sync'), patch('ose.execution.launch.read_plan', return_value=plan):
                processes = SimpleNamespace(start=AsyncMock())
                await start_sync(processes, 'parameter files', 'plan.json', 1234, client='maintenance')
                self.assertEqual(processes.start.call_args.kwargs['env']['OSE_PARAMETER_TOKEN'], expected)
                self.assertIn('parameter files', processes.start.call_args.args[1])

    async def test_custom_token_environment_never_uses_plan_fallback(self):
        with patch.dict(os.environ, {}, clear=True), patch('ose.execution.launch.require_sync'), \
                patch('ose.execution.launch.read_plan', return_value={'parameter_clients': {'operator': {'token': 'test-plan'}}}):
            processes = SimpleNamespace(start=AsyncMock())
            await start_sync(processes, 'parameters', 'plan.json', 1234, token_env='CUSTOM')
        self.assertNotIn('CUSTOM', processes.start.call_args.kwargs['env'])
        self.assertNotIn('OSE_PARAMETER_TOKEN', processes.start.call_args.kwargs['env'])

    async def test_unresponsive_child_is_killed_and_reaped(self):
        process = SimpleNamespace(returncode=None, terminate=Mock(), kill=Mock(),
                                  wait=AsyncMock(side_effect=[asyncio.TimeoutError(), 0]))
        await Processes.stop(process)
        process.terminate.assert_called_once()
        process.kill.assert_called_once()
        self.assertEqual(process.wait.await_count, 2)

    async def test_any_exit_including_zero_fails_supervision(self):
        processes = Processes()
        for status in (0, 1, -15):
            processes.children = {'params-sync': SimpleNamespace(returncode=status)}
            with self.subTest(status=status), self.assertRaisesRegex(RuntimeError, 'params-sync exited'):
                await processes.monitor()
