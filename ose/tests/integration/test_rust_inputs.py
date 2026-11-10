"""Typed Rust CH/2 callbacks in native and mixed feedback graphs."""
import asyncio
import copy
import io
import json
from pathlib import Path
import tempfile
import subprocess

from ose.codec.sdl2 import SDL2
from ose.execution.activation import InputScheduler
from ose.execution.input_ipc import InputServices
from ose.execution.process import start_component, stop_component, input_events
from ose.execution import ipc, input_ipc
from ose.execution.activation import InputReference
from ose.execution.simulation import Simulation
from ose.modeling.compiler import build
from ose.parameters.bindings import LocalBindings
from ose.parameters.service import ParameterService
from ose.recording.component import ComponentRecording
from tests.integration import test_input_process as python_inputs

A = r'''use crate::container::{Callbacks, Context, InputReference, Result, Selector};
use crate::types::value::Value;
#[derive(Default)] pub struct Handler;
impl Callbacks for Handler {
    fn on_start(&mut self, ctx: &mut Context) -> Result {
        ctx.declare_measurement("feedback.count")?;
        if ctx.config_json.contains("\"arm\": true") { ctx.arm("clock", None)?; }
        Ok(())
    }
    fn on_clock(&mut self, ctx: &mut Context, input: &InputReference<()>) -> Result {
        assert!(input.timed_out() && input.message.is_none());
        if ctx.config_json.contains("\"delay\": 0.01") { std::thread::sleep(std::time::Duration::from_millis(10)); }
        else if ctx.config_json.contains("\"delay\": 0.005") { std::thread::sleep(std::time::Duration::from_millis(5)); }
        if ctx.config_json.contains("\"bypass\": true") { return ctx.publish_out(&Value { count: 9 }); }
        if let Some(previous) = ctx.get_feedback(Selector::default().id(u64::MAX))? {
            let handle = previous.handle.as_ref().unwrap();
            assert_eq!(previous.app_id, Some(u64::MAX));
            assert_eq!(ctx.get_feedback(Selector::default().handle(handle))?.unwrap().message.as_ref().unwrap().count,
                previous.message.as_ref().unwrap().count);
            if ctx.activation_index() == 3 {
                assert!(ctx.remove_feedback(Selector::default().handle(handle))?);
            } else {
                assert_eq!(ctx.pull_feedback(Selector::default().handle(handle))?.unwrap().handle, previous.handle);
            }
            assert!(ctx.get_feedback(Selector::default().handle(handle))?.is_none());
            ctx.measure("feedback.count", previous.message.as_ref().unwrap().count as f64)?;
            if ctx.config_json.contains("\"panic\": true") { panic!("panic after deferred pull"); }
            if ctx.config_json.contains("\"fail\": true") { return Err("failed after deferred pull".into()); }
        }
        ctx.publish_out_with_id(&Value { count: ctx.activation_index() as u32 }, u64::MAX)?;
        if ctx.activation_index() == 3 { ctx.disarm("clock")?; }
        Ok(())
    }
}
'''
B = r'''use crate::container::{Callbacks, Context, InputReference, Result};
use crate::types::value::Value;
#[derive(Default)] pub struct Handler;
impl Callbacks for Handler {
    fn on_input(&mut self, ctx: &mut Context, input: &InputReference<Value>) -> Result {
        if input.timed_out() { assert!(input.message.is_none()); return Ok(()); }
        assert_eq!(input.app_id, Some(u64::MAX));
        ctx.publish_out_with_id(&Value { count: input.message.as_ref().unwrap().count + 100 }, input.app_id.unwrap())
    }
}
'''
SERVICES = r'''use crate::container::{Callbacks, Context, InputReference, Result};
use crate::types::value::Value;
#[derive(Default)] pub struct Handler;
impl Callbacks for Handler {
    fn on_start(&mut self, ctx: &mut Context) -> Result {
        let mut value = ctx.parameters_settings().clone(); assert_eq!(value.count, 40);
        value.count = 41; ctx.set_parameters_settings(&value)?;
        ctx.declare_recording_point::<Value>("debug.value", "")?;
        ctx.declare_measurement("observed.count")?;
        ctx.logf(sdl_ose_component::LogLevel::Info, "", format_args!("initialized"))
    }
    fn on_clock(&mut self, ctx: &mut Context, _input: &InputReference<()>) -> Result {
        let mut value = ctx.parameters_settings().clone();
        ctx.publish_out(&value)?; ctx.record("debug.value", &value)?;
        ctx.measure("observed.count", value.count as f64)?;
        value.count += 1; ctx.set_parameters_settings(&value)
    }
}
'''


