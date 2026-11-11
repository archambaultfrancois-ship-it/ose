"""Java CH/2 typed callbacks through native and mixed-language local engines."""
from tests.component_driver import run_component
import asyncio
import copy
import io
import json
from pathlib import Path
import subprocess
import tempfile

from ose.codec.sdl2 import SDL2
from ose.execution.activation import InputScheduler, InputReference
from ose.execution import input_ipc, ipc
from ose.execution.input_ipc import InputServices
from ose.execution.process import start_component, stop_component, input_events
from ose.execution.simulation import Simulation
from ose.modeling.compiler import build
from ose.parameters.bindings import LocalBindings
from ose.parameters.service import ParameterService
from ose.recording.component import ComponentRecording
from tests.integration import test_input_process as python_inputs

A = r'''import org.sdlose.generated.COMPONENT_NAME.API;
import org.sdlose.InputProtocol.Reference;
import org.sdlose.InputProtocol.Selector;
import java.math.BigInteger;
import sdl.globaltypes.value.Value;
public final class HANDLER_NAME extends API.Handler {
    private static final BigInteger ID = BigInteger.ONE.shiftLeft(64).subtract(BigInteger.ONE);
    @Override public void onStart(API.Context ctx) throws Exception {
        ctx.declareMeasurement("feedback.count");
        if (ctx.configJson.contains("\"arm\": true")) ctx.arm("clock");
    }
    @Override public void on_clock(API.Context ctx, Reference<Void> input) throws Exception {
        if (!input.timedOut() || input.message != null) throw new AssertionError("timer reference");
        if (ctx.configJson.contains("\"delay\": 0.01")) Thread.sleep(10);
        else if (ctx.configJson.contains("\"delay\": 0.005")) Thread.sleep(5);
        if (ctx.configJson.contains("\"bypass\": true")) {
            org.sdlose.Component.send(org.sdlose.NativeTransport.instance().output(), 'P', new byte[]{0, 3, 'o', 'u', 't', 0, 1});
            return;
        }
        Reference<Value> previous = ctx.get_feedback(new Selector().id(ID));
        if (previous != null) {
            Selector exact = new Selector().id(ID).handle(previous.handle).source(previous.source);
            if (!ID.equals(previous.appId) || ctx.get_feedback(exact).message.count != previous.message.count)
                throw new AssertionError("repeatable read");
            if (ctx.activationIndex().intValue() == 3) {
                if (!ctx.remove_feedback(exact)) throw new AssertionError("remove");
            } else if (!ctx.pull_feedback(exact).handle.equals(previous.handle)) throw new AssertionError("pull");
            if (ctx.get_feedback(exact) != null) throw new AssertionError("hidden withdrawal");
            ctx.measure("feedback.count", previous.message.count);
            if (ctx.configJson.contains("\"fail\": true")) {
                Value staged = new Value(); staged.count = 999; ctx.publish_out(staged);
                throw new Exception("failed after deferred pull and staged publication");
            }
            if (ctx.configJson.contains("\"error\": true")) throw new AssertionError("error after deferred pull");
        }
        Value value = new Value(); value.count = ctx.activationIndex().intValue();
        ctx.publish_out(value, ID);
        if (ctx.activationIndex().intValue() == 3) ctx.disarm("clock");
    }
}
'''
B = r'''import org.sdlose.generated.COMPONENT_NAME.API;
import org.sdlose.InputProtocol.Reference;
import java.math.BigInteger;
import sdl.globaltypes.value.Value;
public final class HANDLER_NAME extends API.Handler {
    @Override public void on_input(API.Context ctx, Reference<Value> input) throws Exception {
        if (input.timedOut()) {
            if (input.message != null) throw new AssertionError("timeout message");
            return;
        }
        if (!BigInteger.ONE.shiftLeft(64).subtract(BigInteger.ONE).equals(input.appId)) throw new AssertionError("ID");
        Value value = new Value(); value.count = input.message.count + 100;
        ctx.publish_out(value, input.appId);
    }
}
'''
SERVICES = r'''import org.sdlose.generated.services_probe.API;
import org.sdlose.InputProtocol.Reference;
import org.sdlose.Component;
import sdl.globaltypes.value.Value;
public final class Services extends API.Handler {
    @Override public void onStart(API.Context ctx) throws Exception {
        Value value = ctx.parameters_settings();
        if (value.count != 40) throw new AssertionError("initial parameter");
        value.count = 41; ctx.set_parameters_settings(value);
        ctx.declareRecordingPoint("debug.value", Value.class, "");
        ctx.declareMeasurement("observed.count");
        ctx.logf(Component.LogLevel.INFO, "", "%s", "initialized");
    }
    @Override public void on_clock(API.Context ctx, Reference<Void> input) throws Exception {
        Value value = ctx.parameters_settings(); ctx.publish_out(value);
        if (ctx.record("debug.value", value) != Component.RecordResult.QUEUED) throw new AssertionError("record");
        ctx.measure("observed.count", value.count);
        value.count++; ctx.set_parameters_settings(value);
    }
}
'''


