"""Check C analyzer numerical accuracy and its CH/2 message/metric contract."""
import asyncio
import copy
import importlib.util
import json
import math
from pathlib import Path
import random
import sys
import tempfile
from types import SimpleNamespace
import unittest

ROOT = Path(__file__).resolve().parents[1]
FRAMEWORK = ROOT.parent
for directory in (FRAMEWORK / 'ose/src', FRAMEWORK / 'sdl/runtime/python',
                  ROOT / 'tools', ROOT / 'components'):
    sys.path.insert(0, str(directory))

from workshop import prepare
from ose.modeling.compiler import build
from ose.codec.sdl2 import SDL2
from ose.delivery.buffers import Buffer
from ose.delivery.policies import SubscriptionPolicy
from ose.delivery.stores import MemoryStore
from ose.execution.activation import InputScheduler
from ose.execution.input_ipc import InputServices
from ose.execution.process import calculate, start_component, stop_component
from ose.parameters.bindings import LocalBindings
from ose.parameters.service import ParameterService


def module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    result = importlib.util.module_from_spec(spec)
    sys.modules[name] = result
    spec.loader.exec_module(result)
    return result


class AnalyzerCTests(unittest.IsolatedAsyncioTestCase):
    @classmethod
    def setUpClass(cls):
        directory = tempfile.TemporaryDirectory(prefix='thermal-analyzer-c-test-')
        cls.addClassCleanup(directory.cleanup)
        cls.root = Path(directory.name)
        for name, value in zip(('model', 'deployment'), prepare('stress', 'c', directory=cls.root)):
            (cls.root / (name + '.json')).write_text(json.dumps(value))
        path = build(cls.root / 'model.json', cls.root / 'deployment.json',
                     FRAMEWORK / 'sdl', cls.root / 'build', component='cell_1.analyzer')
        cls.plan = json.loads(path.read_text())
        cls.ids = {'.'.join(source['path']): name
                   for name, source in cls.plan['composition']['components'].items()}
        cls.codec = SDL2(FRAMEWORK / 'sdl')
        cls.messages = module('_thermal_analyzer_test_messages',
                              cls.root / 'build/components' / cls.ids['cell_1.plant'] / 'types/messages.py')
        # analyzer.py imports the generated module by its usual name.
        previous = sys.modules.get('messages')
        sys.modules['messages'] = cls.messages
        try:
            cls.reference = module('_thermal_analyzer_reference', ROOT / 'components/analyzer.py')
        finally:
            if previous is None:
                sys.modules.pop('messages', None)
            else:
                sys.modules['messages'] = previous

    async def asyncSetUp(self):
        self.node = copy.deepcopy(self.plan['components'][self.ids['cell_1.analyzer']])
        self.now, self.index = 0, 0
        self.buffer = Buffer(SubscriptionPolicy(), MemoryStore(), lambda: self.now / 1e9)
        self.scheduler = InputScheduler(self.node, {'input': self.buffer})
        self.scheduler.start(0)
        self.services = InputServices(self.scheduler, lambda: self.now)
        self.parameters = ParameterService(self.plan, self.codec)
        self.bindings = LocalBindings(self.node, self.parameters)
        self.process = await start_component(self.node, parameters=self.bindings, inputs=self.services)
        self.addAsyncCleanup(stop_component, self.process)
        self.addCleanup(self.parameters.close)
        m = self.messages
        self.ctx = SimpleNamespace(name='analyzer_1', parameters={'cell': 1}, measurements={}, publications=[], logs=[])
        self.ctx.declare_measurement = lambda name: self.ctx.measurements.setdefault(name, None)
        self.ctx.log = lambda *args: self.ctx.logs.append(args)
        self.ctx.measure = lambda name, value: self.ctx.measurements.__setitem__(name, value)
        self.ctx.publish_analysis = lambda value, **kw: self.ctx.publications.append((value, kw.get('id')))
        self.ctx.parameters_workload = lambda: self.workload
        self.reference.on_start(self.ctx)
        self.encoder = self.codec.api.prepare(self.node['inputs']['input']['catalogue'], [m.SensorFrame, m.Analysis])
        self.workload = m.Workload(min_samples=1, max_samples=8192, burst=4, cpu_iterations=0, log_every=1, label='test')

    async def deliver(self, frame, app_id):
        self.index += 1
        self.now += 1000000
        desc = self.node['inputs']['input']
        payload = self.codec.api.encode(self.encoder, frame)
        body = self.codec.wrap(desc['catalogue'], payload)
        record = dict(id=str(self.index), topic='plant_1.frame', catalogue=desc['catalogue'],
                      epoch='test', sequence=self.index, body=body)
        if app_id is not None:
            record['app_id'] = app_id
        self.assertEqual(self.scheduler.admit('input', record, self.now), 'accepted')
        ref = self.scheduler.select(self.now)
        self.assertIsNotNone(ref)
        return await asyncio.wait_for(calculate(self.process, self.node, ref, self.index,
            self.services, self.codec, lambda: self.now, parameters=self.bindings), 10)

    def assert_analysis_close(self, actual, expected):
        if isinstance(expected, float):
            self.assertTrue(math.isclose(actual, expected, rel_tol=1e-10, abs_tol=1e-12),
                            (actual, expected))
        elif hasattr(expected, '_SDL_FIELDS'):
            self.assertIs(type(actual), type(expected))
            for field in expected._SDL_FIELDS:
                self.assert_analysis_close(getattr(actual, field[1]), getattr(expected, field[1]))
        elif isinstance(expected, list):
            self.assertEqual(len(actual), len(expected))
            for a, b in zip(actual, expected):
                self.assert_analysis_close(a, b)
        else:
            self.assertEqual(actual, expected)

    async def test_numerics_metrics_parameters_and_optional_fields(self):
        m, rng = self.messages, random.Random(412)
        cases = [[0.], [-0.], [1., -1.], [1000., 1., -1000., 3.], [22.] * 16]
        cases += [[rng.uniform(-40, 120) for _ in range(n)] for n in (2, 7, 64, 1024, 8192)]
        # Include the sample shape that exposed differing square roundoff.
        cases += [[36.8623 + .2 * math.sin(k * .11 + 191) for k in range(3900)]]
        sequences = (1, 2, 3, 11, 60, 61, 66, 183, 191, 2**64 - 1, 0)
        for i, (values, sequence) in enumerate(zip(cases, sequences)):
            self.workload.cpu_iterations = (0, 1, 17, 5000, 200001)[i % 5]
            self.workload.log_every = (0, 1, 10001)[i % 3]
            binding = self.node['parameter_bindings']['workload']
            context = self.codec.api.prepare(binding['catalogue'], [m.Workload])
            self.parameters.set(binding['path'], self.codec.api.encode(context, self.workload))
            frame = m.SensorFrame(header=m.Header(cell=1, sequence=sequence, time_ns=i),
                channels=[m.Channel(name='probe-é', samples=values, tags=[]),
                          m.Channel(name='other', samples=[-1., 2.], tags=[])], tags=[],
                diagnostic=m.Diagnostic(code=7, text='é\0warning', hints=['a', '水']) if i % 2 else None,
                note='unused input note' if i % 3 else None)
            # Python receives the same fl32 quantization as the C decoder.
            decoded = self.codec.api.decode(self.encoder, self.codec.api.encode(self.encoder, frame))
            app_id = (None, 0, 2**64 - 1)[i % 3]
            self.reference.on_input(self.ctx, SimpleNamespace(message=decoded, app_id=app_id))
            outputs = await self.deliver(frame, app_id)
            expected, identity = self.ctx.publications[-1]
            desc = self.node['outputs']['analysis']
            # Round spectrum bins to their declared fl32 wire representation.
            expected = self.codec.api.decode(self.encoder, self.codec.api.encode(self.encoder, expected))
            with self.subTest(sequence=sequence, samples=len(values), iterations=self.workload.cpu_iterations):
                self.assertEqual(len(outputs), 1)
                port, envelope, actual_id = outputs[0]
                self.assertEqual((port, actual_id), ('analysis', identity))
                self.codec.validate_only(desc['catalogue'], desc['root'], envelope)
                _, payload = self.codec.api.unpack_envelope(envelope)
                self.assert_analysis_close(self.codec.api.decode(self.encoder, payload), expected)
                actual = self.process.measurements.snapshot()
                self.assertEqual(set(actual), set(self.ctx.measurements))
                for name, value in self.ctx.measurements.items():
                    self.assert_analysis_close(actual[name], value)
        self.assertEqual(self.buffer.store.size(), 0)

    async def test_empty_channel_fails_without_committing_input(self):
        m = self.messages
        frame = m.SensorFrame(header=m.Header(cell=1, sequence=1, time_ns=0),
            channels=[m.Channel(name='empty', samples=[], tags=[])], tags=[], diagnostic=None, note=None)
        with self.assertRaises((EOFError, ValueError, asyncio.IncompleteReadError)):
            await self.deliver(frame, None)
        self.assertEqual(self.buffer.store.size(), 1)
        self.assertTrue(all(value is None for value in self.process.measurements.snapshot().values()))


if __name__ == '__main__':
    unittest.main()
