"""Microsteps are a launch policy; models retain their ordinary v2 schema."""
import copy
import contextlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import AsyncMock, patch

from ose.execution.adapter import Adapter
from ose.execution.distributed import Coordinator, Worker
from ose.execution.simulation import Simulation
from ose.execution.simulation.clock import VirtualClock
from ose.execution.simulation.options import DEFAULT_MAX_MICROSTEPS, microstep_limit
from ose.execution.simulation.trace import Trace
from ose.execution.supervisor import run
from ose.modeling.activation import activation_graph, activation_order
from ose.modeling.validation import validate_graph
from tests.unit.test_activation_model import feedback_model


def cyclic_plan():
    model = feedback_model()
    model['components']['a']['inputs']['feedback'].update(mode='push', activates={'message': ['out']})
    for node in model['components'].values():
        node['execution_model'] = 2
    model['execution'] = 'distributed'
    return model


class MicrostepModelTests(unittest.TestCase):
    def setUp(self):
        self.model = feedback_model()
        self.model['components']['a']['inputs']['feedback'].update(mode='push', activates={'message': ['out']})
        self.deployment = dict(version=1, components={name: dict(command=['unused']) for name in ('a', 'b')})

    def test_structure_accepts_feedback_but_execution_requires_explicit_boundary(self):
        validate_graph(self.model, self.deployment)
        with self.assertRaisesRegex(ValueError, 'activation graph contains a cycle'):
            activation_order(self.model)
        self.assertEqual(activation_graph(self.model, microsteps=True)['b.input:message'], set())
        self.assertIn('a.out:publish', activation_graph(self.model)['b.input:message'])
        self.assertIn('a.feedback:message', activation_order(self.model, microsteps=True))
        self.model['subscriptions'][0]['from'] = 'a.missing'
        with self.assertRaises(ValueError):
            activation_order(self.model, microsteps=True)

    def test_launch_options_are_strict_and_not_part_of_the_model(self):
        self.assertIsNone(microstep_limit())
        self.assertEqual(microstep_limit(True), DEFAULT_MAX_MICROSTEPS)
        self.assertEqual(microstep_limit(True, 1), 1)
        for enabled, maximum in ((False, 1), (1, None), (None, None),
                                 *((True, n) for n in (0, -1, True, 1.0, '2'))):
            with self.subTest(enabled=enabled, maximum=maximum), self.assertRaises(ValueError):
                microstep_limit(enabled, maximum)
        with self.assertRaises(ValueError):
            validate_graph(dict(self.model, simulation=dict(mode='microsteps')), self.deployment)

    def test_microsteps_do_not_relax_input_permissions_or_timer_subscriptions(self):
        for replacement in (dict(root='Value', mode='pull', activates=dict(message=['out'])),
                            dict(root='Value', activates=dict(message=['unknown']))):
            model = copy.deepcopy(self.model)
            model['components']['a']['inputs']['feedback'] = replacement
            with self.assertRaises(ValueError):
                activation_order(model, microsteps=True)
        self.model['subscriptions'][1]['to'] = 'a.clock'
        with self.assertRaisesRegex(ValueError, 'timer input'):
            activation_order(self.model, microsteps=True)

    def test_engines_without_microsteps_reject_cycles_before_loading_sdl(self):
        plan = cyclic_plan()
        for create in (lambda: Adapter(plan, 'a'), lambda: Coordinator(plan),
                       lambda: Worker(plan, 'a', 'localhost', 1), lambda: Simulation(plan, 1)):
            with self.subTest(engine=create), self.assertRaisesRegex(ValueError, 'activation graph contains a cycle'):
                create()

    def test_trace_positions_are_opt_in_and_keep_ordinary_v2_bytes(self):
        clock = VirtualClock()
        clock.time_ns, clock.microstep = 10, 3
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'trace.jsonl'
            trace = Trace(clock, path)
            trace.emit('wave', components=['a']); trace.close()
            self.assertEqual(path.read_text(), '{"components":["a"],"event":"wave","time_ns":10}\n')
            trace = Trace(clock, path, microsteps=True)
            trace.emit('wave', components=['a']); trace.close()
            self.assertEqual(json.loads(path.read_text())['microstep'], 3)


class MicrostepStartupTests(unittest.IsolatedAsyncioTestCase):
    async def test_supervisor_refuses_cycles_before_opening_a_service(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'plan.json'
            path.write_text(json.dumps(cyclic_plan()))
            with patch('ose.execution.supervisor.Broker') as broker:
                with self.assertRaisesRegex(ValueError, 'activation graph contains a cycle'):
                    await run(path)
            broker.assert_not_called()


class MicrostepCliTests(unittest.TestCase):
    def test_simulation_and_replay_forward_launch_options(self):
        from ose.cli import main
        for arguments, target in ((['run'], 'ose.execution.simulation.server.serve'),
                                  (['replay', 'capture.pcapng', '--map', 'recorded=external'], 'ose.recording.replay.run')):
            with self.subTest(arguments=arguments), patch(target, new=AsyncMock(return_value={})) as launch, \
                    contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(main(arguments + ['--mode', 'simulated', '--duration', '1',
                    '--microsteps', '--max-microsteps', '37']), 0)
                self.assertTrue(launch.await_args.kwargs['microsteps'])
                self.assertEqual(launch.await_args.kwargs['max_microsteps'], 37)

    def test_invalid_launch_policy_fails_before_opening_a_plan_or_starting_components(self):
        from ose.cli import main
        for command in (['run'], ['replay', 'capture.pcapng', '--map', 'recorded=external']):
            for options in (['--mode', 'realtime', '--microsteps'], ['--max-microsteps', '3'],
                            ['--mode', 'simulated', '--microsteps', '--max-microsteps', '0']):
                with self.subTest(command=command, options=options), \
                        patch('ose.cli.read_plan') as read, contextlib.redirect_stderr(io.StringIO()):
                    self.assertEqual(main(command + options), 1)
                    read.assert_not_called()
