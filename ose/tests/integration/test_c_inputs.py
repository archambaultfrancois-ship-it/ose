"""Typed C CH/2 callbacks, mixed-language feedback and strict frame handling."""
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

A = r'''#include "component.h"
#include <assert.h>
#include <string.h>
#include <sys/select.h>
static int on_start(OseContext *ctx) {
    if (!ose_declare_measurement(ctx, "feedback.count")) return 0;
    return !strstr(ctx->config_json, "\"arm\": true") || ose_arm(ctx, "clock", NULL);
}
static int on_clock(OseContext *ctx, const Input_clock *input) {
    Input_feedback previous, again, withdrawn, absent;
    OseSelector selector = {1, UINT64_MAX, NULL, NULL};
    Value value = {0};
    struct timeval delay = {0, 0};
    assert(input->meta.event == OSE_INPUT_TIMEOUT && !input->message);
    if (strstr(ctx->config_json, "\"delay\": 0.01")) delay.tv_usec = 10000;
    else if (strstr(ctx->config_json, "\"delay\": 0.005")) delay.tv_usec = 5000;
    if (delay.tv_usec) (void)select(0, NULL, NULL, NULL, &delay);
    if (strstr(ctx->config_json, "\"bypass\": true")) {
        /* Exercise parent-side permissions with a handcrafted CH/2 publication. */
        const unsigned char data[] = {0, 3, 'o', 'u', 't', 0, 0, 0, 0, 9};
        return ose_send('P', data, sizeof(data));
    }
    if (!get_feedback(ctx, &selector, &previous)) return 0;
    if (previous.message) {
        int removed;
        assert(previous.meta.has_app_id && previous.meta.app_id == UINT64_MAX);
        selector.handle = previous.meta.handle; selector.source = previous.meta.source;
        if (!get_feedback(ctx, &selector, &again)) return 0;
        assert(again.message && again.message->count == previous.message->count);
        if (ctx->tick_index == 3) {
            if (!remove_feedback(ctx, &selector, &removed)) return 0;
            assert(removed);
        } else {
            if (!pull_feedback(ctx, &selector, &withdrawn)) return 0;
            assert(withdrawn.message && !strcmp(withdrawn.meta.handle, previous.meta.handle));
        }
        if (!get_feedback(ctx, &selector, &absent)) return 0;
        assert(!absent.message);
        if (!ose_measure(ctx, "feedback.count", previous.message->count)) return 0;
        if (strstr(ctx->config_json, "\"fail\": true")) {
            value.count = 999;
            if (!publish_out(ctx, &value)) return 0;
            return ose_fail(ctx, "failed after deferred pull and staged publication");
        }
    }
    value.count = (uint32_t)ctx->tick_index;
    if (!publish_out_with_id(ctx, &value, UINT64_MAX)) return 0;
    return ctx->tick_index != 3 || ose_disarm(ctx, "clock");
}
const ComponentCallbacks component_callbacks = {.on_start = on_start, .on_clock = on_clock};
'''
B = r'''#include "component.h"
#include <assert.h>
static int on_input(OseContext *ctx, const Input_input *input) {
    Value value = {0};
    if (input->meta.event == OSE_INPUT_TIMEOUT) { assert(!input->message); return 1; }
    assert(input->meta.has_app_id && input->meta.app_id == UINT64_MAX);
    value.count = input->message->count + 100;
    return publish_out_with_id(ctx, &value, input->meta.app_id);
}
const ComponentCallbacks component_callbacks = {.on_input = on_input};
'''
SERVICES = r'''#include "component.h"
#include <assert.h>
static int on_start(OseContext *ctx) {
    Value value = *parameters_settings(ctx);
    assert(value.count == 40); value.count = 41;
    return set_parameters_settings(ctx, &value) &&
        ose_declare_recording_Value(ctx, "debug.value", "") &&
        ose_declare_measurement(ctx, "observed.count") &&
        ose_logf(ctx, OSE_LOG_INFO, "", "%s", "initialized");
}
static int on_clock(OseContext *ctx, const Input_clock *input) {
    Value value = *parameters_settings(ctx);
    (void)input;
    if (!publish_out(ctx, &value) || ose_record_Value(ctx, "debug.value", &value) != OSE_RECORD_QUEUED ||
        !ose_measure(ctx, "observed.count", value.count)) return 0;
    ++value.count; return set_parameters_settings(ctx, &value);
}
const ComponentCallbacks component_callbacks = {.on_start = on_start, .on_clock = on_clock};
'''


