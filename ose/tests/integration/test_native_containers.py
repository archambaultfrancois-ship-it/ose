"""Compile and exercise C/Rust callbacks, ownership and catalogue evolution."""
from tests.component_driver import run_component
import copy
import io
import json
import os
from pathlib import Path
import struct
import subprocess
import tempfile
import unittest
from ose.codec.sdl2 import SDL2
from ose.execution.component_io import read_exact
from ose.execution.input_ipc import unpack_publication
from ose.execution.ipc import frame
from ose.modeling.compiler import build, validate
from tests.component_driver import message_frame, timer_frame

SDL_ROOT = os.environ.get("SDL_ROOT")
SIMPLE = "message Measurement {1: required uint64 sequence; 2: required fl64 value;}"
EVOLVED = "message Alpha {1: int32 x;} message Measurement {1: required uint64 sequence; 2: required fl64 value; 3: uint32 extra;}"
C_SOURCE = r'''#include "component.h"
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
static int start(OseContext *ctx) {
    uint64_t *counter = malloc(sizeof(*counter));
    if (!counter) return 0;
    *counter = 6; ctx->user_data = counter;
    if (strstr(ctx->config_json, "FAIL_INIT")) return ose_fail(ctx, "init failure");
    if (strstr(ctx->config_json, "OUTSIDE_TICK")) {
        Measurement value = {0};
        return publish_out(ctx, &value);
    }
    return 1;
}
static int on_clock(OseContext *ctx, const Input_clock *input) {
    (void)input;
    Measurement value = {0};
    if (strstr(ctx->config_json, "FAIL_TICK")) return ose_fail(ctx, "user failure");
    value.sequence = ++*(uint64_t *)ctx->user_data;
    value.value = 3.5; value.extra = 9;
    return publish_out(ctx, &value);
}
static int stop(OseContext *ctx) {
    free(ctx->user_data);
    fprintf(stderr, "STOP %s\n", ctx->name);
    return 1;
}
const ComponentCallbacks component_callbacks = { .on_start = start, .on_stop = stop, .on_clock = on_clock };
'''
C_ECHO = r'''#include "component.h"
#include <stdio.h>
static int on_input(OseContext *ctx, const Input_input *input) {
    Measurement value = *input->message;
    value.value *= 2;
    return input->meta.has_app_id ? publish_result_with_id(ctx, &value, input->meta.app_id) : publish_result(ctx, &value);
}
static int stop(OseContext *ctx) { fprintf(stderr, "STOP %s\n", ctx->name); return 1; }
const ComponentCallbacks component_callbacks = { .on_input = on_input, .on_stop = stop };
'''
C_STATE = r'''#include "component.h"
#include <stdio.h>
static double second_value;
static int on_second(OseContext *ctx, const Input_second *input) {
    (void)ctx;
    second_value = input->message->value;
    return 1;
}
static int on_first(OseContext *ctx, const Input_first *input) {
    Measurement value = {0};
    value.sequence = input->message->sequence;
    value.value = input->message->value + second_value;
    return publish_result(ctx, &value);
}
static int stop(OseContext *ctx) { fprintf(stderr, "STOP %s\n", ctx->name); return 1; }
const ComponentCallbacks component_callbacks = { .on_first = on_first, .on_second = on_second, .on_stop = stop };
'''
RUST_SOURCE = r'''use crate::container::{Callbacks, Context, InputReference, Result};
use crate::types::measurement::Measurement;
#[derive(Default)]
pub struct Handler { counter: u64 }
impl Callbacks for Handler {
    fn on_start(&mut self, ctx: &mut Context) -> Result {
        self.counter = 6;
        if ctx.config_json.contains("FAIL_INIT") { return Err("init failure".into()); }
        if ctx.config_json.contains("OUTSIDE_TICK") { ctx.publish_out(&Measurement::default())?; }
        Ok(())
    }
    fn on_clock(&mut self, ctx: &mut Context, _input: &InputReference<()>) -> Result {
        if ctx.config_json.contains("FAIL_TICK") { return Err("user failure".into()); }
        if ctx.config_json.contains("PANIC") { panic!("user panic"); }
        self.counter += 1;
        ctx.publish_out(&Measurement { sequence: self.counter, value: 3.5, extra: 9 })
    }
    fn on_stop(&mut self, ctx: &mut Context) -> Result { eprintln!("STOP {}", ctx.name); Ok(()) }
}
'''
RUST_ECHO = r'''use crate::container::{Callbacks, Context, InputReference, Result};
use crate::types::measurement::Measurement;
#[derive(Default)]
pub struct Handler;
impl Callbacks for Handler {
    fn on_input(&mut self, ctx: &mut Context, input: &InputReference<Measurement>) -> Result {
        let mut value = input.message.as_ref().unwrap().clone(); value.value *= 2.0;
        match input.app_id {
            Some(id) => ctx.publish_result_with_id(&value, id),
            None => ctx.publish_result(&value),
        }
    }
    fn on_stop(&mut self, ctx: &mut Context) -> Result { eprintln!("STOP {}", ctx.name); Ok(()) }
}
'''
RUST_STATE = r'''use crate::container::{Callbacks, Context, InputReference, Result};
use crate::types::measurement::Measurement;
#[derive(Default)]
pub struct Handler { second_value: f64 }
impl Callbacks for Handler {
    fn on_second(&mut self, _ctx: &mut Context, input: &InputReference<Measurement>) -> Result {
        self.second_value = input.message.as_ref().unwrap().value;
        Ok(())
    }
    fn on_first(&mut self, ctx: &mut Context, input: &InputReference<Measurement>) -> Result {
        let first = input.message.as_ref().unwrap();
        ctx.publish_result(&Measurement { sequence: first.sequence, value: first.value + self.second_value })
    }
    fn on_stop(&mut self, ctx: &mut Context) -> Result { eprintln!("STOP {}", ctx.name); Ok(()) }
}
'''


