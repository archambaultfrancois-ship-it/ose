"""Network-controlled simulation with real CH/2 components and shared services."""
import asyncio
import base64
import io
import json
import os
from pathlib import Path
import struct
import sys
import tempfile
import unittest
import uuid

from ose.execution.simulation.server import SimulationServer
from ose.transport.tcp import Client, RemoteError

CAT = 'SDL2|Value{1:uint64:sequence;2:fl64:value;}'
HANDLER = '''from ose.execution.input_process import InputSession
s = InputSession(auto_ready=False)
s.declare_measurement('counter')
s.ready()
for activation in s.activations():
    s.measure('counter', float(s.tick_index))
'''


@unittest.skipUnless(os.environ.get('SDL_ROOT'), 'set SDL_ROOT')
class SimulationServerTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        handler = self.root / 'handler.py'
        handler.write_text(HANDLER)
        self.payload = b'\x01' + struct.pack('>Qd', 1, 2.)
        self.plan = dict(version=2, sdl_root=os.environ['SDL_ROOT'], build_dir=str(self.root),
            max_frame=1024*1024, components={'source': dict(execution_model=2,
                command=[sys.executable, str(handler)], cwd=str(self.root), env={}, parameters={},
                inputs={'clock': dict(kind='timer', timer=dict(mode='periodic', delay=.01), activates={'timeout': []})}, outputs={})},
            subscriptions=[], shared_parameters={'settings': dict(root='Value', catalogue=CAT,
                initial_payload=base64.b64encode(self.payload).decode())},
            parameter_clients={'operator': dict(token='test-token', read=['settings'], write=['settings'])})
        self.server = SimulationServer(self.plan, .1, keep_alive=True)
        await self.server.start()
        self.server.control.output = io.StringIO()
        self.addAsyncCleanup(self.server.close)
        await asyncio.wait_for(self.server.ready.wait(), 5)
        self.assertEqual(self.server.state, 'ready', self.server.error)
        self.client = await Client.connect('127.0.0.1', self.server.port)
        self.addAsyncCleanup(self.client.close)

    async def submit(self, action, **args):
        return (await self.client.request('simulation_command', epoch=self.server.epoch,
            command_id=uuid.uuid4().hex, action=action, **args))[0]

    async def wait(self, operation):
        for _ in range(300):
            result, _ = await self.client.request('simulation_operation', id=operation['id'])
            if result['status'] != 'running':
                self.assertNotEqual(result['status'], 'failed', result)
                return result
            await asyncio.sleep(.01)
        self.fail('simulation operation did not settle')

    async def test_persistent_breakpoints_survive_reset_and_fire_once_per_session(self):
        await self.wait(await self.submit('break_add', time_ns='20000000', persistent=True))
        await self.wait(await self.submit('break_add', time_ns='40000000'))
        await self.wait(await self.submit('run_until', time_ns='30000000'))
        self.assertEqual(self.server.engine.clock.time_ns, 20000000)
        self.assertEqual(self.server.execution()['persistent_breakpoints_ns'], ['20000000'])
        self.assertEqual(self.server.execution()['armed_breakpoints_ns'], ['40000000'])
        await self.wait(await self.submit('run_until', time_ns='30000000'))
        self.assertEqual(self.server.engine.clock.time_ns, 30000000)
        await self.wait(await self.submit('reset'))
        self.assertEqual(self.server.execution()['armed_breakpoints_ns'], ['20000000'])
        await self.wait(await self.submit('run_until', time_ns='30000000'))
        self.assertEqual(self.server.engine.clock.time_ns, 20000000)
        await self.wait(await self.submit('break_remove', time_ns='20000000'))
        await self.wait(await self.submit('reset'))
        self.assertEqual(self.server.execution()['breakpoints_ns'], [])
        await self.wait(await self.submit('break_add', time_ns='10000000', persistent=True))
        await self.wait(await self.submit('break_clear'))
        await self.wait(await self.submit('reset'))
        self.assertEqual(self.server.execution()['breakpoints_ns'], [])
        with self.assertRaises(RemoteError):
            await self.submit('break_add', time_ns='20000000', persistent='true')

    async def test_endpoint_snapshot_step_exclusive_boundary_and_final_state(self):
        endpoint = json.loads((self.root / 'endpoint.json').read_text())
        self.assertEqual(endpoint['mode'], 'simulated')
        first, _ = await self.client.request('status')
        self.assertEqual(first['execution']['state'], 'paused')
        self.assertEqual(first['simulation_time_ns'], '0')
        self.assertIsNone(first['components']['source']['measurements']['counter'])
        await self.wait(await self.submit('run_until', time_ns='20000000'))
        status, _ = await self.client.request('status')
        self.assertEqual(status['simulation_time_ns'], '20000000')
        self.assertEqual(status['components']['source']['measurements']['counter'], 1.)
        self.assertEqual(status['execution']['position_ns'], status['simulation_time_ns'])
        await self.wait(await self.submit('run_until', time_ns='100000000'))
        await self.server.task
        last, _ = await self.client.request('status')
        self.assertEqual(last['execution']['state'], 'finished')
        self.assertEqual(last['components']['source']['measurements']['counter'], 9.)
        self.assertTrue(all(p.returncode is not None for p in self.server.engine.processes.values()))
        # The listener and final snapshot survive component teardown.
        self.assertEqual((await self.client.request('status'))[0]['epoch'], first['epoch'])

    async def test_parameter_authorization_changes_trace_and_reset(self):
        with self.assertRaises(RemoteError):
            await self.client.request('parameters_get', path='settings')
        with self.assertRaises(RemoteError):
            await self.client.request('parameters_open', client='operator', token='wrong')
        await self.client.request('parameters_open', client='operator', token='test-token')
        _, initial = await self.client.request('parameters_get', path='settings')
        self.assertEqual(initial, self.payload)
        changed = b'\x01' + struct.pack('>Qd', 2, 3.)
        await self.client.request('parameters_set', changed, path='settings')
        self.assertEqual((await self.client.request('parameters_get', path='settings'))[1], changed)
        self.assertEqual(self.server.engine.parameter_changes, 1)
        epoch = self.server.epoch
        await self.wait(await self.submit('reset'))
        self.server.control.output = io.StringIO()
        self.assertNotEqual(self.server.epoch, epoch)
        self.assertEqual(self.server.execution()['state'], 'paused')
        with self.assertRaises(RemoteError):
            await self.client.request('simulation_command', epoch=epoch, command_id='stale', action='resume')
        with self.assertRaises(RemoteError):
            await self.client.request('parameters_get', path='settings')
        await self.client.request('parameters_open', client='operator', token='test-token')
        self.assertEqual((await self.client.request('parameters_get', path='settings'))[1], initial)

    async def test_idempotency_conflicts_and_precision_validation(self):
        args = dict(epoch=self.server.epoch, command_id='one-step', action='step_duration', time_ns='10000000')
        op, _ = await self.client.request('simulation_command', **args)
        await self.wait(op)
        again, _ = await self.client.request('simulation_command', **args)
        self.assertEqual(again['id'], op['id'])
        self.assertEqual(self.server.engine.clock.time_ns, 10000000)
        with self.assertRaises(RemoteError):
            await self.client.request('simulation_command', **(args | {'time_ns': '20000000'}))
        for value in (1, True, '-1', '1.5', '18446744073709551616'):
            with self.subTest(value=value), self.assertRaises(RemoteError):
                await self.submit('run_until', time_ns=value)

    async def test_breakpoint_and_pause_interrupt_an_active_advance(self):
        await self.wait(await self.submit('break_add', time_ns='20000000'))
        await self.wait(await self.submit('run_until', time_ns='90000000'))
        self.assertEqual(self.server.control.pause_reason, 'breakpoint')
        self.assertEqual(self.server.engine.clock.time_ns, 20000000)
        entered, release = asyncio.Event(), asyncio.Event()
        calculate = self.server.engine._calculate
        async def slow(*args):
            entered.set()
            await release.wait()
            return await calculate(*args)
        self.server.engine._calculate = slow
        advancing = await self.submit('run_until', time_ns='90000000')
        await asyncio.wait_for(entered.wait(), 2)
        with self.assertRaises(RemoteError):
            await self.submit('step_instants', count=1)
        paused = await self.submit('pause')
        release.set()
        await self.wait(paused)
        result = await self.wait(advancing)
        self.assertEqual(result['status'], 'interrupted')
        self.assertTrue(self.server.control.paused)
        self.assertIsNone(self.server.active)
        await self.wait(await self.submit('reset'))

    async def test_continuous_and_stepped_runs_keep_the_same_functional_trace(self):
        await self.wait(await self.submit('run_until', time_ns='100000000'))
        await self.server.task
        digest = self.server.engine.summary['trace_sha256']
        from ose.execution.simulation import Simulation
        continuous = Simulation(self.plan, .1)
        summary = await continuous.execute()
        self.assertEqual(digest, summary['trace_sha256'])

    async def test_failed_wave_retains_last_committed_measurements_and_reports_failure(self):
        calculate = self.server.engine._calculate
        async def fail(*args):
            await calculate(*args)
            raise RuntimeError('injected failed wave')
        self.server.engine._calculate = fail
        operation = await self.submit('run_until', time_ns='30000000')
        await asyncio.wait_for(self.server.task, 5)
        await self.server.operations[operation['id']]['_task']
        result, _ = await self.client.request('simulation_operation', id=operation['id'])
        self.assertEqual(result['status'], 'failed')
        status, _ = await self.client.request('status')
        self.assertEqual(status['execution']['state'], 'failed')
        self.assertIn('injected failed wave', status['execution']['error'])
        self.assertIsNone(status['components']['source']['measurements']['counter'])