class CInputProcessTests(python_inputs.InputProcessTests):
    SOURCE_LANGUAGE = RESPONSE_LANGUAGE = "c"
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

    async def test_empty_push_inactivity_repeats_without_a_message(self):
        with tempfile.TemporaryDirectory(prefix="ose c timeout ") as directory:
            root = Path(directory)
            (root / "value.sdl").write_text(python_inputs.SCHEMA)
            (root / "b.c").write_text(B)
            node = dict(inputs=dict(input=dict(root="Value", timeout=.01)), outputs=dict(out=dict(root="Value")))
            model = dict(version=2, schemas="value.sdl", subscriptions=[], components=dict(deadline_probe=node))
            deployment = dict(version=1, components=dict(deadline_probe=dict(container=dict(language="c", handler="b.c"))))
            (root / "model.json").write_text(json.dumps(model))
            (root / "deployment.json").write_text(json.dumps(deployment))
            path = build(root / "model.json", root / "deployment.json", python_inputs.SDL_ROOT, self.root / "build")
            summary = await Simulation(json.loads(path.read_text()), .035).execute()
            self.assertEqual((summary["ticks"], summary["publications"], summary["stop_reason"]), (3, 0, "duration"))

    async def test_generated_handler_builds_and_subsequent_builds_preserve_user_code(self):
        with tempfile.TemporaryDirectory(prefix="ose c scaffold ") as directory:
            root = Path(directory)
            (root / "value.sdl").write_text(python_inputs.SCHEMA)
            node = dict(inputs=dict(clock=dict(kind="timer", timer=dict(mode="periodic", delay=.01),
                activates=dict(timeout=["out"])), feedback=dict(root="Value", mode="pull")), outputs=dict(out=dict(root="Value")))
            model = dict(version=2, schemas="value.sdl", components=dict(scaffold_probe=node), subscriptions=[])
            deployment = dict(version=1, components=dict(scaffold_probe=dict(container=dict(language="c", handler="handler.c"))))
            (root / "model.json").write_text(json.dumps(model))
            (root / "deployment.json").write_text(json.dumps(deployment))
            build(root / "model.json", root / "deployment.json", python_inputs.SDL_ROOT, self.root / "build")
            handler = root / "handler.c"
            self.assertIn("on_clock", handler.read_text())
            self.assertNotIn("on_tick", handler.read_text())
            self.assertNotIn("on_feedback", handler.read_text())
            handler.write_text(A)
            path = build(root / "model.json", root / "deployment.json", python_inputs.SDL_ROOT, self.root / "build")
            self.assertEqual(handler.read_text(), A)
            summary = await Simulation(json.loads(path.read_text()), .1).execute()
            self.assertEqual((summary["ticks"], summary["publications"], summary["stop_reason"]), (3, 3, "quiescent"))

    async def test_input_callbacks_keep_parameter_recording_log_and_measurement_services(self):
        with tempfile.TemporaryDirectory(prefix="ose c services ") as directory:
            root = Path(directory)
            (root / "value.sdl").write_text(python_inputs.SCHEMA)
            (root / "handler.c").write_text(SERVICES)
            node = dict(inputs=dict(clock=dict(kind="timer", timer=dict(mode="periodic", delay=.01),
                activates=dict(timeout=["out"]))), outputs=dict(out=dict(root="Value")),
                parameter_bindings=dict(settings=dict(path="shared/settings", access="read_write")))
            model = dict(version=2, schemas="value.sdl", components=dict(services_probe=node), subscriptions=[],
                shared_parameters={"shared/settings": dict(root="Value", initial=dict(count=40))})
            deployment = dict(version=1, components=dict(services_probe=dict(container=dict(language="c", handler="handler.c"))))
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
                self.assertEqual(process.measurements.snapshot(), {"observed.count": 42.})
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
                result = subprocess.run(node["command"], input=ipc.frame("I", json.dumps(node).encode()) +
                    ipc.frame("T", body) + ipc.frame("X"), capture_output=True, timeout=5)
                self.assertNotEqual(result.returncode, 0)
                stream, events = io.BytesIO(result.stdout), []
                while stream.tell() < len(result.stdout):
                    size = int.from_bytes(stream.read(4), "big")
                    events.append(stream.read(size))
                self.assertIn(b"R\x02", events)
                self.assertFalse(any(event[:1] == b"F" for event in events))

    def test_invalid_input_replies_fail_without_finished(self):
        node = self.template["components"]["a"]
        timer = InputReference("clock", "timeout", 10_000_000, 10_000_000, 1, None, frozenset())
        for reply in (b"", b"\2", b"\1", b"\1\2", b"\1\0\0", b"\1\1", b"\0rejected"):
            with self.subTest(reply=reply):
                result = subprocess.run(node["command"], input=ipc.frame("I", json.dumps(node).encode()) +
                    ipc.frame("T", input_ipc.activation_data(timer, 1)) + ipc.frame("A", reply) + ipc.frame("X"),
                    capture_output=True, timeout=5)
                self.assertNotEqual(result.returncode, 0)
                stream, events = io.BytesIO(result.stdout), []
                while stream.tell() < len(result.stdout):
                    size = int.from_bytes(stream.read(4), "big")
                    events.append(stream.read(size))
                self.assertTrue(any(event[:1] == b"Q" for event in events))
                self.assertFalse(any(event[:1] in (b"F", b"P") for event in events))

    async def test_silent_push_computes_and_unauthorized_publication_fails_the_callback(self):
        with tempfile.TemporaryDirectory(prefix="ose c silent push ") as directory:
            root = Path(directory)
            (root / "value.sdl").write_text(python_inputs.SCHEMA)
            (root / "handler.c").write_text(r'''#include "component.h"
#include <assert.h>
#include <string.h>
static int on_clock(OseContext *ctx, const Input_clock *input) {
    Value value = {0}; (void)input; value.count = 1;
    return publish_out_with_id(ctx, &value, 0);
}
static int on_feedback(OseContext *ctx, const Input_feedback *input) {
    Value value = {0}; value.count = input->message->count + 1;
    assert(value.count == 2 && input->meta.has_app_id && input->meta.app_id == 0);
    if (strstr(ctx->config_json, "\"forbidden\": true")) {
        assert(!publish_out(ctx, &value));
        return 1; /* Runtime failures remain failures even if the handler ignores them. */
    }
    return 1;
}
const ComponentCallbacks component_callbacks = {.on_clock = on_clock, .on_feedback = on_feedback};
''')
            node = dict(inputs=dict(clock=dict(kind="timer", timer=dict(mode="one_shot", delay=.01),
                activates=dict(timeout=["out"])), feedback=dict(root="Value")), outputs=dict(out=dict(root="Value")))
            model = dict(version=2, schemas="value.sdl", components=dict(push_probe=node), subscriptions=[
                {"id": "feedback", "from": "push_probe.out", "to": "push_probe.feedback"}])
            deployment = dict(version=1, components=dict(push_probe=dict(container=dict(language="c", handler="handler.c"))))
            (root / "model.json").write_text(json.dumps(model))
            (root / "deployment.json").write_text(json.dumps(deployment))
            path = build(root / "model.json", root / "deployment.json", python_inputs.SDL_ROOT, self.root / "build")
            plan = json.loads(path.read_text())
            summary = await Simulation(plan, .1).execute()
            self.assertEqual((summary["ticks"], summary["publications"], summary["stop_reason"]), (2, 1, "quiescent"))
            plan["components"]["push_probe"]["parameters"] = dict(forbidden=True)
            engine = Simulation(plan, .1)
            with self.assertRaises(asyncio.IncompleteReadError):
                await engine.execute()
            self.assertEqual(engine.summary["inputs"]["push_probe"]["feedback"]["depth"], 1)


class CToPythonInputProcessTests(python_inputs.InputProcessTests):
    SOURCE_LANGUAGE = "c"
    SOURCE_HANDLER = A
    test_empty_push_inactivity_repeats_without_a_message = None
    test_input_callbacks_keep_parameter_recording_log_and_measurement_services = None


class PythonToCInputProcessTests(python_inputs.InputProcessTests):
    RESPONSE_LANGUAGE = "c"
    RESPONSE_HANDLER = B
    test_empty_push_inactivity_repeats_without_a_message = None
    test_input_callbacks_keep_parameter_recording_log_and_measurement_services = None
    test_manual_timer_can_be_armed_during_initialization = None
    test_model_two_explicitly_rejects_an_old_ready = None
    test_parent_checks_output_rights_even_if_the_sdk_is_bypassed = None
    test_pull_over_ch_two_decodes_a_different_catalogue_on_each_message = None