@unittest.skipUnless(SDL_ROOT, "set SDL_ROOT to an independent SDL checkout")
class NativeContainerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        temporary = tempfile.TemporaryDirectory()
        cls.addClassCleanup(temporary.cleanup)
        cls.root = Path(temporary.name)
        cls.fixtures = {}
        cls.codec = SDL2(SDL_ROOT)
        for language, suffix, handlers in (("c", ".c", (C_SOURCE, C_ECHO, C_STATE)),
                                           ("rust", ".rs", (RUST_SOURCE, RUST_ECHO, RUST_STATE))):
            root = cls.root / language
            root.mkdir()
            (root / "types.sdl").write_text(EVOLVED)
            (root / "consumer").mkdir()
            (root / "consumer/measurement.sdl").write_text(SIMPLE)
            port = {"root": "Measurement", "schema": "consumer/measurement.sdl"}
            model = {"version": 2, "schemas": "types.sdl", "components": {
                "source": {"inputs": {"clock": {"kind": "timer", "timer": {"mode": "periodic", "delay": .01},
                                              "activates": {"timeout": ["out"]}}}, "outputs": {"out": {"root": "Measurement"}}},
                "echo": {"inputs": {"input": dict(port, activates={"message": ["result"]})}, "outputs": {"result": dict(port)}},
                "sink": {"inputs": {"first": dict(port, activates={"message": ["result"]}), "second": dict(port),
                                    "passive": dict(port, mode="pull")}, "outputs": {"result": dict(port)}}},
                "subscriptions": [{"id": name, "from": "source.out", "to": target} for name, target in
                                  (("echo", "echo.input"), ("first", "sink.first"), ("second", "sink.second"))]}
            deployment = {"version": 1, "components": {name: {"container": {"language": language, "handler": name + suffix}}
                                                      for name in model["components"]}}
            for name, handler in zip(model["components"], handlers):
                if language == "rust" and name == "source":
                    handler = handler.replace("crate::types::measurement::Measurement", "crate::types::types::Measurement")
                (root / (name + suffix)).write_text(handler)
            m, d = root / "model.json", root / "deployment.json"
            m.write_text(json.dumps(model)); d.write_text(json.dumps(deployment))
            path = build(m, d, SDL_ROOT, root / "build")
            cls.fixtures[language] = {"root": root, "model": model, "deployment": deployment, "plan": json.loads(path.read_text()), "suffix": suffix}

    def run_node(self, language, name, events=b"", probe=None):
        node = copy.deepcopy(self.fixtures[language]["plan"]["components"][name])
        if probe:
            node["parameters"] = {"probe": probe}
        result = run_component(node["command"], input=frame("I", json.dumps(node).encode()) + events + frame("X"),
                                capture_output=True, timeout=10)
        stream, messages = io.BytesIO(result.stdout), []
        while stream.tell() < len(result.stdout):
            size = struct.unpack(">I", read_exact(stream, 4))[0]
            data = read_exact(stream, size)
            messages.append((chr(data[0]), data[1:]))
        return result, messages

    def source_payload(self, language):
        result, messages = self.run_node(language, "source", timer_frame(1))
        self.assertEqual(result.returncode, 0, result.stderr.decode())
        self.assertEqual(messages[0], ("R", b"\2"))
        return unpack_publication(messages[1][1])[1]

    def push(self, language, payload, *, port="input", index=1, app_id=None):
        catalogue = self.fixtures[language]["plan"]["components"]["source"]["outputs"]["out"]["catalogue"]
        return message_frame(index, port, catalogue, self.codec.wrap(catalogue, payload), app_id=app_id)

    def test_periodic_sources_publish_typed_values_and_stop(self):
        for language, fixture in self.fixtures.items():
            with self.subTest(language=language):
                result, messages = self.run_node(language, "source", timer_frame(1) + timer_frame(2))
                self.assertEqual(result.returncode, 0, result.stderr.decode())
                self.assertEqual([op for op, _ in messages], ["R", "P", "F", "P", "F"])
                catalogue = fixture["plan"]["components"]["source"]["outputs"]["out"]["catalogue"]
                decoder = self.codec.api.prepare(catalogue)
                values = [self.codec.api.decode_dynamic(decoder, unpack_publication(body)[1]).fields for op, body in messages if op == "P"]
                self.assertEqual([value["sequence"] for value in values], [7, 8])
                self.assertEqual(values[0]["extra"], 9)
                self.assertIn(b"STOP source", result.stderr)

    def test_named_callbacks_decode_evolved_catalogue(self):
        for language, fixture in self.fixtures.items():
            with self.subTest(language=language):
                payload = self.source_payload(language)
                publisher = fixture["plan"]["components"]["source"]["outputs"]["out"]["catalogue"]
                consumer = fixture["plan"]["components"]["echo"]["outputs"]["result"]["catalogue"]
                self.assertNotEqual(self.codec.api.message_names(self.codec.api.prepare(publisher)), self.codec.api.message_names(self.codec.api.prepare(consumer)))
                for identity in (None, 0, 2**64 - 1):
                    result, messages = self.run_node(language, "echo", self.push(language, payload, app_id=identity))
                    self.assertEqual(result.returncode, 0, result.stderr.decode())
                    self.assertEqual([op for op, _ in messages], ["R", "P", "F"])
                    _, wire, actual_id = unpack_publication(messages[1][1])
                    self.assertEqual(actual_id, identity)
                    output = self.codec.api.decode_dynamic(self.codec.api.prepare(consumer), wire)
                    self.assertEqual(output.fields, {"sequence": 7, "value": 7.0})
                    self.assertIn(b"STOP echo", result.stderr)

    def test_independent_callbacks_retain_owned_state_and_do_not_wait_for_passive_input(self):
        for language, fixture in self.fixtures.items():
            with self.subTest(language=language):
                payload = self.source_payload(language)
                catalogue = fixture["plan"]["components"]["source"]["outputs"]["out"]["catalogue"]
                events = self.push(language, payload, port="first") + self.push(language, payload, port="second", index=2) + \
                         self.push(language, payload, port="first", index=3)
                result, messages = self.run_node(language, "sink", events)
                self.assertEqual(result.returncode, 0, result.stderr.decode())
                self.assertEqual([op for op, _ in messages], ["R", "P", "F", "F", "P", "F"])
                output_catalogue = fixture["plan"]["components"]["sink"]["outputs"]["result"]["catalogue"]
                values = [self.codec.api.decode_dynamic(self.codec.api.prepare(output_catalogue), unpack_publication(body)[1]).fields["value"]
                          for op, body in messages if op == "P"]
                self.assertEqual(values, [3.5, 7.0])

    def test_old_frames_invalid_origins_and_payloads_never_invoke_callbacks(self):
        for language, fixture in self.fixtures.items():
            payload = self.source_payload(language)
            catalogue = fixture["plan"]["components"]["source"]["outputs"]["out"]["catalogue"]
            for events in (frame("T"), frame("T", b"\0" * 16), frame("D", b"first"),
                           self.push(language, payload, port="first", index=2),
                           self.push(language, b"invalid", port="first"),
                           self.push(language, payload, port="unknown"),
                           self.push(language, payload, port="passive")):
                with self.subTest(language=language, events=events[-10:]):
                    result, messages = self.run_node(language, "sink", events)
                    self.assertNotEqual(result.returncode, 0)
                    self.assertEqual([op for op, _ in messages], ["R"])
                    self.assertIn(b"STOP sink", result.stderr)

    def test_initialization_publication_and_callback_failures(self):
        for language in self.fixtures:
            for probe, expected in (("FAIL_INIT", []), ("OUTSIDE_TICK", []), ("FAIL_TICK", ["R"])):
                with self.subTest(language=language, probe=probe):
                    result, messages = self.run_node(language, "source", timer_frame(1), probe)
                    self.assertNotEqual(result.returncode, 0)
                    self.assertEqual([op for op, _ in messages], expected)
                    self.assertIn(b"STOP source", result.stderr)
        result, messages = self.run_node("rust", "source", timer_frame(1), "PANIC")
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual([op for op, _ in messages], ["R"])
        self.assertIn(b"STOP source", result.stderr)

    def test_rebuild_preserves_native_user_files(self):
        for language, fixture in self.fixtures.items():
            with self.subTest(language=language):
                root = fixture["root"]
                handler = root / ("source" + fixture["suffix"])
                original = handler.read_bytes()
                model = copy.deepcopy(fixture["model"])
                model["components"] = {"source": model["components"]["source"]}
                model["components"]["source"]["outputs"]["mirror"] = {"root": "Measurement"}
                model["subscriptions"] = []
                deployment = {"version": 1, "components": {"source": fixture["deployment"]["components"]["source"]}}
                m, d = root / "rebuild-model.json", root / "rebuild-deployment.json"
                m.write_text(json.dumps(model)); d.write_text(json.dumps(deployment))
                build(m, d, SDL_ROOT, root / "rebuild")
                self.assertEqual(handler.read_bytes(), original)
                generated = root / "rebuild/components/source" / ("component.h" if language == "c" else "src/container.rs")
                self.assertIn("publish_mirror", generated.read_text())

    def test_qualified_nested_bindings_and_unicode_handler_paths(self):
        for language in self.fixtures:
            with self.subTest(language=language):
                root = self.root / (language + "-qualified")
                schemas = root / "schémas"
                schemas.mkdir(parents=True)
                (schemas / "point.sdl").write_text("package demo; message Point {1: int32 x;}")
                (schemas / "packet.sdl").write_text("package demo; message Packet {1: Point point; 2: string label;}")
                model = {"version": 2, "schemas": "schémas", "components": {
                    "source": {"inputs": {"clock": {"kind": "timer", "timer": {"mode": "periodic", "delay": .01},
                                                  "activates": {"timeout": ["out"]}}}, "outputs": {"out": {"root": "demo.Packet"}}},
                    "echo": {"inputs": {"input": {"root": "demo.Packet", "activates": {"message": ["result"]}}},
                             "outputs": {"result": {"root": "demo.Packet"}}}},
                    "subscriptions": [{"id": "link", "from": "source.out", "to": "echo.input"}]}
                suffix = self.fixtures[language]["suffix"]
                deployment = {"version": 1, "components": {name: {"container": {"language": language, "handler": name + "-é" + suffix}}
                                                          for name in model["components"]}}
                if language == "c":
                    handlers = ['#include "component.h"\nstatic int on_clock(OseContext *ctx, const Input_clock *input) { (void)input; Output_out value = {0}; value.point.x = 12; value.label = "native"; return publish_out(ctx, &value); }\nconst ComponentCallbacks component_callbacks = { .on_clock = on_clock };\n',
                                '#include "component.h"\nstatic int on_input(OseContext *ctx, const Input_input *input) { return publish_result(ctx, input->message); }\nconst ComponentCallbacks component_callbacks = { .on_input = on_input };\n']
                else:
                    handlers = ['use crate::container::{Callbacks, Context, InputReference, Result};\nuse crate::types::demo::packet::Packet;\n#[derive(Default)] pub struct Handler;\nimpl Callbacks for Handler { fn on_clock(&mut self, ctx: &mut Context, _input: &InputReference<()>) -> Result { let mut value = Packet::default(); value.point.x = 12; value.label = "native".into(); ctx.publish_out(&value) } }\n',
                                'use crate::container::{Callbacks, Context, InputReference, Result};\nuse crate::types::demo::packet::Packet;\n#[derive(Default)] pub struct Handler;\nimpl Callbacks for Handler { fn on_input(&mut self, ctx: &mut Context, input: &InputReference<Packet>) -> Result { ctx.publish_result(input.message.as_ref().unwrap()) } }\n']
                for name, handler in zip(model["components"], handlers):
                    (root / (name + "-é" + suffix)).write_text(handler)
                m, d = root / "model.json", root / "deployment.json"
                m.write_text(json.dumps(model)); d.write_text(json.dumps(deployment))
                plan = json.loads(build(m, d, SDL_ROOT, root / "build").read_text())
                def run(name, events):
                    node = plan["components"][name]
                    result = run_component(node["command"], input=frame("I", json.dumps(node).encode()) + events + frame("X"), capture_output=True, timeout=10)
                    self.assertEqual(result.returncode, 0, result.stderr.decode())
                    stream, messages = io.BytesIO(result.stdout), []
                    while stream.tell() < len(result.stdout):
                        size = struct.unpack(">I", read_exact(stream, 4))[0]
                        data = read_exact(stream, size)
                        messages.append((chr(data[0]), data[1:]))
                    self.assertEqual([op for op, _ in messages], ["R", "P", "F"])
                    self.assertEqual(messages[0], ("R", b"\2"))
                    return unpack_publication(messages[1][1])[1]
                payload = run("source", timer_frame(1))
                catalogue = plan["components"]["source"]["outputs"]["out"]["catalogue"]
                output = run("echo", message_frame(1, "input", catalogue, self.codec.wrap(catalogue, payload)))
                decoded = self.codec.api.decode_dynamic(self.codec.api.prepare(catalogue), output)
                self.assertEqual(decoded.fields["point"].fields["x"], 12)
                self.assertEqual(decoded.fields["label"], "native")

    def test_validation_and_first_build_scaffold_native_handlers(self):
        for language, fixture in self.fixtures.items():
            with self.subTest(language=language):
                root = fixture["root"]
                model = copy.deepcopy(fixture["model"])
                model["components"] = {"source": model["components"]["source"]}
                model["subscriptions"] = []
                handler = root / ("new" + fixture["suffix"])
                deployment = {"version": 1, "components": {"source": {"container": {"language": language, "handler": str(handler)}}}}
                m, d = root / "new-model.json", root / "new-deployment.json"
                m.write_text(json.dumps(model)); d.write_text(json.dumps(deployment))
                validate(m, d, SDL_ROOT)
                self.assertFalse(handler.exists())
                path = build(m, d, SDL_ROOT, root / "new-build")
                self.assertIn("on_clock", handler.read_text())
                node = json.loads(path.read_text())["components"]["source"]
                result = run_component(node["command"], input=frame("I", json.dumps(node).encode()) + timer_frame(1) + frame("X"), capture_output=True, timeout=10)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn(b"implement the input callback", result.stderr)