class JavaInputProcessTests(python_inputs.InputProcessTests):
    SOURCE_LANGUAGE = RESPONSE_LANGUAGE = "java"
    SOURCE_HANDLER, RESPONSE_HANDLER = A, B

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

    async def test_error_cancels_its_deferred_withdrawals(self):
        plan = copy.deepcopy(self.template)
        plan["components"]["a"]["parameters"] = dict(error=True)
        engine = Simulation(plan, .1, workers=1)
        with self.assertRaises(asyncio.IncompleteReadError):
            await engine.execute()
        self.assertEqual(engine.summary["inputs"]["a"]["feedback"]["depth"], 1)

    async def test_empty_push_inactivity_repeats_without_a_message(self):
        with tempfile.TemporaryDirectory(prefix="ose java timeout ") as directory:
            root = Path(directory)
            (root / "value.sdl").write_text(python_inputs.SCHEMA)
            (root / "Deadline.java").write_text(B.replace("COMPONENT_NAME", "deadline_probe").replace("HANDLER_NAME", "Deadline"))
            node = dict(inputs=dict(input=dict(root="Value", timeout=.01)), outputs=dict(out=dict(root="Value")))
            model = dict(version=2, schemas="value.sdl", components=dict(deadline_probe=node), subscriptions=[])
            deployment = dict(version=1, components=dict(deadline_probe=dict(container=dict(language="java", handler="Deadline.java", **{"class": "Deadline"}))))
            (root / "model.json").write_text(json.dumps(model)); (root / "deployment.json").write_text(json.dumps(deployment))
            path = build(root / "model.json", root / "deployment.json", python_inputs.SDL_ROOT, self.root / "build")
            summary = await Simulation(json.loads(path.read_text()), .035).execute()
            self.assertEqual((summary["ticks"], summary["publications"], summary["stop_reason"]), (3, 0, "duration"))

    async def test_generated_handler_builds_and_subsequent_builds_preserve_user_code(self):
        with tempfile.TemporaryDirectory(prefix="ose java scaffold ") as directory:
            root = Path(directory)
            (root / "value.sdl").write_text(python_inputs.SCHEMA)
            node = dict(inputs=dict(clock=dict(kind="timer", timer=dict(mode="periodic", delay=.01),
                activates=dict(timeout=["out"])), feedback=dict(root="Value", mode="pull")), outputs=dict(out=dict(root="Value")))
            model = dict(version=2, schemas="value.sdl", components=dict(scaffold_probe=node), subscriptions=[])
            deployment = dict(version=1, components=dict(scaffold_probe=dict(container=dict(language="java", handler="Scaffold.java", **{"class": "Scaffold"}))))
            (root / "model.json").write_text(json.dumps(model)); (root / "deployment.json").write_text(json.dumps(deployment))
            build(root / "model.json", root / "deployment.json", python_inputs.SDL_ROOT, self.root / "build")
            handler = root / "Scaffold.java"
            self.assertIn("on_clock", handler.read_text()); self.assertNotIn("onTick", handler.read_text())
            self.assertNotIn("on_feedback", handler.read_text())
            handler.write_text(A.replace("COMPONENT_NAME", "scaffold_probe").replace("HANDLER_NAME", "Scaffold"))
            before = handler.read_bytes()
            path = build(root / "model.json", root / "deployment.json", python_inputs.SDL_ROOT, self.root / "build")
            self.assertEqual(handler.read_bytes(), before)
            summary = await Simulation(json.loads(path.read_text()), .1).execute()
            self.assertEqual((summary["ticks"], summary["publications"], summary["stop_reason"]), (3, 3, "quiescent"))

    async def test_input_callbacks_keep_parameter_recording_log_and_measurement_services(self):
        with tempfile.TemporaryDirectory(prefix="ose java services ") as directory:
            root = Path(directory)
            (root / "value.sdl").write_text(python_inputs.SCHEMA)
            (root / "Services.java").write_text(SERVICES)
            node = dict(inputs=dict(clock=dict(kind="timer", timer=dict(mode="periodic", delay=.01),
                activates=dict(timeout=["out"]))), outputs=dict(out=dict(root="Value")),
                parameter_bindings=dict(settings=dict(path="shared/settings", access="read_write")))
            model = dict(version=2, schemas="value.sdl", components=dict(services_probe=node), subscriptions=[],
                shared_parameters={"shared/settings": dict(root="Value", initial=dict(count=40))})
            deployment = dict(version=1, components=dict(services_probe=dict(container=dict(language="java", handler="Services.java", **{"class": "Services"}))))
            (root / "model.json").write_text(json.dumps(model)); (root / "deployment.json").write_text(json.dumps(deployment))
            path = build(root / "model.json", root / "deployment.json", python_inputs.SDL_ROOT, self.root / "build")
            plan = json.loads(path.read_text())
            node, codec = plan["components"]["services_probe"], SDL2(python_inputs.SDL_ROOT)
            service = ParameterService(plan, codec); self.addCleanup(service.close)
            bindings = LocalBindings(node, service); self.addCleanup(bindings.close)
            scheduler = InputScheduler(node, {}); scheduler.start()
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
                self.assertEqual(observed, [41, 42]); self.assertEqual(process.measurements.snapshot(), {"observed.count": 42.})
            finally:
                await stop_component(process)

    def test_malformed_or_out_of_scope_activation_never_finishes_a_callback(self):
        node = self.template["components"]["a"]
        timer = InputReference("clock", "timeout", 10_000_000, 10_000_000, 1, None, frozenset())
        passive = InputReference("feedback", "timeout", 10_000_000, 10_000_000, 1, None, frozenset())
        data = input_ipc.activation_data(timer, 1)
        malformed = [data[:end] for end in range(len(data))]
        malformed += [data + b"\0", bytes(32) + data[32:], input_ipc.activation_data(timer, 2),
                      input_ipc.activation_data(passive, 1), data[:33] + b"\0\x01\xff\0"]
        for body in malformed:
            with self.subTest(data=body):
                result, events = self.exchange(node, ipc.frame("T", body))
                self.assertNotEqual(result.returncode, 0); self.assertIn(b"R\x02", events)
                self.assertFalse(any(event[:1] == b"F" for event in events))

    def test_invalid_input_replies_fail_without_finished(self):
        node = self.template["components"]["a"]
        timer = InputReference("clock", "timeout", 10_000_000, 10_000_000, 1, None, frozenset())
        bad_id = b"\1\1" + input_ipc.text("handle") + input_ipc.text("source.out") + b"\2"
        bad_envelope = dict(id="handle", topic="source.out", catalogue=node["inputs"]["feedback"]["catalogue"], body=b"invalid")
        for reply in (b"", b"\2", b"\1", b"\1\2", b"\1\0\0", b"\1\1", b"\0rejected", bad_id,
                      b"\1" + input_ipc.record_data(bad_envelope)):
            with self.subTest(reply=reply):
                result, events = self.exchange(node, ipc.frame("T", input_ipc.activation_data(timer, 1)) + ipc.frame("A", reply))
                self.assertNotEqual(result.returncode, 0); self.assertTrue(any(event[:1] == b"Q" for event in events))
                self.assertFalse(any(event[:1] in (b"F", b"P") for event in events))

    def test_full_unsigned_timer_fields_and_publication_ids(self):
        node = self.template["components"]["a"]
        maximum = (1 << 64) - 1
        timer = InputReference("clock", "timeout", maximum, maximum, maximum, None, frozenset())
        result, events = self.exchange(node, ipc.frame("T", input_ipc.activation_data(timer, 1)) + ipc.frame("A", b"\1\0"))
        self.assertEqual(result.returncode, 0, result.stderr.decode())
        self.assertIn(b"F", events)
        publication = next(event[1:] for event in events if event[:1] == b"P")
        self.assertEqual(input_ipc.unpack_publication(publication)[2], maximum)

    async def test_silent_push_and_context_scope_preserve_zero_and_absent_ids(self):
        with tempfile.TemporaryDirectory(prefix="ose java silent push ") as directory:
            root = Path(directory)
            (root / "value.sdl").write_text(python_inputs.SCHEMA)
            (root / "Push.java").write_text(r'''import org.sdlose.generated.push_probe.API;
import org.sdlose.InputProtocol.Reference;
import java.math.BigInteger;
import java.util.concurrent.atomic.AtomicBoolean;
import sdl.globaltypes.value.Value;
public final class Push extends API.Handler {
    @Override public void onStart(API.Context ctx) throws Exception {
        try { ctx.arm("clock", BigInteger.ZERO); throw new AssertionError("zero delay accepted"); }
        catch (IllegalArgumentException expected) {}
        try { ctx.arm("clock", BigInteger.ONE.shiftLeft(64)); throw new AssertionError("oversized delay accepted"); }
        catch (IllegalArgumentException expected) {}
        try { ctx.disarm("feedback"); throw new AssertionError("message used as timer"); }
        catch (IllegalArgumentException expected) {}
        try { ctx.get_cache(); throw new AssertionError("Pull allowed during onStart"); }
        catch (IllegalStateException expected) {}
    }
    @Override public void on_clock(final API.Context ctx, Reference<Void> input) throws Exception {
        final AtomicBoolean blocked = new AtomicBoolean(false);
        Thread thread = new Thread(new Runnable() { public void run() {
            try { ctx.get_cache(); } catch (IllegalStateException expected) { blocked.set(true); }
            catch (Exception unexpected) { throw new AssertionError(unexpected); }
        }});
        thread.start(); thread.join();
        if (!blocked.get()) throw new AssertionError("Pull allowed on a foreign thread");
        Value value = new Value(); value.count = 1;
        ctx.publish_out(value, ctx.configJson.contains("\"no_id\": true") ? null : BigInteger.ZERO);
    }
    @Override public void on_feedback(API.Context ctx, Reference<Value> input) throws Exception {
        if (input.message.count + 1 != 2 ||
            (ctx.configJson.contains("\"no_id\": true") ? input.appId != null : !BigInteger.ZERO.equals(input.appId)))
            throw new AssertionError("reference metadata");
        try { ctx.publish_out(input.message); throw new AssertionError("unauthorized output"); }
        catch (IllegalStateException expected) {}
    }
    @Override public void onStop(API.Context ctx) throws Exception {
        try { ctx.get_cache(); throw new AssertionError("Pull allowed onStop"); }
        catch (IllegalStateException expected) {}
        try { ctx.arm("clock"); throw new AssertionError("timer allowed onStop"); }
        catch (IllegalStateException expected) {}
    }
}
''')
            node = dict(inputs=dict(clock=dict(kind="timer", timer=dict(mode="one_shot", delay=.01),
                activates=dict(timeout=["out"])), feedback=dict(root="Value"), cache=dict(root="Value", mode="pull")),
                outputs=dict(out=dict(root="Value")))
            model = dict(version=2, schemas="value.sdl", components=dict(push_probe=node), subscriptions=[
                {"id": "feedback", "from": "push_probe.out", "to": "push_probe.feedback"}])
            deployment = dict(version=1, components=dict(push_probe=dict(container=dict(language="java", handler="Push.java", **{"class": "Push"}))))
            (root / "model.json").write_text(json.dumps(model)); (root / "deployment.json").write_text(json.dumps(deployment))
            path = build(root / "model.json", root / "deployment.json", python_inputs.SDL_ROOT, self.root / "build")
            plan = json.loads(path.read_text())
            for no_id in (False, True):
                plan["components"]["push_probe"]["parameters"] = dict(no_id=no_id)
                summary = await Simulation(plan, .1).execute()
                self.assertEqual((summary["ticks"], summary["publications"], summary["stop_reason"]), (2, 1, "quiescent"))

    @staticmethod
    def exchange(node, data):
        result = run_component(node["command"], input=ipc.frame("I", json.dumps(node).encode()) + data + ipc.frame("X"),
            capture_output=True, timeout=10)
        stream, events = io.BytesIO(result.stdout), []
        while stream.tell() < len(result.stdout):
            size = int.from_bytes(stream.read(4), "big"); events.append(stream.read(size))
        return result, events


class JavaToPythonInputProcessTests(python_inputs.InputProcessTests):
    SOURCE_LANGUAGE = "java"
    SOURCE_HANDLER = A
    test_empty_push_inactivity_repeats_without_a_message = None
    test_input_callbacks_keep_parameter_recording_log_and_measurement_services = None


class PythonToJavaInputProcessTests(python_inputs.InputProcessTests):
    RESPONSE_LANGUAGE = "java"
    RESPONSE_HANDLER = B
    test_empty_push_inactivity_repeats_without_a_message = None
    test_input_callbacks_keep_parameter_recording_log_and_measurement_services = None
    test_manual_timer_can_be_armed_during_initialization = None
    test_model_two_explicitly_rejects_an_old_ready = None
    test_parent_checks_output_rights_even_if_the_sdk_is_bypassed = None
    test_pull_over_ch_two_decodes_a_different_catalogue_on_each_message = None
