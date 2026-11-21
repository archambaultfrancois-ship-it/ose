"""Exercise the real component graph, variable SDL payloads and OSE web services."""
import asyncio
import copy
import importlib.util
import json
import math
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
import uuid

ROOT = Path(__file__).resolve().parents[1]
FRAMEWORK = ROOT.parent
for directory in (FRAMEWORK / 'sdl/runtime/python', FRAMEWORK / 'sdl/runtime/filter/python',
                  FRAMEWORK / 'sdl/build/filter/release', FRAMEWORK / 'build/python-deps', FRAMEWORK / 'ose/src', FRAMEWORK / 'ose-ws/src'):
    sys.path.insert(0, str(directory))

from ose.execution.simulation.server import SimulationServer
from ose.execution.plan import read_plan


class WorkshopTests(unittest.IsolatedAsyncioTestCase):
    @classmethod
    def setUpClass(cls):
        temporary = tempfile.TemporaryDirectory()
        cls.addClassCleanup(temporary.cleanup)
        cls.build_dir = Path(temporary.name) / 'build'
        subprocess.run([sys.executable, str(ROOT / 'tools/workshop.py'), 'build', '--build-dir', str(cls.build_dir)], cwd=ROOT, check=True,
                       stdout=subprocess.DEVNULL)
        cls.plan = read_plan(cls.build_dir / 'plan.json')
        cls.ids = {'.'.join(source['path']): name
                   for name, source in cls.plan['composition']['components'].items()}
        if any(name != path for path, name in cls.ids.items()):
            raise AssertionError('runtime component identities must be their dotted subsystem paths')
        cls.subscriptions = {'.'.join(source['path']): name
                             for name, source in cls.plan['composition']['subscriptions'].items()}

    async def test_native_workload_kernels_and_bounds(self):
        binary, = (self.build_dir / 'native').glob('_thermal_native*.so')
        spec = importlib.util.spec_from_file_location('_thermal_native', binary)
        native = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(native)
        for count in (1, 1024, 8192):
            actual = native.samples(22.75, 191, 3, count)
            expected = [22.75 + .2 * math.sin(k * .11 + 191 + 3) for k in range(count)]
            self.assertEqual(len(actual), count)
            self.assertTrue(all(math.isclose(a, b, abs_tol=1e-12) for a, b in zip(actual, expected)))
        for iterations in (0, 1, 5000, 200000):
            expected = 22.75
            for n in range(iterations):
                expected = math.sin(expected + n * .001) * .5
            self.assertAlmostEqual(native.burn(iterations, 22.75), expected, places=12)
        for count in (0, 8193):
            with self.assertRaises(ValueError):
                native.samples(22.75, 191, 3, count)
        self.assertEqual(len(native.samples(22.75, 2**64-1, 3, 1)), 1)
        with self.assertRaises(OverflowError):
            native.samples(22.75, -1, 3, 1)
        with self.assertRaises(ValueError):
            native.burn(200001, 22.75)

    async def start_server(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.directory = Path(temporary.name)
        plan = copy.deepcopy(self.plan)
        plan['build_dir'] = str(self.directory)
        server = SimulationServer(plan, 3, keep_alive=True)
        self.addAsyncCleanup(server.close)
        await server.start()
        await asyncio.wait_for(server.ready.wait(), 15)
        self.assertEqual(server.state, 'ready', server.error)
        return server

    async def command(self, server, action, **values):
        operation = await server.command(dict(epoch=server.epoch, command_id=uuid.uuid4().hex, action=action, **values))
        outcome = await asyncio.wait_for(server.wait_operation(operation['id']), 20)
        self.assertEqual(outcome['status'], 'complete', outcome)
        return outcome

    async def test_feedback_all_components_measurements_and_reset(self):
        server = await self.start_server()
        self.assertEqual(len(server.plan['components']), 20)
        self.assertEqual(len(server.plan['subscriptions']), 28)
        await self.command(server, 'run_until', time_ns='1100000000')
        snapshot = server.snapshot()
        for name, component in snapshot['components'].items():
            self.assertGreater(component['measurements']['messages'], 0, name)
        for i in range(1, 5):
            plant = snapshot['components'][self.ids[f'cell_{i}.plant']]['measurements']
            self.assertGreater(plant['temperature'], 22)
            self.assertNotEqual(plant['power'], .25)
        self.assertGreater(snapshot['components'][self.ids['supervision.supervisor']]['measurements']['correction'], 0)
        self.assertEqual(snapshot['components'][self.ids['supervision.fusion']]['measurements']['cells'], 4)
        self.assertEqual(sum(len(component['measurements']) for component in snapshot['components'].values()), 83)
        await self.command(server, 'break_add', time_ns='1500000000', persistent=True)
        await self.command(server, 'reset')
        self.assertEqual(server.execution()['armed_breakpoints_ns'], ['1500000000'])
        self.assertEqual(server.execution()['position_ns'], '0')

    async def test_nested_variable_messages_round_trip_and_bounds(self):
        sys.path.insert(0, str(self.build_dir / 'components' / self.ids['cell_1.plant'] / 'types'))
        sys.path.insert(0, str(ROOT / 'components'))
        from messages import Workload, CellSettings, Actuation, Header
        import plant

        class Inbox:
            def pull(self):
                return type('Input', (), {'message': Actuation(header=Header(cell=1, sequence=1, time_ns=0), power=.8, stages=[], diagnostic=None)})()

        class Context:
            parameters = {'cell': 1}
            name = 'plant_1'
            time_ns = 100000000
            def __init__(self):
                self.frames = []
            def now(self): return self.time_ns / 1e9
            def declare_measurement(self, name): pass
            def log(self, *args): pass
            def measure(self, *args): pass
            def parameters_cell(self): return CellSettings(target=60, gain=.01, ambient=20)
            def parameters_workload(self): return Workload(min_samples=32, max_samples=128, burst=16, cpu_iterations=0, log_every=20, label='test')
            def inbox(self, name): return Inbox()
            def publish_frame(self, message, **kwargs): self.frames.append(message)

        ctx = Context()
        plant.on_start(ctx)
        plant.on_clock(ctx, None)
        self.assertEqual(len(ctx.frames), 16)
        self.assertEqual({len(frame.channels) for frame in ctx.frames}, {1, 2, 3, 4})
        self.assertTrue(any(frame.note is None for frame in ctx.frames))
        self.assertTrue(any(frame.note is not None for frame in ctx.frames))
        self.assertTrue(any(frame.diagnostic is not None for frame in ctx.frames))
        from ose_ws.schema.adapter import SchemaAdapter
        descriptor = self.plan['components'][self.ids['cell_1.plant']]['outputs']['frame']
        adapter = SchemaAdapter(descriptor['catalogue'], 'SensorFrame')
        codec = adapter.values.api.prepare(descriptor['catalogue'], [type(ctx.frames[0])])
        lengths = set()
        for frame in ctx.frames:
            # The real runtime encoder exercises nested repeated/packed fields.
            payload = adapter.values.api.encode(codec, frame)
            lengths.add(len(payload))
            decoded = adapter.read(payload)
            _, encoded = adapter.validate(decoded)
            self.assertEqual(encoded, payload)
            self.assertEqual(len(decoded['channels']), len(frame.channels))
        self.assertGreater(len(lengths), 8)

    async def console(self):
        import httpx
        from ose_ws.cli import initialize
        from ose_ws.config import read
        from ose_ws.server import create_app

        config_path = self.directory / 'ose-ws.json'
        initialize(str(config_path), auth=True)
        config = read(config_path)
        config['application'].update(root=str(ROOT), endpoint=str(self.directory / 'endpoint.json'))
        environment = patch.dict(os.environ, OSE_PARAMETER_TOKEN='thermal-workshop-local-operator')
        environment.start()
        self.addCleanup(environment.stop)
        app = create_app(config)
        lifetime = app.router.lifespan_context(app)
        await lifetime.__aenter__()
        self.addAsyncCleanup(lifetime.__aexit__, None, None, None)
        client = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://127.0.0.1:8080')
        self.addAsyncCleanup(client.aclose)
        token = Path(config['auth']['token_file']).read_text().strip()
        login = await client.post('/api/v1/auth/login', json={'token': token})
        self.assertEqual(login.status_code, 200)
        csrf = {'X-CSRF-Token': login.json()['csrf']}
        return app, client, csrf

    async def test_web_console_observes_graph_parameters_logs_and_virtual_series(self):
        server = await self.start_server()
        app, client, csrf = await self.console()
        async def collected(position):
            for _ in range(100):
                if app.state.collector.snapshot.get('simulation_time_ns') == position:
                    return
                await asyncio.sleep(.05)
            self.fail('web collector did not observe ' + position)
        await collected('0')
        components = (await client.get('/api/v1/components')).json()
        self.assertEqual(len(components['components']), 20)
        topology = components['topology']['composition']
        self.assertEqual(set(topology['instances']), set(self.plan['composition']['instances']))
        self.assertEqual(topology['components'][self.ids['cell_1.plant']]['path'], ['cell_1', 'plant'])
        measures = (await client.get('/api/v1/measurements')).json()['items']
        self.assertEqual(len(measures), 83)
        self.assertTrue(all(point['component'] in self.plan['components'] for point in measures))
        self.assertTrue(all(point['name'].startswith(point['component'] + ':') for point in measures))
        identity = measures[0]['id']
        response = await client.post('/api/v1/measurement-watches', json={'measurement_ids': [identity]}, headers=csrf)
        self.assertEqual(response.status_code, 200, response.text)
        parameters = (await client.get('/api/v1/parameters')).json()['items']
        self.assertEqual(len(parameters), 5)
        for block in parameters:
            response = await client.get('/api/v1/parameters/' + block['id'])
            self.assertEqual(response.status_code, 200, response.text)
        await self.command(server, 'run_until', time_ns='1100000000')
        await collected('1100000000')
        series = (await client.get('/api/v1/series', params={'ids': identity, 'start': 0})).json()
        self.assertTrue(series['segments'])
        self.assertEqual(series['segments'][0]['time_basis'], 'simulated')
        logs = (await client.get('/api/v1/logs')).json()
        self.assertGreaterEqual(len(logs['items']), 20)

    async def test_realtime_live_preview_and_concurrent_web_reads(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.directory = Path(temporary.name)
        plan = json.loads((self.build_dir / 'plan.json').read_text())
        plan['build_dir'] = str(self.directory)
        path = self.directory / 'plan.json'
        path.write_text(json.dumps(plan))
        environment = dict(os.environ, SDL_ROOT=str(FRAMEWORK / 'sdl'))
        environment['PYTHONPATH'] = os.pathsep.join([str(FRAMEWORK / 'ose/src'), environment.get('PYTHONPATH', '')])
        log_path = self.directory / 'realtime.log'
        with log_path.open('wb') as log:
            process = await asyncio.create_subprocess_exec(sys.executable, '-m', 'ose.cli', 'run', '--plan', str(path),
                '--duration', '30', cwd=ROOT, env=environment, stdout=log, stderr=log)
        async def stop():
            if process.returncode is None:
                process.terminate()
            await asyncio.wait_for(process.wait(), 10)
        self.addAsyncCleanup(stop)
        for _ in range(200):
            if (self.directory / 'endpoint.json').exists():
                break
            self.assertIsNone(process.returncode, log_path.read_text())
            await asyncio.sleep(.05)
        app, client, csrf = await self.console()
        for _ in range(200):
            snapshot = app.state.collector.snapshot
            if app.state.collector.error is None and len(snapshot.get('components', {})) == 20:
                break
            self.assertIsNone(process.returncode, log_path.read_text())
            await asyncio.sleep(.05)
        self.assertIsNone(app.state.collector.error, app.state.collector.error)
        self.assertEqual(set(snapshot['components']), set(self.ids))
        from ose.transport.secure import connect
        endpoint = json.loads((self.directory / 'endpoint.json').read_text())
        rpc = await connect(endpoint['host'], endpoint['port'], 1024 * 1024)
        try:
            points, _ = await rpc.request('recording_points')
        finally:
            await rpc.close()
        self.assertTrue(any(point['topic'] == 'cell_1.plant:frame' for point in points['points']))
        self.assertTrue(all('ss_' not in point['topic'] for point in points['points']))
        response = await client.post('/api/v1/flow-watches', json={
            'subscription': self.subscriptions['cell_1.frame'], 'epoch': snapshot['epoch']}, headers=csrf)
        self.assertEqual(response.status_code, 200, response.text)
        identity = response.json()['id']
        preview = None
        for _ in range(100):
            preview = (await client.get('/api/v1/flow-watches/' + identity)).json()
            if preview.get('sample'):
                break
            await asyncio.sleep(.05)
        self.assertEqual(preview['status'], 'live', preview)
        fields = {field['name'] for field in preview['sample']['value']['children']}
        self.assertIn('channels', fields)
        self.assertIn('diagnostic', fields)
        responses = await asyncio.gather(*(client.get(path) for _ in range(4)
            for path in ('/api/v1/application', '/api/v1/components', '/api/v1/measurements', '/api/v1/logs')))
        for response in responses:
            self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(len(responses), 16)
        response = await client.delete('/api/v1/flow-watches/' + identity, headers=csrf)
        self.assertEqual(response.status_code, 204)


if __name__ == '__main__':
    unittest.main()
