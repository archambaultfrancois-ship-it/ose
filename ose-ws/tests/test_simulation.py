"""Web API against a real networked simulation, including permissions and epochs."""
import asyncio
import base64
import copy
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
import uuid

import httpx
from ose.execution.simulation.server import SimulationServer
from ose_ws.cli import initialize
from ose_ws.config import read
from ose_ws.schema.adapter import SchemaAdapter
from ose_ws.server import create_app
from test_core import CATALOGUE, VALUE

SDL_ROOT = Path(__file__).resolve().parents[2] / 'sdl'
HANDLER = '''from ose.execution.input_process import InputSession
s = InputSession(auto_ready=False)
s.declare_measurement('counter')
s.log('INFO', 'source initialized', 'lifecycle')
s.ready()
for activation in s.activations():
    s.measure('counter', float(s.tick_index))
    s.log('DEBUG', 'callback complete', 'simulation')
'''


class SimulationAPITests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        (self.root / 'build').mkdir()
        handler = self.root / 'handler.py'
        handler.write_text(HANDLER)
        adapter = SchemaAdapter(CATALOGUE, 'Settings')
        _, initial = adapter.validate(VALUE)
        plan = dict(version=2, sdl_root=str(SDL_ROOT), build_dir=str(self.root / 'build'), max_frame=1024*1024,
            components={'source': dict(execution_model=2, command=[sys.executable, str(handler)],
                cwd=str(self.root), env={}, parameters={}, outputs={},
                inputs={'clock': dict(kind='timer', timer=dict(mode='periodic', delay=.01), activates={'timeout': []})})},
            subscriptions=[], shared_parameters={'settings': dict(root='Settings', catalogue=CATALOGUE,
                initial_payload=base64.b64encode(initial).decode())},
            parameter_clients={'operator': dict(token='simulation-test-token', read=['settings'], write=['settings'])})
        self.server = SimulationServer(plan, .1, keep_alive=True)
        self.addAsyncCleanup(self.server.close)
        await self.server.start()
        await asyncio.wait_for(self.server.ready.wait(), 5)
        path = self.root / 'ose-ws.json'
        initialize(str(path))
        config = read(path)
        config['application'].update(root=str(self.root), endpoint=str(self.root / 'build/endpoint.json'))
        environment = patch.dict(os.environ, OSE_PARAMETER_TOKEN='simulation-test-token')
        environment.start()
        self.addCleanup(environment.stop)
        self.app = create_app(config)
        lifetime = self.app.router.lifespan_context(self.app)
        await lifetime.__aenter__()
        self.addAsyncCleanup(lifetime.__aexit__, None, None, None)
        self.client = httpx.AsyncClient(transport=httpx.ASGITransport(app=self.app), base_url='http://127.0.0.1:8080')
        self.addAsyncCleanup(self.client.aclose)
        token = Path(config['auth']['token_file']).read_text().strip()
        response = await self.client.post('/api/v1/auth/login', json={'token': token})
        self.csrf = response.json()['csrf']
        await self.collected()

    async def collected(self, position=None, epoch=None):
        for _ in range(150):
            snapshot = self.app.state.collector.snapshot
            if snapshot and (position is None or snapshot.get('simulation_time_ns') == position) and (epoch is None or snapshot.get('epoch') == epoch):
                return
            await asyncio.sleep(.02)
        self.fail('collector did not observe the expected simulation snapshot')

    async def command(self, action, *, key=None, epoch=None, **args):
        data = dict(action=action, epoch=epoch or self.server.epoch, **args)
        response = await self.client.post('/api/v1/simulation/commands', json=data,
            headers={'X-CSRF-Token': self.csrf, 'Idempotency-Key': key or uuid.uuid4().hex})
        self.assertEqual(response.status_code, 200, response.text)
        operation = response.json()
        for _ in range(150):
            if operation['status'] != 'pending':
                self.assertEqual(operation['status'], 'complete', operation)
                return operation
            await asyncio.sleep(.02)
            operation = (await self.client.get('/api/v1/operations/' + operation['id'])).json()
        self.fail('web operation did not complete')

    async def test_observation_measurement_virtual_axis_logs_and_final_snapshot(self):
        application = (await self.client.get('/api/v1/application')).json()
        self.assertEqual(application['mode'], 'simulated')
        self.assertEqual(application['execution']['state'], 'paused')
        components = (await self.client.get('/api/v1/components')).json()
        self.assertEqual(components['components'][0]['state'], 'IDLE')
        catalogue = (await self.client.get('/api/v1/measurements')).json()['items']
        identity = catalogue[0]['id']
        response = await self.client.post('/api/v1/measurement-watches', json={'measurement_ids': [identity]}, headers={'X-CSRF-Token': self.csrf})
        self.assertEqual(response.status_code, 200)
        store = self.app.state.measurements
        store.observe(self.app.state.collector.snapshot)
        store.observe(self.app.state.collector.snapshot)
        initial = store.query([identity])['segments']
        self.assertEqual(len(initial[0]['points']), 1)
        await self.command('run_until', time_ns='30000000')
        await self.collected('30000000')
        history = (await self.client.get('/api/v1/series', params={'ids': identity, 'start': 0})).json()
        self.assertTrue(history['segments'], history)
        segment = history['segments'][0]
        self.assertEqual(segment['time_basis'], 'simulated')
        self.assertEqual(segment['points'][-1]['time'], 30.)
        self.assertEqual(segment['points'][-1]['value'], 2.)
        self.assertIsNone(segment['points'][-1]['observed_at'])
        logs = (await self.client.get('/api/v1/logs')).json()
        self.assertTrue(any(row.get('simulation_time_ns') is not None for row in logs['items']))
        await self.command('run_until', time_ns='100000000')
        await self.collected('100000000')
        await self.server.task
        self.assertEqual((await self.client.get('/api/v1/simulation')).json()['state'], 'finished')

    async def test_commands_require_csrf_permission_epoch_and_preserve_idempotency(self):
        data = dict(action='step_duration', epoch=self.server.epoch, time_ns='10000000')
        response = await self.client.post('/api/v1/simulation/commands', json=data)
        self.assertEqual(response.status_code, 403)
        signed_in = next(iter(self.app.state.auth.sessions.values()))
        with patch.object(signed_in, 'permissions', {'components.read': ['*']}):
            response = await self.client.post('/api/v1/simulation/commands', json=data, headers={'X-CSRF-Token': self.csrf})
            self.assertEqual(response.status_code, 403)
        key = uuid.uuid4().hex
        first = await self.command('step_duration', key=key, time_ns='10000000')
        again = await self.command('step_duration', key=key, time_ns='10000000')
        self.assertEqual(first['id'], again['id'])
        self.assertEqual(self.server.engine.clock.time_ns, 10000000)
        await self.collected('10000000')
        old = self.server.epoch
        await self.command('reset')
        await self.collected('0', self.server.epoch)
        response = await self.client.post('/api/v1/simulation/commands', json=data | {'epoch': old},
            headers={'X-CSRF-Token': self.csrf, 'Idempotency-Key': uuid.uuid4().hex})
        self.assertEqual(response.status_code, 409)

    async def test_existing_typed_parameter_service_works_during_pause(self):
        blocks = (await self.client.get('/api/v1/parameters')).json()['items']
        self.assertEqual(len(blocks), 1)
        block = blocks[0]['id']
        value = (await self.client.get('/api/v1/parameters/' + block)).json()
        self.assertEqual(value['value']['counter'], VALUE['counter'])
        schema = (await self.client.get('/api/v1/parameters/' + block + '/schema')).json()
        self.assertEqual(schema['root'], 'Settings')
        self.assertEqual(self.server.engine.clock.time_ns, 0)

    async def test_terminal_and_web_share_breakpoints_and_component_controls(self):
        from ose.execution.simulation.console import SimulationConsole
        import io

        terminal = SimulationConsole(self.server, output=io.StringIO())
        await terminal.execute('break-time 20ms')
        self.assertEqual(self.server.execution()['breakpoints_ns'], ['20000000'])
        await self.command('break_clear')
        self.assertEqual(self.server.execution()['breakpoints_ns'], [])
        await self.command('component_set', components=['source'], enabled=False)
        states = await terminal.execute('component status source')
        self.assertFalse(states['source']['active'])
        await terminal.execute('component enable source')
        states = await terminal.execute('component status source')
        self.assertTrue(states['source']['active'])
        self.assertEqual(self.server.engine.clock.time_ns, 0)
        await self.command('break_add', time_ns='20000000', persistent=True)
        await self.command('run_until', time_ns='30000000')
        self.assertEqual(self.server.engine.clock.time_ns, 20000000)
        await self.command('reset')
        self.assertEqual(self.server.execution()['armed_breakpoints_ns'], ['20000000'])
        await self.collected('0', self.server.epoch)
        await self.command('break_clear')
        self.assertEqual(self.server.execution()['persistent_breakpoints_ns'], [])
