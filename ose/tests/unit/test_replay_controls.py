"""Replay budgets, exclusive temporal boundaries and acknowledged admissions."""
import asyncio
import io
import hashlib
import json
import time
import tempfile
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from ose.execution.simulation.scenario import Event
from ose.recording.control import ReplayControl, duration_ns
from ose.recording.live_replay import play


class ReplayControlTests(unittest.IsolatedAsyncioTestCase):
    async def playback(self, times, commands, **options):
        output, seen = io.StringIO(), []
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        body = Path(temporary.name) / 'body'
        body.write_bytes(b'body')
        events = [Event(at, index, 'external', body, hashlib.sha256(b'body').hexdigest())
                  for index, at in enumerate(times)]
        async def publish(topic, body):
            seen.append(body)
            return {'subscription': 'accepted'}
        result = await asyncio.wait_for(play(events, publish, commands=commands, output=output, **options), 3)
        statuses = [json.loads(line) for line in output.getvalue().splitlines() if line.startswith('{')]
        return result, statuses, seen

    async def test_message_steps_skip_delays_and_temporal_steps_exclude_the_endpoint(self):
        result, statuses, seen = await self.playback([0, 100000000, 200000000, 1000000000],
            ['step-messages 2', 'step-time 100ms', 'step-messages', 'stop'], speed=0)
        self.assertEqual(len(seen), 3)
        self.assertEqual([s['position_ns'] for s in statuses[:3]], [100000000, 200000000, 200000000])
        self.assertEqual([s['last_command']['messages'] for s in statuses[:3]], [2, 0, 1])
        self.assertTrue(all(s['paused'] for s in statuses))
        self.assertEqual(statuses[0]['last_command']['delivery_results'], {'accepted': 2})
        self.assertEqual(result['stop_reason'], 'stopped')

    async def test_empty_interval_advances_without_publishing_and_startup_is_paused(self):
        _, statuses, seen = await self.playback([0, 100000000],
            ['wait 1ms', 'status', 'step-time 50ms', 'step-time 25ms', 'stop'], speed=0)
        self.assertEqual(statuses[0]['published'], 0)
        self.assertEqual([s['position_ns'] for s in statuses[2:4]], [50000000, 75000000])
        self.assertEqual(len(seen), 1)

    async def test_temporal_step_obeys_realtime_speed(self):
        start = time.monotonic()
        _, statuses, seen = await self.playback([0, 100000000], ['step-time 40ms', 'stop'], speed=2)
        self.assertGreaterEqual(time.monotonic() - start, .018)
        self.assertEqual(statuses[0]['position_ns'], 40000000)
        self.assertEqual(len(seen), 1)

    async def test_message_steps_cross_loops_and_stop_at_duration(self):
        result, statuses, seen = await self.playback([0, 10], ['step-messages 20'], loops=0, duration=.000000025)
        self.assertEqual(len(seen), 5)
        self.assertEqual(result['stop_reason'], 'duration')
        self.assertEqual(statuses[0]['last_command']['messages'], 5)

    async def test_time_steps_clip_at_horizon_and_end_of_capture(self):
        for options, expected, count in (({'duration': .05}, 50000000, 1), ({}, 100000001, 2)):
            result, statuses, seen = await self.playback([0, 100000000], ['step-time 1s'], speed=0, **options)
            self.assertEqual(len(seen), count)
            self.assertEqual(statuses[0]['position_ns'], expected)
            self.assertIn(result['stop_reason'], ('complete', 'duration'))

    async def test_temporal_step_can_pause_before_a_later_terminal_boundary(self):
        result, statuses, seen = await self.playback([0, 100000000],
            ['step-time 10ms', 'stop'], speed=0, duration=.05)
        self.assertEqual(len(seen), 1)
        self.assertEqual(statuses[0]['position_ns'], 10000000)
        self.assertEqual(result['stop_reason'], 'stopped')

    async def test_commands_wait_for_admission_and_report_refusals(self):
        output = io.StringIO()
        async def publish(topic, body):
            await asyncio.sleep(.01)
            return {'a': 'full'}
        event = SimpleNamespace(at=0, topic='external', read=lambda: b'body')
        result = await play([event, event], publish, commands=['step-messages', 'status', 'stop'], output=output)
        statuses = [json.loads(line) for line in output.getvalue().splitlines()]
        self.assertEqual(result['refused'], 1)
        self.assertEqual(statuses[0]['last_command']['delivery_results'], {'full': 1})
        self.assertEqual(statuses[1]['published'], 1)

    async def test_interactive_errors_allow_recovery_and_eof_stops(self):
        lines = iter(['step 0\n', 'step-time nan\n', 'status\n', ''])
        class Input:
            def __init__(self, **options):
                pass
            async def readline(self, prompt):
                return next(lines)
            def close(self):
                pass
        output = io.StringIO()
        with patch('ose.recording.control.CommandInput', Input):
            session = ReplayControl('simulated', interactive=True, output=output)
            await session.controls()
        self.assertTrue(session.stopped)
        self.assertIn('positive integer', output.getvalue())
        self.assertIn('duration requires', output.getvalue())

    async def test_invalid_batch_commands_fail_without_publishing(self):
        for command in ('step 0', 'step -1', 'step 1.5', 'step 1 2', 'step-time',
                        'step-time 0', 'step-time -1s', 'step-time inf', 'step-time 1e100s'):
            with self.subTest(command=command), self.assertRaises(ValueError):
                await self.playback([0], [command])
        session = ReplayControl('simulated', commands=[])
        with self.assertRaisesRegex(ValueError, 'speed requires realtime'):
            await session.execute('speed 2')

    async def test_instant_steps_group_timestamps(self):
        result, statuses, seen = await self.playback([0, 0, 10, 10, 20, 20, 30],
            ['step', 'step 1', 'stop'], speed=.001)
        self.assertEqual(len(seen), 6)
        self.assertEqual([s['position_ns'] for s in statuses[:2]], [10, 20])
        self.assertEqual([s['last_command']['completed_units'] for s in statuses[:2]], [1, 1])
        self.assertEqual([s['published'] for s in statuses[:2]], [4, 6])
        self.assertEqual(result['stop_reason'], 'stopped')

    async def test_unified_temporal_step_keeps_an_exclusive_endpoint(self):
        _, statuses, seen = await self.playback([0, 10, 20], ['step 10ns', 'stop'], speed=0)
        self.assertEqual(len(seen), 1)
        self.assertEqual(statuses[0]['position_ns'], 10)
        self.assertEqual(statuses[0]['last_command']['kind'], 'time')

    async def test_breakpoints_interrupt_steps_before_admission_and_disarm(self):
        result, statuses, seen = await self.playback([0, 10, 20],
            ['break-time 10ns', 'step 10ns', 'step-messages', 'stop'], speed=0)
        hit = statuses[1]
        self.assertEqual(hit['position_ns'], 10)
        self.assertEqual(hit['published'], 1)
        self.assertEqual(hit['last_command']['stop_reason'], 'breakpoint')
        self.assertEqual(hit['breakpoints_ns'], [])
        self.assertEqual(statuses[2]['published'], 2)
        self.assertEqual(len(seen), 2)
        self.assertEqual(result['last_breakpoint_ns'], 10)

    async def test_breakpoint_preempts_instant_and_message_budgets(self):
        for command in ('step 2', 'step-messages 3'):
            with self.subTest(command=command):
                _, statuses, seen = await self.playback([0, 10, 20],
                    ['break-time 5ns', command, 'stop'], speed=0)
                self.assertEqual(len(seen), 1)
                self.assertEqual(statuses[1]['position_ns'], 5)
                self.assertEqual(statuses[1]['last_command']['stop_reason'], 'breakpoint')

    async def test_multiple_breakpoints_zero_time_and_removal(self):
        _, statuses, seen = await self.playback([0, 10, 20],
            ['break-time 0ns', 'break-time 5ns', 'break-time 10ns', 'clear-break-time 5ns',
             'breakpoints', 'step 1s', 'clear-break-time all', 'step-messages', 'stop'], speed=0)
        self.assertEqual(statuses[4]['breakpoints_ns'], [0, 10])
        self.assertEqual(statuses[5]['published'], 0)
        self.assertEqual(statuses[5]['position_ns'], 0)
        self.assertEqual(statuses[6]['breakpoints_ns'], [])
        self.assertEqual(len(seen), 1)

    async def test_resume_notifies_a_breakpoint_without_a_pending_step(self):
        _, statuses, seen = await self.playback([0, 10000000, 20000000],
            ['break-time 5ms', 'resume', 'wait 1ms', 'status', 'stop'], speed=0)
        hits = [s for s in statuses if s.get('event') == 'breakpoint']
        self.assertEqual(len(hits), 1)
        self.assertEqual(hits[0]['position_ns'], 5000000)
        self.assertEqual(len(seen), 1)

    async def test_mode_restrictions_and_past_breakpoints(self):
        with self.assertRaisesRegex(ValueError, 'simulated mode for run'):
            ReplayControl('realtime', replay=False)
        session = ReplayControl('simulated', replay=False, commands=[], output=io.StringIO())
        session.position = lambda: 10
        for command, error in [('step-messages', 'requires replay'), ('microstep', '--microsteps'),
                               ('break-time 9ns', 'already passed'),
                               ('clear-break-time 10ns', 'no breakpoint')]:
            with self.subTest(command=command), self.assertRaisesRegex(ValueError, error):
                await session.execute(command)
        for command in ('step 1.0', 'step 1e2', 'microstep', 'break-time -1s'):
            with self.subTest(command=command), self.assertRaises(ValueError):
                await self.playback([0], [command])

    async def test_breakpoint_time_is_global_across_replay_loops(self):
        _, statuses, seen = await self.playback([0, 10],
            ['break-time 15ns', 'step 1s', 'step-messages', 'stop'], loops=0, speed=0)
        self.assertEqual(statuses[1]['position_ns'], 15)
        self.assertEqual(statuses[1]['published'], 3)  # 0, 10, 11; next is 21.
        self.assertEqual(statuses[1]['completed_loops'], 1)
        self.assertEqual(statuses[2]['position_ns'], 21)
        self.assertEqual(len(seen), 4)

    def test_duration_units_are_exact_and_bounded(self):
        for text, expected in [('1', 1000000000), ('100ms', 100000000), ('1us', 1000),
                               ('1ns', 1), ('2min', 120000000000), ('.5ns', 1)]:
            self.assertEqual(duration_ns(text), expected)
