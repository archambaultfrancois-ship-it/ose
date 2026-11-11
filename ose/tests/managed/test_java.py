"""Executable Java CH/2 container boundaries; toolchain failures are failures."""
from tests.component_driver import run_component
import io
import json
import os
from pathlib import Path
import struct
import subprocess
import tempfile
import unittest

from ose.codec.sdl2 import SDL2
from ose.execution.activation import InputReference
from ose.execution.input_ipc import activation_data, unpack_publication
from ose.execution.ipc import frame, port_data
from ose.modeling.compiler import build
from generation_cases import check_generation
from tests.component_driver import timer_frame

SDL = Path(os.environ.get('SDL_ROOT', Path(__file__).resolve().parents[3] / "sdl"))


class JavaTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root / 'measurement.sdl').write_text('message Measurement {1: required uint64 sequence; 2: required fl64 value;}')
        self.model = {'version': 2, 'schemas': 'measurement.sdl', 'components': {
            'source': {'inputs': {'clock': {'kind': 'timer', 'timer': {'mode': 'periodic', 'delay': .01},
                                          'activates': {'timeout': ['out']}}},
                       'outputs': {'out': {'root': 'Measurement'}}},
            'sink': {'inputs': {'input': {'root': 'Measurement', 'timeout': .1,
                                         'activates': {'message': ['result'], 'timeout': ['result']}}},
                     'outputs': {'result': {'root': 'Measurement'}}}},
            'subscriptions': [{'id': 'link', 'from': 'source.out', 'to': 'sink.input'}]}
        self.deployment = {'version': 1, 'components': {
            name: {'container': {'language': 'java', 'handler': name.title() + '.java', 'class': name.title()}}
            for name in self.model['components']}}
        (self.root / 'deployment.json').write_text(json.dumps(self.deployment))
        (self.root / 'Source.java').write_text('''import org.sdlose.generated.source.API;
public class Source extends API.Handler {
 public void on_clock(API.Context ctx, org.sdlose.InputProtocol.Reference<Void> input) throws Exception {
  sdl.globaltypes.measurement.Measurement m = new sdl.globaltypes.measurement.Measurement();
  m.sequence = -1; m.value = ctx.now();
  ctx.publish_out(m, java.math.BigInteger.ONE.shiftLeft(64).subtract(java.math.BigInteger.ONE));
 }
}''')
        (self.root / 'Sink.java').write_text('''import org.sdlose.generated.sink.API;
import sdl.globaltypes.measurement.Measurement;
public class Sink extends API.Handler {
 public void on_input(API.Context ctx, org.sdlose.InputProtocol.Reference<Measurement> input) throws Exception {
  Measurement m = input.message;
  if (input.timedOut()) { m = new Measurement(); m.sequence = -1; m.value = -1; }
  ctx.publish_result(m, input.appId);
 }
 public void onStop(API.Context ctx) { System.err.println("stopped"); }
}''')
        self.rebuild()
        self.codec = SDL2(SDL)

    def rebuild(self):
        (self.root / 'model.json').write_text(json.dumps(self.model))
        path = build(self.root / 'model.json', self.root / 'deployment.json', SDL, self.root / 'build')
        self.plan = json.loads(path.read_text())

    def run_node(self, name, data, init=b'{}'):
        result = run_component(self.plan['components'][name]['command'],
                                input=frame('I', init) + data + frame('X'),
                                capture_output=True, timeout=15)
        stream, events = io.BytesIO(result.stdout), []
        while stream.tell() < len(result.stdout):
            size, = struct.unpack('>I', stream.read(4))
            value = stream.read(size)
            events.append((chr(value[0]), value[1:]))
        return result, events

    def activation(self, port='input', *, payload=None, index=1, app_id=None):
        record = None
        if payload is not None:
            cat = self.plan['components']['sink']['inputs']['input']['catalogue']
            record = dict(id='source:1', topic='source.out', catalogue=cat,
                          body=self.codec.wrap(cat, payload), received_ns=0)
            if app_id is not None:
                record['app_id'] = app_id
        origin = InputReference(port, 'message' if record else 'timeout',
                                2_000_000_000, 2_000_000_000, 1, record, frozenset())
        return frame('T', activation_data(origin, index))

    def test_typed_references_uint64_ids_and_timeout(self):
        result, events = self.run_node('source', timer_frame(1, time_ns=2_000_000_000))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual([op for op, _ in events], ['R', 'P', 'F'])
        self.assertEqual(events[0][1], b'\2')
        port, payload, app_id = unpack_publication(events[1][1])
        self.assertEqual((port, app_id), ('out', 2**64 - 1))
        cat = self.plan['components']['source']['outputs']['out']['catalogue']
        value = self.codec.api.decode_dynamic(self.codec.api.prepare(cat), payload)
        self.assertEqual(value.fields, {'sequence': 2**64 - 1, 'value': 2.0})
        for timed_out in (False, True):
            with self.subTest(timed_out=timed_out):
                data = self.activation(payload=None if timed_out else payload, app_id=app_id)
                result, events = self.run_node('sink', data)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual([op for op, _ in events], ['R', 'P', 'F'])
                output, body, identity = unpack_publication(events[1][1])
                self.assertEqual(output, 'result')
                self.assertEqual(identity, None if timed_out else app_id)
                value = self.codec.api.decode_dynamic(self.codec.api.prepare(cat), body)
                self.assertEqual(value.fields['value'], -1.0 if timed_out else 2.0)
                self.assertIn(b'stopped', result.stderr)

    def test_invalid_and_legacy_protocol_never_completes(self):
        cases = [frame('T'), frame('T', struct.pack('>QQ', 1, 1)),
                 frame('D', port_data('input', b'', '')), frame('T', b'bad'),
                 self.activation(port='unknown'), self.activation(index=2)]
        for data in cases:
            with self.subTest(data=data):
                result, events = self.run_node('sink', data)
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual(events, [('R', b'\2')])
        result, events = self.run_node('source', b'', b'{invalid')
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(events, [])

    def test_rebuild_preserves_handler(self):
        before = (self.root / 'Source.java').read_bytes()
        self.rebuild()
        self.assertEqual((self.root / 'Source.java').read_bytes(), before)

    def test_independent_callbacks_coordinate_state_and_failure_sends_no_finished(self):
        sink = self.model['components']['sink']
        sink['inputs'].update(second={'root': 'Measurement'}, passive={'root': 'Measurement', 'mode': 'pull'})
        self.model['subscriptions'].append({'id': 'second', 'from': 'source.out', 'to': 'sink.second'})
        (self.root / 'Sink.java').write_text('''import org.sdlose.generated.sink.API;
import org.sdlose.InputProtocol.Reference;
import sdl.globaltypes.measurement.Measurement;
public class Sink extends API.Handler {
 private Measurement previous;
 public void on_second(API.Context ctx, Reference<Measurement> input) { previous = input.message; }
 public void on_input(API.Context ctx, Reference<Measurement> input) throws Exception {
  if (previous == null) throw new AssertionError("missing component state");
  ctx.publish_result(previous);
  if (input.timedOut()) throw new Exception("expected callback failure");
 }
 public void onStop(API.Context ctx) { System.err.println("stopped"); }
}''')
        self.rebuild()
        _, source = self.run_node('source', timer_frame(1))
        payload = unpack_publication(source[1][1])[1]
        for timed_out in (False, True):
            with self.subTest(timed_out=timed_out):
                data = self.activation('second', payload=payload) + self.activation(
                    payload=None if timed_out else payload, index=2)
                result, frames = self.run_node('sink', data)
                self.assertEqual(result.returncode == 0, not timed_out, result.stderr)
                self.assertEqual([op for op, _ in frames], ['R', 'F', 'P'] if timed_out else ['R', 'F', 'P', 'F'])
                self.assertIn(b'stopped', result.stderr)


class JavaGenerationTests(unittest.TestCase):
    def test_packaged_scaffolds_multiple_schemas_and_space_paths(self):
        with tempfile.TemporaryDirectory() as directory:
            check_generation(self, directory, SDL, 'java')
