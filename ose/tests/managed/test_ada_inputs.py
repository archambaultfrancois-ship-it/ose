"""Ada CH/2 qualification; GNAT absence is a failure, never a passing skip."""
import asyncio
import copy
import json
from pathlib import Path
import subprocess
import tempfile

from ose.codec.sdl2 import SDL2
from ose.execution.activation import InputScheduler
from ose.execution.input_ipc import InputServices
from ose.execution.process import start_component, stop_component, input_events
from ose.execution.simulation import Simulation
from ose.modeling.compiler import build
from ose.modeling.managed_containers import tool
from ose.parameters.bindings import LocalBindings
from ose.parameters.service import ParameterService
from ose.recording.component import ComponentRecording
from tests.integration import test_input_process as python_inputs
from tests.integration import test_c_inputs as c_inputs
from tests.managed import ada_input_handlers as handlers


class AdaInputProcessTests(python_inputs.InputProcessTests):
    SOURCE_LANGUAGE = RESPONSE_LANGUAGE = "ada"
    SOURCE_HANDLER, RESPONSE_HANDLER = handlers.A, handlers.B
    SOURCE_SPEC, RESPONSE_SPEC = handlers.A_SPEC, handlers.B_SPEC

    @classmethod
    def setUpClass(cls):
        subprocess.run(tool('GNATMAKE', 'gnatmake') + ['--version'], check=True, capture_output=True)
        super().setUpClass()

    test_malformed_or_out_of_scope_activation_never_finishes_a_callback = c_inputs.CInputProcessTests.test_malformed_or_out_of_scope_activation_never_finishes_a_callback
    test_invalid_input_replies_fail_without_finished = c_inputs.CInputProcessTests.test_invalid_input_replies_fail_without_finished

    async def test_failed_subprocess_cancels_its_deferred_withdrawal(self):
        plan = copy.deepcopy(self.template)
        plan["components"] = {name: plan["components"][name] for name in ("a", "b")}
        plan["subscriptions"] = [sub for sub in plan["subscriptions"] if sub["from"].split(".")[0] in ("a", "b")]
        plan["components"]["a"]["parameters"] = dict(fail=True)
        engine = Simulation(plan, .1, workers=1)
        with self.assertRaises(asyncio.IncompleteReadError):
            await engine.execute()
        self.assertEqual(engine.summary["stop_reason"], "failed")
        self.assertEqual(engine.summary["inputs"]["a"]["feedback"]["depth"], 1)
        self.assertEqual(engine.summary["publications"], 2)

    def fixture(self, root, name, node, spec, body, *, shared=None):
        (root / 'value.sdl').write_text(python_inputs.SCHEMA)
        for suffix, text in (('.ads', spec), ('.adb', body)):
            (root / ('probe_handler' + suffix)).write_text(text.replace('COMPONENT_NAME', name).replace('UNIT_NAME', 'Probe_Handler'))
        model = dict(version=2, schemas='value.sdl', components={name: node}, subscriptions=[])
        if shared:
            model['shared_parameters'] = shared
        deployment = dict(version=1, components={name: dict(container=dict(language='ada', handler='probe_handler.adb', unit='Probe_Handler'))})
        (root / 'model.json').write_text(json.dumps(model)); (root / 'deployment.json').write_text(json.dumps(deployment))
        path = build(root / 'model.json', root / 'deployment.json', python_inputs.SDL_ROOT, self.root / 'build')
        return json.loads(path.read_text())

    async def test_empty_push_inactivity_repeats_without_a_message(self):
        with tempfile.TemporaryDirectory(prefix='ose ada timeout ') as directory:
            node = dict(inputs=dict(input=dict(root='Value', timeout=.01)), outputs=dict(out=dict(root='Value')))
            plan = self.fixture(Path(directory), 'deadline_probe', node, handlers.B_SPEC, handlers.B)
            summary = await Simulation(plan, .035).execute()
            self.assertEqual((summary['ticks'], summary['publications'], summary['stop_reason']), (3, 0, 'duration'))

    async def test_generated_handler_builds_and_subsequent_builds_preserve_user_code(self):
        with tempfile.TemporaryDirectory(prefix='ose ada scaffold ') as directory:
            root = Path(directory)
            node = dict(inputs=dict(clock=dict(kind='timer', timer=dict(mode='periodic', delay=.01), activates=dict(timeout=['out'])),
                feedback=dict(root='Value', mode='pull')), outputs=dict(out=dict(root='Value')))
            (root / 'value.sdl').write_text(python_inputs.SCHEMA)
            model = dict(version=2, schemas='value.sdl', components=dict(scaffold_probe=node), subscriptions=[])
            deployment = dict(version=1, components=dict(scaffold_probe=dict(container=dict(language='ada', handler='probe_handler.adb', unit='Probe_Handler'))))
            (root / 'model.json').write_text(json.dumps(model)); (root / 'deployment.json').write_text(json.dumps(deployment))
            build(root / 'model.json', root / 'deployment.json', python_inputs.SDL_ROOT, self.root / 'build')
            self.assertNotIn('On_Tick', (root / 'probe_handler.ads').read_text())
            self.assertNotIn('On_feedback', (root / 'probe_handler.ads').read_text())
            plan = self.fixture(root, 'scaffold_probe', node, handlers.A_SPEC, handlers.A)
            before = {p: p.read_bytes() for p in (root / 'probe_handler.ads', root / 'probe_handler.adb')}
            build(root / 'model.json', root / 'deployment.json', python_inputs.SDL_ROOT, self.root / 'build')
            self.assertEqual({p: p.read_bytes() for p in before}, before)
            summary = await Simulation(plan, .1).execute()
            self.assertEqual((summary['ticks'], summary['publications'], summary['stop_reason']), (3, 3, 'quiescent'))

    async def test_input_callbacks_keep_parameter_recording_log_and_measurement_services(self):
        body = '''with SDL_Value; with SDL_Runtime; with Ose_Component; with Ose_Recording;
with Interfaces;
package body UNIT_NAME is
   use type Interfaces.Unsigned_32;
   use type Ose_Recording.Emit_Result;
   overriding procedure On_Start (Self : in out Object; Ctx : in out Ose_COMPONENT_NAME.Context) is
      pragma Unreferenced (Self);
      Value : SDL_Value.T_Value := Ose_COMPONENT_NAME.Parameters_settings (Ctx);
   begin
      pragma Assert (Value.F_count = 40); Value.F_count := 41;
      Ose_COMPONENT_NAME.Set_Parameters_settings (Ctx, Value);
      Ose_COMPONENT_NAME.Declare_Recording_Value (Ctx, "debug.value");
      Ose_COMPONENT_NAME.Declare_Measurement (Ctx, "observed.count");
      Ose_COMPONENT_NAME.Log (Ctx, Ose_Component.Info, "", "initialized");
   end;
   overriding procedure On_clock (Self : in out Object; Ctx : in out Ose_COMPONENT_NAME.Context; Input : Ose_COMPONENT_NAME.Input_clock) is
      pragma Unreferenced (Self, Input);
      Value : SDL_Value.T_Value := Ose_COMPONENT_NAME.Parameters_settings (Ctx);
      Emitted : Ose_Recording.Emit_Result;
   begin
      Ose_COMPONENT_NAME.Publish_out (Ctx, Value);
      Emitted := Ose_COMPONENT_NAME.Record_Value (Ctx, "debug.value", Value); pragma Assert (Emitted = Ose_Recording.Queued);
      Ose_COMPONENT_NAME.Measure (Ctx, "observed.count", SDL_Runtime.Float64 (Value.F_count));
      Value.F_count := Value.F_count + 1; Ose_COMPONENT_NAME.Set_Parameters_settings (Ctx, Value);
   end;
end UNIT_NAME;
'''
        with tempfile.TemporaryDirectory(prefix='ose ada services ') as directory:
            node = dict(inputs=dict(clock=dict(kind='timer', timer=dict(mode='periodic', delay=.01), activates=dict(timeout=['out']))),
                outputs=dict(out=dict(root='Value')), parameter_bindings=dict(settings=dict(path='shared/settings', access='read_write')))
            plan = self.fixture(Path(directory), 'services_probe', node, handlers.A_SPEC, body,
                shared={'shared/settings': dict(root='Value', initial=dict(count=40))})
            node, codec = plan['components']['services_probe'], SDL2(python_inputs.SDL_ROOT)
            service = ParameterService(plan, codec); self.addCleanup(service.close)
            bindings = LocalBindings(node, service); self.addCleanup(bindings.close)
            scheduler = InputScheduler(node, {}); scheduler.start()
            now, captured = 0, []
            recording = ComponentRecording(codec, active=lambda: {'debug.value'}, emit=lambda name, desc, payload: captured.append((name, payload)))
            services = InputServices(scheduler, lambda: now)
            process = await start_component(node, bindings, recording, inputs=services)
            try:
                self.assertEqual(process.logs.drain()[0]['message'], 'initialized')
                counts = []
                for index in (1, 2):
                    now = index * 10_000_000
                    reference = scheduler.select(now)
                    outputs = [item async for item in input_events(process, node, reference, index, services, bindings)]
                    scheduler.complete(reference, now)
                    counts.append(codec.api.decode_dynamic(codec.api.prepare(node['outputs']['out']['catalogue']), outputs[0][1]).fields['count'])
                    self.assertEqual(captured[-1], ('debug.value', outputs[0][1]))
                self.assertEqual(counts, [41, 42]); self.assertEqual(process.measurements.snapshot(), {'observed.count': 42.})
            finally:
                await stop_component(process)

    async def test_silent_push_preserves_absent_zero_ids_and_enforces_context_scope(self):
        spec = '''with Ose_COMPONENT_NAME;
package UNIT_NAME is
   type Object is new Ose_COMPONENT_NAME.Handler with null record;
   overriding procedure On_Start (Self : in out Object; Ctx : in out Ose_COMPONENT_NAME.Context);
   overriding procedure On_Stop (Self : in out Object; Ctx : in out Ose_COMPONENT_NAME.Context);
   overriding procedure On_clock (Self : in out Object; Ctx : in out Ose_COMPONENT_NAME.Context; Input : Ose_COMPONENT_NAME.Input_clock);
   overriding procedure On_feedback (Self : in out Object; Ctx : in out Ose_COMPONENT_NAME.Context; Input : Ose_COMPONENT_NAME.Input_feedback);
end UNIT_NAME;
'''
        body = '''with Ada.Strings.Fixed; with Interfaces; with SDL_Value; with Ose_Component;
package body UNIT_NAME is
   use type Interfaces.Unsigned_32;
   use type Interfaces.Unsigned_64;
   function No_Id (Ctx : Ose_COMPONENT_NAME.Context) return Boolean is
     (Ada.Strings.Fixed.Index (Ose_COMPONENT_NAME.Config_JSON (Ctx), """no_id"": true") > 0);
   overriding procedure On_Start (Self : in out Object; Ctx : in out Ose_COMPONENT_NAME.Context) is
      pragma Unreferenced (Self);
   begin
      begin Ose_COMPONENT_NAME.Arm (Ctx, "clock", 0); raise Constraint_Error with "zero delay accepted";
      exception when Ose_Component.Protocol_Error => null; end;
      declare Value : Ose_COMPONENT_NAME.Input_cache;
      begin Value := Ose_COMPONENT_NAME.Get_cache (Ctx); raise Constraint_Error with "Pull during on_start";
      exception when Program_Error => null; end;
   end;
   overriding procedure On_clock (Self : in out Object; Ctx : in out Ose_COMPONENT_NAME.Context; Input : Ose_COMPONENT_NAME.Input_clock) is
      pragma Unreferenced (Self, Input);
      Value : SDL_Value.T_Value;
   begin
      Value.F_count := 1;
      if No_Id (Ctx) then Ose_COMPONENT_NAME.Publish_out (Ctx, Value);
      else Ose_COMPONENT_NAME.Publish_out (Ctx, Value, Interfaces.Unsigned_64'(0)); end if;
   end;
   overriding procedure On_feedback (Self : in out Object; Ctx : in out Ose_COMPONENT_NAME.Context; Input : Ose_COMPONENT_NAME.Input_feedback) is
      pragma Unreferenced (Self);
   begin
      pragma Assert (Input.Present and then Input.Message.F_count + 1 = 2);
      pragma Assert (Input.Meta.App_Id.Present /= No_Id (Ctx));
      if Input.Meta.App_Id.Present then pragma Assert (Input.Meta.App_Id.Value = 0); end if;
      begin Ose_COMPONENT_NAME.Publish_out (Ctx, Input.Message); raise Constraint_Error with "forbidden output";
      exception when Program_Error => null; end;
   end;
   overriding procedure On_Stop (Self : in out Object; Ctx : in out Ose_COMPONENT_NAME.Context) is
      pragma Unreferenced (Self);
   begin
      begin Ose_COMPONENT_NAME.Arm (Ctx, "clock"); raise Constraint_Error with "timer allowed on_stop";
      exception when Program_Error => null; end;
   end;
end UNIT_NAME;
'''
        with tempfile.TemporaryDirectory(prefix='ose ada scope ') as directory:
            node = dict(inputs=dict(clock=dict(kind='timer', timer=dict(mode='one_shot', delay=.01), activates=dict(timeout=['out'])),
                feedback=dict(root='Value'), cache=dict(root='Value', mode='pull')), outputs=dict(out=dict(root='Value')))
            plan = self.fixture(Path(directory), 'push_probe', node, spec, body)
            plan['subscriptions'] = [dict(id='self', **{'from': 'push_probe.out', 'to': 'push_probe.feedback'},
                policy=python_inputs.SubscriptionPolicy().to_dict())]
            for no_id in (False, True):
                plan['components']['push_probe']['parameters'] = dict(no_id=no_id)
                summary = await Simulation(plan, .1).execute()
                self.assertEqual((summary['ticks'], summary['publications'], summary['stop_reason']), (2, 1, 'quiescent'))


class AdaToPythonInputProcessTests(AdaInputProcessTests):
    RESPONSE_LANGUAGE = 'python'
    RESPONSE_HANDLER = python_inputs.B
    test_empty_push_inactivity_repeats_without_a_message = None
    test_input_callbacks_keep_parameter_recording_log_and_measurement_services = None
    test_generated_handler_builds_and_subsequent_builds_preserve_user_code = None
    test_malformed_or_out_of_scope_activation_never_finishes_a_callback = None
    test_invalid_input_replies_fail_without_finished = None
    test_silent_push_preserves_absent_zero_ids_and_enforces_context_scope = None


class PythonToAdaInputProcessTests(AdaToPythonInputProcessTests):
    SOURCE_LANGUAGE = 'python'
    SOURCE_HANDLER = python_inputs.A
    RESPONSE_LANGUAGE = 'ada'
    RESPONSE_HANDLER = handlers.B
    test_manual_timer_can_be_armed_during_initialization = None
    test_model_two_explicitly_rejects_an_old_ready = None
    test_parent_checks_output_rights_even_if_the_sdk_is_bypassed = None
    test_pull_over_ch_two_decodes_a_different_catalogue_on_each_message = None

