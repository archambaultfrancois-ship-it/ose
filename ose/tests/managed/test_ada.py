"""Ada CH/2 executable boundaries; missing GNAT is an explicit failure."""
import io
import json
import os
from pathlib import Path
import struct
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from ose.codec.sdl2 import SDL2
from ose.execution.activation import InputReference
from ose.execution.input_ipc import activation_data, unpack_publication
from ose.execution.ipc import frame, port_data
from ose.modeling import ada_containers
from ose.modeling.compiler import build, compile_plan
from ose.modeling.managed_containers import scaffold_managed
from generation_cases import check_generation
from tests.component_driver import timer_frame

SDL = Path(os.environ.get('SDL_ROOT', Path(__file__).resolve().parents[3] / "sdl"))


class AdaTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root / 'measurement.sdl').write_text('message Measurement {1: required uint64 sequence; 2: required fl64 value;}')
        self.model = {'version': 2, 'schemas': 'measurement.sdl', 'components': {
            'source': {'inputs': {'clock': {'kind': 'timer', 'timer': {'mode': 'periodic', 'delay': .01},
                                          'activates': {'timeout': ['value']}}},
                       'outputs': {'value': {'root': 'Measurement'}}},
            'sink': {'inputs': {'input': {'root': 'Measurement', 'timeout': .1,
                                         'activates': {'message': ['result'], 'timeout': ['result']}}},
                     'outputs': {'result': {'root': 'Measurement'}}}},
            'subscriptions': [{'id': 'link', 'from': 'source.value', 'to': 'sink.input'}]}
        deployment = {'version': 1, 'components': {
            name: {'container': {'language': 'ada', 'handler': name + '_handler.adb', 'unit': name.title() + '_Handler'}}
            for name in self.model['components']}}
        (self.root / 'model.json').write_text(json.dumps(self.model))
        (self.root / 'deployment.json').write_text(json.dumps(deployment))
        plan = compile_plan(self.root / 'model.json', self.root / 'deployment.json', SDL, self.root / 'build')
        for node in plan['components'].values():
            scaffold_managed(node, node['container'])
        (self.root / 'source_handler.adb').write_text('''with SDL_measurement; with SDL_Runtime; with Interfaces;
package body Source_Handler is
 overriding procedure On_clock (Self : in out Object; Ctx : in out Ose_source.Context; Input : Ose_source.Input_clock) is
   M : SDL_measurement.T_Measurement;
 begin
   M.F_sequence := Interfaces.Unsigned_64'Last;
   M.F_value := SDL_Runtime.Float64 (Ose_source.Now (Ctx));
   Ose_source.Publish_value (Ctx, M, Interfaces.Unsigned_64'Last);
 end;
end Source_Handler;
''')
        (self.root / 'sink_handler.adb').write_text('''with SDL_measurement; with SDL_Runtime; with Interfaces;
package body Sink_Handler is
 use type SDL_Runtime.Float64;
 overriding procedure On_input (Self : in out Object; Ctx : in out Ose_sink.Context; Input : Ose_sink.Input_input) is
   M : SDL_measurement.T_Measurement;
 begin
   if Input.Present then M := Input.Message;
   else M.F_sequence := Interfaces.Unsigned_64'Last; M.F_value := -1.0; end if;
   Ose_sink.Publish_result (Ctx, M, Input.Meta.App_Id);
 end;
end Sink_Handler;
''')
        self.rebuild()
        self.codec = SDL2(SDL)

    def rebuild(self):
        (self.root / 'model.json').write_text(json.dumps(self.model))
        path = build(self.root / 'model.json', self.root / 'deployment.json', SDL, self.root / 'build')
        self.plan = json.loads(path.read_text())

    def run_node(self, name, events):
        result = subprocess.run(self.plan['components'][name]['command'],
                                input=frame('I', b'{}') + events + frame('X'),
                                capture_output=True, timeout=15)
        stream, frames = io.BytesIO(result.stdout), []
        while stream.tell() < len(result.stdout):
            size, = struct.unpack('>I', stream.read(4))
            value = stream.read(size)
            frames.append((chr(value[0]), value[1:]))
        return result, frames

    def activation(self, port='input', *, payload=None, index=1, app_id=None):
        record = None
        if payload is not None:
            cat = self.plan['components']['sink']['inputs']['input']['catalogue']
            record = dict(id='source:1', topic='source.value', catalogue=cat,
                          body=self.codec.wrap(cat, payload), received_ns=0)
            if app_id is not None:
                record['app_id'] = app_id
        reference = InputReference(port, 'message' if record else 'timeout',
                                   2_000_000_000, 2_000_000_000, 1, record, frozenset())
        return frame('T', activation_data(reference, index))

    def test_typed_references_uint64_ids_timeout_and_legacy_rejection(self):
        result, events = self.run_node('source', timer_frame(1, time_ns=2_000_000_000))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual([op for op, _ in events], ['R', 'P', 'F'])
        self.assertEqual(events[0][1], b'\2')
        port, payload, app_id = unpack_publication(events[1][1])
        self.assertEqual((port, app_id), ('value', 2**64 - 1))
        cat = self.plan['components']['source']['outputs']['value']['catalogue']
        value = self.codec.api.decode_dynamic(self.codec.api.prepare(cat), payload)
        self.assertEqual(value.fields, {'sequence': 2**64 - 1, 'value': 2.0})
        for timed_out in (False, True):
            result, frames = self.run_node('sink', self.activation(payload=None if timed_out else payload, app_id=app_id))
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual([op for op, _ in frames], ['R', 'P', 'F'])
            output, body, identity = unpack_publication(frames[1][1])
            self.assertEqual(output, 'result')
            self.assertEqual(identity, None if timed_out else app_id)
            value = self.codec.api.decode_dynamic(self.codec.api.prepare(cat), body)
            self.assertEqual(value.fields['value'], -1.0 if timed_out else 2.0)
        for events in (frame('T'), frame('T', struct.pack('>QQ', 1, 1)),
                       frame('D', port_data('input', b'', '')), frame('T', b'bad'),
                       self.activation(port='unknown'), self.activation(index=2)):
            result, frames = self.run_node('sink', events)
            self.assertNotEqual(result.returncode, 0)
            self.assertEqual(frames, [('R', b'\2')])

    def test_independent_callbacks_failure_and_rebuild_with_equal_timestamps(self):
        api = self.root / 'build/components/sink'
        timestamps = {api / ('ose_sink' + suffix): (api / ('ose_sink' + suffix)).stat().st_mtime_ns
                      for suffix in ('.ads', '.adb')}
        self.model['components']['sink']['inputs'].update(
            second={'root': 'Measurement'}, passive={'root': 'Measurement', 'mode': 'pull'})
        self.model['subscriptions'].append({'id': 'second', 'from': 'source.value', 'to': 'sink.second'})
        (self.root / 'sink_handler.ads').write_text('''with Ose_sink; with SDL_measurement;
package Sink_Handler is
 type Object is new Ose_sink.Handler with record
  Previous : SDL_measurement.T_Measurement;
  Seen : Boolean := False;
 end record;
 overriding procedure On_second (Self : in out Object; Ctx : in out Ose_sink.Context; Input : Ose_sink.Input_second);
 overriding procedure On_input (Self : in out Object; Ctx : in out Ose_sink.Context; Input : Ose_sink.Input_input);
end Sink_Handler;
''')
        (self.root / 'sink_handler.adb').write_text('''package body Sink_Handler is
 overriding procedure On_second (Self : in out Object; Ctx : in out Ose_sink.Context; Input : Ose_sink.Input_second) is
 begin Self.Previous := Input.Message; Self.Seen := True; end;
 overriding procedure On_input (Self : in out Object; Ctx : in out Ose_sink.Context; Input : Ose_sink.Input_input) is
 begin
  if not Self.Seen then raise Program_Error with "missing component state"; end if;
  Ose_sink.Publish_result (Ctx, Self.Previous);
  if not Input.Present then raise Program_Error with "expected callback failure"; end if;
 end;
end Sink_Handler;
''')
        generate = ada_containers.generate
        def regenerate_with_same_timestamps(*args, **kwargs):
            generated = generate(*args, **kwargs)
            # GNAT must rebuild even if regenerated API sources retain their timestamps.
            for source, timestamp in timestamps.items():
                os.utime(source, ns=(timestamp, timestamp))
            return generated
        with patch.object(ada_containers, 'generate', side_effect=regenerate_with_same_timestamps):
            self.rebuild()
        _, source = self.run_node('source', timer_frame(1))
        payload = unpack_publication(source[1][1])[1]
        for timed_out in (False, True):
            events = self.activation('second', payload=payload) + self.activation(
                payload=None if timed_out else payload, index=2)
            result, frames = self.run_node('sink', events)
            self.assertEqual(result.returncode == 0, not timed_out, result.stderr)
            self.assertEqual([op for op, _ in frames], ['R', 'F', 'P'] if timed_out else ['R', 'F', 'P', 'F'])


class AdaGenerationTests(unittest.TestCase):
    def test_child_units_multiple_schemas_and_space_paths(self):
        with tempfile.TemporaryDirectory() as directory:
            check_generation(self, directory, SDL, 'ada')
