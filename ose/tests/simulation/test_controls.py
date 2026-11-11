"""Real CH/2 processes under temporal, instant, microstep and breakpoint controls."""
import asyncio
import contextlib
import io
import json
import os
from pathlib import Path
import struct
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from ose.codec.sdl2 import SDL2
from ose.delivery.policies import SubscriptionPolicy
from ose.execution.simulation import Simulation
from ose.recording.control import ReplayControl

CAT = 'SDL2|Value{1:uint64:sequence;2:fl64:value;}'
HANDLER = '''import os
from ose.execution.input_process import InputSession
from ose.execution.input_ipc import publication_data
from ose.codec.sdl2 import SDL2
s = InputSession()
codec = SDL2(os.environ['SDL_ROOT'])
for activation in s.activations():
    if activation.record is not None:
        _, payload = codec.api.unpack_envelope(activation.record['body'])
        s.send('P', publication_data('out', payload, activation.record.get('app_id')))
'''


@unittest.skipUnless(os.environ.get('SDL_ROOT'), 'set SDL_ROOT')
class SimulationControlTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.codec = SDL2(os.environ['SDL_ROOT'])
        handler = self.root / 'handler.py'
        handler.write_text(HANDLER)
        desc = dict(root='Value', catalogue=CAT)
        common = dict(execution_model=2, parameters={}, cwd=str(self.root), env={},
                      command=[sys.executable, str(handler)])
        self.plan = dict(version=2, sdl_root=str(self.codec.root), build_dir=str(self.root),
            external_topics={'external.signal': desc}, components={
                'sink': dict(common, inputs={'input': dict(desc, activates={'message': ['out']})}, outputs={'out': desc}),
                'timer': dict(common, inputs={'clock': dict(kind='timer', timer=dict(mode='periodic', delay=10e-9),
                                                           activates={'timeout': []})}, outputs={})},
            subscriptions=[dict(id='external', **{'from': 'external.signal', 'from_topic': 'external.signal',
                'to': 'sink.input', 'policy': SubscriptionPolicy.resolve({}).to_dict()})])
        self.plan_path = self.root / 'plan.json'
        self.plan_path.write_text(json.dumps(self.plan))
        events = []
        for index, at in enumerate((0, 20, 20, 35)):
            envelope = self.root / f'{index}.s2ps'
            envelope.write_bytes(self.codec.wrap(CAT, b'\x01' + struct.pack('>Qd', index, float(index))))
            events.append(dict(at=at / 1e9, topic='external.signal', envelope=str(envelope)))
        self.scenario = self.root / 'scenario.json'
        self.scenario.write_text(json.dumps(dict(version=1, events=events)))

    async def execute(self, commands=None, *, scenario=True, name='trace', microsteps=True):
        output = io.StringIO()
        control = (ReplayControl('simulated', replay=False, commands=commands, output=output,
                                 microsteps=microsteps) if commands is not None else None)
        trace = self.root / (name + '.jsonl')
        engine = Simulation(self.plan, 50e-9, scenario=self.scenario if scenario else None,
                            trace=trace, microsteps=microsteps, controller=control)
        summary = await asyncio.wait_for(engine.execute(), 5)
        statuses = [json.loads(line) for line in output.getvalue().splitlines() if line.startswith('{')]
        return summary, statuses, trace.read_bytes()

    async def test_instant_and_microsteps_stop_before_callbacks_and_preserve_the_trace(self):
        _, _, continuous = await self.execute(name='continuous')
        result, statuses, stepped = await self.execute(
            ['step', 'microstep', 'step 2', 'microstep', 'step 5ns', 'step 1s'])
        self.assertEqual([s['position_ns'] for s in statuses], [10, 10, 30, 30, 35, 50])
        self.assertEqual([s['ticks'] for s in statuses], [1, 2, 5, 6, 6, 8])
        self.assertEqual([s['microstep'] for s in statuses[:4]], [0, 1, 0, 1])
        self.assertEqual(statuses[2]['last_command']['completed_units'], 2)
        self.assertEqual(result['stop_reason'], 'duration')
        self.assertEqual(continuous, stepped)

    async def test_breakpoint_wins_over_a_temporal_step_and_precedes_all_inputs_at_that_time(self):
        _, _, continuous = await self.execute(name='continuous')
        result, statuses, stepped = await self.execute(
            ['break-time 20ns', 'step 20ns', 'microstep', 'microstep', 'step 1s'])
        self.assertEqual(statuses[1]['position_ns'], 20)
        self.assertEqual(statuses[1]['published'], 1)
        self.assertEqual(statuses[1]['ticks'], 2)
        self.assertEqual(statuses[1]['last_command']['stop_reason'], 'breakpoint')
        self.assertEqual(statuses[1]['breakpoints_ns'], [])
        self.assertEqual(statuses[2]['published'], 3)
        self.assertEqual(statuses[2]['ticks'], 4)
        self.assertEqual(statuses[3]['ticks'], 5)
        self.assertEqual(result['last_breakpoint_ns'], 20)
        self.assertEqual(continuous, stepped)

    async def test_zero_and_empty_interval_breakpoints(self):
        _, statuses, _ = await self.execute(
            ['break-time 0ns', 'step 1s', 'break-time 5ns', 'step', 'stop'])
        self.assertEqual(statuses[1]['position_ns'], 0)
        self.assertEqual(statuses[1]['published'], 0)
        self.assertEqual(statuses[3]['position_ns'], 5)
        self.assertEqual(statuses[3]['published'], 1)
        self.assertEqual(statuses[3]['ticks'], 1)
        self.assertEqual(statuses[3]['last_command']['stop_reason'], 'breakpoint')

    async def test_microstep_does_not_jump_to_a_future_timer(self):
        result, statuses, _ = await self.execute(
            ['microstep 3', 'step', 'microstep 3', 'stop'], scenario=False)
        self.assertEqual([s['position_ns'] for s in statuses[:3]], [0, 10, 10])
        self.assertEqual(statuses[0]['last_command']['stop_reason'], 'blocked')
        self.assertEqual(statuses[0]['last_command']['completed_units'], 0)
        self.assertEqual(statuses[2]['last_command']['stop_reason'], 'blocked')
        self.assertEqual(statuses[2]['last_command']['completed_units'], 1)
        self.assertEqual(result['ticks'], 1)

    async def test_instant_steps_also_work_without_microsteps(self):
        result, statuses, _ = await self.execute(['step 2', 'stop'], microsteps=False)
        self.assertEqual(statuses[0]['position_ns'], 20)
        self.assertEqual(statuses[0]['ticks'], 2)
        self.assertEqual(statuses[0]['published'], 3)
        self.assertEqual(result['time_ns'], 20)

    async def test_run_cli_supports_control_files_and_rejects_controls_in_realtime(self):
        script = self.root / 'commands.txt'
        script.write_text('break-time 20ns\nstep 1s\nmicrostep\nstep 1s\n')
        result = await asyncio.to_thread(subprocess.run,
            [sys.executable, '-m', 'ose.cli', 'run', '--plan', str(self.plan_path), '--mode', 'simulated',
             '--duration', '0.000000050', '--scenario', str(self.scenario), '--microsteps',
             '--command-file', str(script)], capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)['last_breakpoint_ns'], 20)
        self.assertEqual(json.loads(result.stdout)['ticks'], 8)
        from ose.cli import main
        for options in (['--interactive'], ['--command', 'step'], ['--command', 'step 1s'],
                        ['--command-file', '/missing/control-file']):
            with self.subTest(options=options), patch('ose.cli.read_plan') as read, \
                    contextlib.redirect_stderr(io.StringIO()) as output:
                self.assertEqual(main(['run', '--mode', 'realtime', *options]), 1)
                self.assertIn('require simulated mode for run', output.getvalue())
                read.assert_not_called()

    async def test_run_rejects_message_steps_and_microsteps_without_the_launch_option(self):
        for commands, microsteps in ((['step-messages'], True), (['microstep'], False)):
            with self.subTest(commands=commands), self.assertRaises(ValueError):
                await self.execute(commands, microsteps=microsteps)

    async def test_breakpoint_can_extend_a_quiescent_tail_and_the_horizon_still_wins(self):
        self.plan['components']['timer']['inputs']['clock']['timer']['mode'] = 'one_shot'
        result, statuses, _ = await self.execute(
            ['break-time 30ns', 'step 1s', 'resume', 'wait 1s'], scenario=False)
        self.assertEqual(statuses[1]['position_ns'], 30)
        self.assertEqual(statuses[1]['ticks'], 1)
        self.assertEqual(statuses[1]['last_command']['stop_reason'], 'breakpoint')
        self.assertEqual(result['stop_reason'], 'quiescent')
        result, statuses, _ = await self.execute(['break-time 50ns', 'step 1s'], scenario=False)
        self.assertEqual(result['stop_reason'], 'duration')
        self.assertEqual(result['time_ns'], 50)
        self.assertIsNone(result['last_breakpoint_ns'])
        self.assertEqual(result['breakpoints_ns'], [50])

    async def test_a_breakpoint_cannot_rewind_an_already_admitted_timestamp(self):
        with self.assertRaisesRegex(ValueError, 'events have started'):
            await self.execute(['step 2', 'break-time 20ns'])
