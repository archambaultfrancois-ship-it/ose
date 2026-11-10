"""Last-value storage, strict declarations and status-based sampling."""
import io
import json
import struct
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

from ose.execution.input_process import InputSession
from ose.execution.ipc import frame, port_data
from ose.execution.measurements import Measurements, validate_snapshot
from ose.measures import sample, render, run
from ose.transport.secure import RPCServer
from tests.component_driver import timer_frame


def status(distributed=False):
    metrics = {'measurements': {'moteur.temperature': 42.5, 'moteur.unpublished': None}}
    return {'epoch': 'run', 'measurements_version': 1,
            'observation': {'components': {'analyse': {'connected': True}, 'gone': {'connected': False}}},
            'components': {'analyse': {'metrics': metrics} if distributed else metrics,
                           'gone': {'measurements': {'obsolete': 1.0}}}}


def options(**kwargs):
    return SimpleNamespace(**(dict(endpoint=None, host='127.0.0.1', port=7000, distributed=False,
        insecure=False, tls_ca=None, tls_cert=None, tls_key=None, watch=False, interval=.1, count=None,
        patterns=[], format='jsonl') | kwargs))


class RegistryTests(unittest.TestCase):
    def test_only_latest_value_and_unpublished_names(self):
        registry = Measurements()
        registry.declare(port_data('moteur.temperature', b''))
        registry.declare(port_data('moteur.unpublished', b''))
        self.assertEqual(registry.snapshot()['moteur.temperature'], None)
        for index in range(1000):
            registry.update(port_data('moteur.temperature', struct.pack('>d', index + .5)))
        self.assertEqual(registry.snapshot(), {'moteur.temperature': 999.5, 'moteur.unpublished': None})
        self.assertEqual(Measurements().snapshot(), {})

    def test_names_and_registry_limits(self):
        for name in ('', '.a', 'a.', 'a..b', '1a', 'a.2b', 'a-b', 'été', 'a\x00b', 'a' * 201):
            with self.subTest(name=name), self.assertRaises(ValueError):
                Measurements().declare(port_data(name, b''))
        registry = Measurements()
        registry.declare(port_data('_valid._v2', b''))
        registry.declare(port_data('a' * 200, b''))
        with self.assertRaises(ValueError):
            registry.declare(port_data('_valid._v2', b''))
        for index in range(126):
            registry.declare(port_data(f'point.n{index}', b''))
        with self.assertRaises(ValueError):
            registry.declare(port_data('too.many', b''))
        with self.assertRaises(ValueError):
            Measurements().declare(port_data('a', b'extra'))

    def test_invalid_updates_and_network_snapshots(self):
        registry = Measurements(); registry.declare(port_data('a', b''))
        for name, payload in [('unknown', struct.pack('>d', 1.)), ('a', b'bad'),
                              *[('a', struct.pack('>d', value)) for value in (float('nan'), float('inf'), -float('inf'))]]:
            with self.subTest(name=name, payload=payload), self.assertRaises(ValueError):
                registry.update(port_data(name, payload))
        for values in ([], {'a': True}, {'a': '2'}, {'a': float('nan')}, {'bad..name': 1.},
                       {f'n{i}': None for i in range(129)}):
            with self.subTest(values=str(values)[:60]), self.assertRaises(ValueError):
                validate_snapshot(values)
        self.assertEqual(registry.snapshot(), {'a': None})

    def test_python_sdk_lifecycle_and_finite_values(self):
        output = io.BytesIO()
        session = InputSession(io.BytesIO(frame('I', b'{}') + timer_frame(1) + frame('X')),
                                   output, auto_ready=False)
        session.declare_measurement('moteur.temperature')
        with self.assertRaises(RuntimeError):
            session.measure('moteur.temperature', 1.)
        session.ready()
        with self.assertRaises(RuntimeError):
            session.declare_measurement('late')
        for _ in session.activations():
            for value in (True, '42', float('nan'), float('inf'), 10**1000):
                with self.subTest(value=str(value)[:20]), self.assertRaises(ValueError):
                    session.measure('moteur.temperature', value)
            with self.assertRaises(ValueError):
                session.measure('unknown', 1.)
            session.measure('moteur.temperature', 42.5)
        stream = io.BytesIO(output.getvalue()); ops = []
        while stream.tell() < len(output.getvalue()):
            size, = struct.unpack('>I', stream.read(4)); ops.append(chr(stream.read(size)[0]))
        self.assertEqual(ops, ['M', 'R', 'V', 'F'])

    def test_sampling_filters_shapes_and_missing_values(self):
        for distributed in (False, True):
            report = sample(status(distributed), ['analyse.moteur.*'])
            self.assertEqual(report['measurements'], {'analyse': {'moteur.temperature': 42.5, 'moteur.unpublished': None}})
            self.assertIn('moteur.unpublished', render(report))
            self.assertIn('-', render(report))
            self.assertEqual(sample(status(distributed), ['*.temperature'])['measurements'],
                             {'analyse': {'moteur.temperature': 42.5}})
            self.assertEqual(sample(status(distributed), ['nothing'])['measurements'], {})
        with self.assertRaisesRegex(ValueError, 'updated OSE'):
            sample({})


class SamplingTests(unittest.IsolatedAsyncioTestCase):
    async def test_status_only_rpc_sampling_and_default_single_read(self):
        requests = []
        async def request(peer, header, body):
            requests.append(header['op'])
            return status(distributed=True), None
        server = RPCServer(1024 * 1024, request)
        port = await server.start('127.0.0.1', 0)
        self.addAsyncCleanup(server.close)
        for count in (None, 2):
            output = io.StringIO()
            self.assertEqual(await run(options(port=port, count=count), output), 0)
            reports = [json.loads(line) for line in output.getvalue().splitlines()]
            self.assertEqual(len(reports), count or 1)
            self.assertEqual(reports[0]['measurements']['analyse']['moteur.temperature'], 42.5)
        self.assertEqual(requests, ['status'] * 3)

    async def test_connection_failure_never_reuses_old_values(self):
        client = SimpleNamespace(request=AsyncMock(side_effect=[(status(), b''), EOFError('gone')]), close=AsyncMock())
        output = io.StringIO()
        with patch('ose.measures.connect', AsyncMock(return_value=client)):
            self.assertEqual(await run(options(count=2), output), 1)
        reports = [json.loads(line) for line in output.getvalue().splitlines()]
        self.assertTrue(reports[0]['measurements'])
        self.assertEqual(reports[1]['measurements'], {})
        self.assertEqual(reports[1]['error'], 'gone')
        client.close.assert_awaited_once()

    async def test_invalid_options_fail_before_connecting(self):
        for kwargs in ({'interval': 0}, {'interval': float('nan')}, {'count': 0},
                       {'endpoint': 'endpoint.json'}, {'port': 0}):
            with self.subTest(kwargs=kwargs), patch('ose.measures.connect', AsyncMock()) as connect:
                with self.assertRaises(ValueError):
                    await run(options(**kwargs), io.StringIO())
                connect.assert_not_awaited()