class RustInputProcessTests(python_inputs.InputProcessTests):
    SOURCE_LANGUAGE = RESPONSE_LANGUAGE = "rust"
    SOURCE_HANDLER, RESPONSE_HANDLER = A, B

    async def test_panicking_callback_cancels_its_deferred_withdrawals(self):
        plan = copy.deepcopy(self.template)
        plan["components"]["a"]["parameters"] = dict(panic=True)
        engine = Simulation(plan, .1, workers=1)
        with self.assertRaises(asyncio.IncompleteReadError):
            await engine.execute()
        self.assertEqual(engine.summary["inputs"]["a"]["feedback"]["depth"], 1)

    async def test_generated_handler_builds_and_subsequent_builds_preserve_user_code(self):
        with tempfile.TemporaryDirectory(prefix="ose rust scaffold ") as directory:
            root = Path(directory)
            (root / "value.sdl").write_text(python_inputs.SCHEMA)
            node = dict(inputs=dict(clock=dict(kind="timer", timer=dict(mode="periodic", delay=.01),
                activates=dict(timeout=["out"])), feedback=dict(root="Value", mode="pull")),
                outputs=dict(out=dict(root="Value")))
            model = dict(version=2, schemas="value.sdl", components=dict(scaffold_probe=node), subscriptions=[])
            deployment = dict(version=1, components=dict(scaffold_probe=dict(container=dict(language="rust", handler="handler.rs"))))
            (root / "model.json").write_text(json.dumps(model))
            (root / "deployment.json").write_text(json.dumps(deployment))
            build(root / "model.json", root / "deployment.json", python_inputs.SDL_ROOT, self.root / "build")
            handler = root / "handler.rs"
            self.assertIn("fn on_clock", handler.read_text())
            self.assertNotIn("on_tick", handler.read_text())
            self.assertNotIn("fn on_feedback", handler.read_text())
            handler.write_text(A)
            path = build(root / "model.json", root / "deployment.json", python_inputs.SDL_ROOT, self.root / "build")
            self.assertEqual(handler.read_text(), A)
            summary = await Simulation(json.loads(path.read_text()), .1).execute()
            self.assertEqual((summary["ticks"], summary["publications"], summary["stop_reason"]), (3, 3, "quiescent"))

    async def test_empty_push_inactivity_repeats_without_a_message(self):
        # Rebuild the changed declaration so generated callback validation and
        # the compiled plan describe the same model.
        with tempfile.TemporaryDirectory(prefix="ose rust timeout ") as directory:
            root = Path(directory)
            (root / "value.sdl").write_text(python_inputs.SCHEMA)
            (root / "b.rs").write_text(B)
            model = dict(version=2, schemas="value.sdl", subscriptions=[], components=dict(
                b=dict(inputs=dict(input=dict(root="Value", timeout=.01)), outputs=dict(out=dict(root="Value")))))
            deployment = dict(version=1, components=dict(b=dict(container=dict(language="rust", handler="b.rs"))))
            (root / "model.json").write_text(json.dumps(model))
            (root / "deployment.json").write_text(json.dumps(deployment))
            model["components"]["deadline_probe"] = model["components"].pop("b")
            deployment["components"]["deadline_probe"] = deployment["components"].pop("b")
            (root / "model.json").write_text(json.dumps(model))
            (root / "deployment.json").write_text(json.dumps(deployment))
            path = build(root / "model.json", root / "deployment.json", python_inputs.SDL_ROOT, self.root / "build")
            summary = await Simulation(json.loads(path.read_text()), .035).execute()
            self.assertEqual((summary["ticks"], summary["publications"], summary["stop_reason"]), (3, 0, "duration"))

    async def test_input_callbacks_keep_parameter_recording_log_and_measurement_services(self):
        with tempfile.TemporaryDirectory(prefix="ose rust services ") as directory:
            root = Path(directory)
            (root / "value.sdl").write_text(python_inputs.SCHEMA)
            (root / "handler.rs").write_text(SERVICES)
            node = dict(inputs=dict(clock=dict(kind="timer", timer=dict(mode="periodic", delay=.01),
                activates=dict(timeout=["out"]))), outputs=dict(out=dict(root="Value")),
                parameter_bindings=dict(settings=dict(path="shared/settings", access="read_write")))
            model = dict(version=2, schemas="value.sdl", components=dict(probe=node), subscriptions=[],
                shared_parameters={"shared/settings": dict(root="Value", initial=dict(count=40))})
            deployment = dict(version=1, components=dict(probe=dict(container=dict(language="rust", handler="handler.rs"))))
            (root / "model.json").write_text(json.dumps(model))
            (root / "deployment.json").write_text(json.dumps(deployment))
            model["components"]["services_probe"] = model["components"].pop("probe")
            deployment["components"]["services_probe"] = deployment["components"].pop("probe")
            (root / "model.json").write_text(json.dumps(model))
            (root / "deployment.json").write_text(json.dumps(deployment))
            path = build(root / "model.json", root / "deployment.json", python_inputs.SDL_ROOT, self.root / "build")
            plan = json.loads(path.read_text())
            node, codec = plan["components"]["services_probe"], SDL2(python_inputs.SDL_ROOT)
            service = ParameterService(plan, codec)
            self.addCleanup(service.close)
            bindings = LocalBindings(node, service)
            self.addCleanup(bindings.close)
            scheduler = InputScheduler(node, {})
            scheduler.start()
            now, captured = 0, []
            recording = ComponentRecording(codec, active=lambda: {"debug.value"},
                emit=lambda name, desc, payload: captured.append((name, payload)))
            services = InputServices(scheduler, lambda: now)
            process = await start_component(node, bindings, recording, inputs=services)
            try:
                self.assertEqual(process.logs.drain()[0]["message"], "initialized")
                observed = []
                for index in (1, 2):
                    now = index * 10_000_000
                    reference = scheduler.select(now)
                    outputs = [item async for item in input_events(process, node, reference, index, services, bindings)]
                    scheduler.complete(reference, now)
                    value = codec.api.decode_dynamic(codec.api.prepare(node["outputs"]["out"]["catalogue"]), outputs[0][1])
                    observed.append(value.fields["count"])
                    self.assertEqual(captured[-1], ("debug.value", outputs[0][1]))
                self.assertEqual(observed, [41, 42])
                self.assertEqual(len(captured), 2)
                self.assertEqual(process.measurements.snapshot(), {"observed.count": 42.})
            finally:
                await stop_component(process)

    async def test_silent_push_computes_but_the_sdk_prevents_output_activation(self):
        with tempfile.TemporaryDirectory(prefix="ose rust silent push ") as directory:
            root = Path(directory)
            (root / "value.sdl").write_text(python_inputs.SCHEMA)
            (root / "handler.rs").write_text(r'''use crate::container::{Callbacks, Context, InputReference, Result};
use crate::types::value::Value;
#[derive(Default)] pub struct Handler;
impl Callbacks for Handler {
    fn on_clock(&mut self, ctx: &mut Context, _input: &InputReference<()>) -> Result {
        ctx.publish_out_with_id(&Value { count: 1 }, 0)
    }
    fn on_feedback(&mut self, ctx: &mut Context, input: &InputReference<Value>) -> Result {
        assert_eq!(input.message.as_ref().unwrap().count, 1);
        assert_eq!(input.app_id, Some(0));
        assert!(ctx.publish_out(&Value { count: 2 }).is_err());
        Ok(())
    }
}''')
            node = dict(inputs=dict(clock=dict(kind="timer", timer=dict(mode="one_shot", delay=.01),
                activates=dict(timeout=["out"])), feedback=dict(root="Value")), outputs=dict(out=dict(root="Value")))
            model = dict(version=2, schemas="value.sdl", components=dict(push_probe=node), subscriptions=[
                {"id": "feedback", "from": "push_probe.out", "to": "push_probe.feedback"}])
            deployment = dict(version=1, components=dict(push_probe=dict(container=dict(language="rust", handler="handler.rs"))))
            (root / "model.json").write_text(json.dumps(model))
            (root / "deployment.json").write_text(json.dumps(deployment))
            path = build(root / "model.json", root / "deployment.json", python_inputs.SDL_ROOT, self.root / "build")
            summary = await Simulation(json.loads(path.read_text()), .1).execute()
            self.assertEqual((summary["ticks"], summary["publications"], summary["stop_reason"]), (2, 1, "quiescent"))

    def test_malformed_or_out_of_scope_activation_never_finishes_a_callback(self):
        node = self.template["components"]["a"]
        skipped = InputReference("clock", "timeout", 10_000_000, 10_000_000, 1, None, frozenset())
        passive = InputReference("feedback", "timeout", 10_000_000, 10_000_000, 1, None, frozenset())
        data = input_ipc.activation_data(skipped, 1)
        for malformed in (b"", data[:32], bytes(32) + data[32:],
                          input_ipc.activation_data(skipped, 2), input_ipc.activation_data(passive, 1)):
            with self.subTest(data=malformed[:32]):
                result = subprocess.run(node["command"], input=ipc.frame("I", json.dumps(node).encode())
                    + ipc.frame("T", malformed) + ipc.frame("X"), capture_output=True, timeout=5)
                self.assertNotEqual(result.returncode, 0)
                stream, events = io.BytesIO(result.stdout), []
                while stream.tell() < len(result.stdout):
                    size = int.from_bytes(stream.read(4), "big")
                    events.append(stream.read(size))
                self.assertIn(b"R\x02", events)
                self.assertFalse(any(event[:1] == b"F" for event in events))


class MixedInputProcessTests(RustInputProcessTests):
    RESPONSE_LANGUAGE = "python"
    RESPONSE_HANDLER = python_inputs.B
    # These two tests build standalone Rust fixtures, independent of peer language.
    test_empty_push_inactivity_repeats_without_a_message = None
    test_input_callbacks_keep_parameter_recording_log_and_measurement_services = None
    test_silent_push_computes_but_the_sdk_prevents_output_activation = None
    test_malformed_or_out_of_scope_activation_never_finishes_a_callback = None
    test_panicking_callback_cancels_its_deferred_withdrawals = None
    test_generated_handler_builds_and_subsequent_builds_preserve_user_code = None


class PythonToRustInputProcessTests(python_inputs.InputProcessTests):
    RESPONSE_LANGUAGE = "rust"
    RESPONSE_HANDLER = B
    # Keep the graph-level checks; standalone Python services and CH/2 parser
    # checks are already covered by their own fixtures.
    test_empty_push_inactivity_repeats_without_a_message = None
    test_input_callbacks_keep_parameter_recording_log_and_measurement_services = None
    test_manual_timer_can_be_armed_during_initialization = None
    test_model_two_explicitly_rejects_an_old_ready = None
    test_parent_checks_output_rights_even_if_the_sdk_is_bypassed = None
    test_pull_over_ch_two_decodes_a_different_catalogue_on_each_message = None
