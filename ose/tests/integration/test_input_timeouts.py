"""Timed inputs across generated containers and real broker/adapter execution."""
from tests.component_driver import run_component
import asyncio
import io
import json
import os
from pathlib import Path
import struct
import subprocess
import sys
import tempfile
import threading
import unittest

from ose.client import Client
from ose.codec.sdl2 import SDL2
from ose.execution.component_io import read_exact
from ose.execution.input_ipc import unpack_publication
from ose.execution.ipc import frame
from ose.modeling.compiler import build
from tests.component_driver import message_frame, timer_frame

SDL_ROOT = os.environ.get("SDL_ROOT")
SCHEMA = "message Measurement {1: required uint64 sequence;2: required fl64 value;}"

PYTHON_NAMED = '''from measurement import Measurement
def on_input(ctx, input):
    message = input.message
    if input.timed_out:
        ctx.publish_result(Measurement(sequence=0, value=-1.0))
    else:
        ctx.publish_result(Measurement(sequence=message.sequence, value=message.value * 2))

'''
PYTHON_PAIR = '''from measurement import Measurement
state = {"a": None, "b": None}
def update(ctx, input):
    state[input.port] = input.message
    a, b = state["a"], state["b"]
    ctx.publish_result(Measurement(sequence=0, value=(a.value if a else -1.0) + (b.value if b else -2.0)))
on_a = on_b = update
'''
C_NAMED = '''#include "component.h"
static int on_input(OseContext *ctx, const Input_input *input) {
    const Measurement *message = input->message;
    Measurement result = {0};
    result.sequence = message ? message->sequence : 0;
    result.value = message ? message->value * 2 : -1.0;
    return publish_result(ctx, &result);
}
const ComponentCallbacks component_callbacks = { .on_input = on_input };
'''
C_PAIR = '''#include "component.h"
static double a = -1.0, b = -2.0;
static int emit(OseContext *ctx) {
    Measurement result = {0};
    result.value = a + b;
    return publish_result(ctx, &result);
}
static int on_a(OseContext *ctx, const Input_a *input) {
    a = input->message ? input->message->value : -1.0;
    return emit(ctx);
}
static int on_b(OseContext *ctx, const Input_b *input) {
    b = input->message ? input->message->value : -2.0;
    return emit(ctx);
}
const ComponentCallbacks component_callbacks = { .on_a = on_a, .on_b = on_b };
'''
RUST_NAMED = '''use crate::container::{Callbacks, Context, InputReference, Result};
use crate::types::measurement::Measurement;
#[derive(Default)] pub struct Handler;
impl Callbacks for Handler {
    fn on_input(&mut self, ctx: &mut Context, input: &InputReference<Measurement>) -> Result {
        let result = match &input.message {
            Some(m) => Measurement { sequence: m.sequence, value: m.value * 2.0 },
            None => Measurement { sequence: 0, value: -1.0 },
        };
        ctx.publish_result(&result)
    }
}
'''
RUST_PAIR = '''use crate::container::{Callbacks, Context, InputReference, Result};
use crate::types::measurement::Measurement;
#[derive(Default)] pub struct Handler { a: Option<f64>, b: Option<f64> }
impl Handler {
    fn emit(&self, ctx: &mut Context) -> Result {
        ctx.publish_result(&Measurement { sequence: 0,
            value: self.a.unwrap_or(-1.0) + self.b.unwrap_or(-2.0) })
    }
}
impl Callbacks for Handler {
    fn on_a(&mut self, ctx: &mut Context, input: &InputReference<Measurement>) -> Result {
        self.a = input.message.as_ref().map(|m| m.value);
        self.emit(ctx)
    }
    fn on_b(&mut self, ctx: &mut Context, input: &InputReference<Measurement>) -> Result {
        self.b = input.message.as_ref().map(|m| m.value);
        self.emit(ctx)
    }
}
'''


@unittest.skipUnless(SDL_ROOT, "set SDL_ROOT")
class TimeoutContainerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        temporary = tempfile.TemporaryDirectory()
        cls.addClassCleanup(temporary.cleanup)
        cls.root = Path(temporary.name)
        cls.codec = SDL2(SDL_ROOT)
        cls.fixtures = {}
        for language, suffix, named, pair in (("python", ".py", PYTHON_NAMED, PYTHON_PAIR),
                                               ("c", ".c", C_NAMED, C_PAIR),
                                               ("rust", ".rs", RUST_NAMED, RUST_PAIR)):
            root = cls.root / language
            root.mkdir()
            (root / "measurement.sdl").write_text(SCHEMA)
            timed = dict(root="Measurement", timeout=.05, activates={"message": ["result"], "timeout": ["result"]})
            optional = dict(root="Measurement", mode="pull")
            components = {
                "sink": dict(inputs={"input": dict(timed), "optional": dict(optional)},
                             outputs={"result": {"root": "Measurement"}}),
                "pair": dict(inputs={"a": dict(timed), "b": dict(timed, timeout=.1), "optional": dict(optional)},
                             outputs={"result": {"root": "Measurement"}}),
            }
            model = dict(version=2, schemas="measurement.sdl", components=components,
                         external_topics={"source": {"root": "Measurement"}}, subscriptions=[
                             {"id": "sink", "from_topic": "source", "to": "sink.input"},
                             {"id": "a", "from_topic": "source", "to": "pair.a"},
                             {"id": "b", "from_topic": "source", "to": "pair.b"},
                         ])
            deployment = dict(version=1, components={name: {"container": {"language": language, "handler": name + suffix}}
                                                       for name in components})
            (root / ("sink" + suffix)).write_text(named)
            (root / ("pair" + suffix)).write_text(pair)
            m, d = root / "model.json", root / "deployment.json"
            m.write_text(json.dumps(model)); d.write_text(json.dumps(deployment))
            plan = json.loads(build(m, d, SDL_ROOT, root / "build").read_text())
            cls.fixtures[language] = plan
        cls.catalogue = cls.fixtures["python"]["external_topics"]["source"]["catalogue"]
        cls.payload = b"\x01" + struct.pack(">Qd", 7, 3.5)

    def run_node(self, language, name, events):
        node = self.fixtures[language]["components"][name]
        result = run_component(node["command"], input=frame("I", json.dumps(node).encode()) + events + frame("X"),
                                capture_output=True, timeout=10)
        messages, stream = [], io.BytesIO(result.stdout)
        while stream.tell() < len(result.stdout):
            size = struct.unpack(">I", read_exact(stream, 4))[0]
            data = read_exact(stream, size)
            messages.append((chr(data[0]), data[1:]))
        return result, messages

    def data(self, port, index):
        return message_frame(index, port, self.catalogue, self.codec.wrap(self.catalogue, self.payload))

    def timeout(self, port, index):
        return timer_frame(index, port=port)

    def values(self, messages):
        codec = self.codec.api.prepare(self.catalogue)
        return [self.codec.api.decode_dynamic(codec, unpack_publication(body)[1]).fields["value"]
                for op, body in messages if op == "P"]

    def test_same_named_callback_handles_timeout_message_and_timeout_again(self):
        events = self.timeout("input", 1) + self.data("input", 2) + self.timeout("input", 3)
        for language in self.fixtures:
            with self.subTest(language=language):
                result, messages = self.run_node(language, "sink", events)
                self.assertEqual(result.returncode, 0, result.stderr.decode())
                self.assertEqual([op for op, _ in messages], ["R", "P", "F", "P", "F", "P", "F"])
                self.assertEqual(messages[0][1], b"\2")
                self.assertEqual(self.values(messages), [-1., 7., -1.])

    def test_independent_callbacks_coordinate_state_and_timeout_recovery(self):
        events = self.data("a", 1) + self.data("b", 2) + self.timeout("a", 3) + self.timeout("b", 4)
        for language in self.fixtures:
            with self.subTest(language=language):
                result, messages = self.run_node(language, "pair", events)
                self.assertEqual(result.returncode, 0, result.stderr.decode())
                self.assertEqual(self.values(messages), [1.5, 7., 2.5, -3.])
                self.assertEqual(sum(op == "F" for op, _ in messages), 4)

    def test_old_frames_passive_unknown_and_malformed_activations_are_rejected(self):
        cases = [frame("T"), frame("T", b"\0" * 16), frame("D", b"input"),
                 self.timeout("input", 2), self.data("input", 2),
                 self.timeout("optional", 1), self.timeout("unknown", 1),
                 frame("T", self.data("input", 1)[5:-1])]
        for language in self.fixtures:
            for events in cases:
                with self.subTest(language=language, events=events[-12:]):
                    result, messages = self.run_node(language, "sink", events)
                    self.assertNotEqual(result.returncode, 0)
                    self.assertEqual([op for op, _ in messages], ["R"])

    def test_compiled_plan_preserves_timeout_seconds(self):
        for plan in self.fixtures.values():
            self.assertEqual(plan["components"]["sink"]["inputs"]["input"]["timeout"], .05)
            self.assertEqual(plan["components"]["pair"]["inputs"]["b"]["timeout"], .1)


@unittest.skipUnless(SDL_ROOT, "set SDL_ROOT")
class TimeoutRuntimeTests(unittest.IsolatedAsyncioTestCase):
    async def test_live_independent_timeouts_and_recovery(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "measurement.sdl").write_text(SCHEMA)
            (root / "handler.py").write_text(PYTHON_PAIR)
            activation = {"message": ["result"], "timeout": ["result"]}
            model = dict(version=2, schemas="measurement.sdl", external_topics={"a": {"root": "Measurement"}, "b": {"root": "Measurement"}},
                components={"pair": dict(inputs={"a": dict(root="Measurement", timeout=.15, activates=activation),
                    "b": dict(root="Measurement", timeout=.3, activates=activation), "optional": dict(root="Measurement", mode="pull")},
                    outputs={"result": {"root": "Measurement"}})},
                subscriptions=[{"id": p, "from_topic": p, "to": "pair." + p, "qos": {"ack": "received"}} for p in ("a", "b")])
            deployment = dict(version=1, components={"pair": {"container": {"language": "python", "handler": "handler.py"}}})
            m, d = root / "model.json", root / "deployment.json"
            m.write_text(json.dumps(model)); d.write_text(json.dumps(deployment))
            plan = await asyncio.to_thread(build, m, d, SDL_ROOT, root / "build")
            process = await asyncio.create_subprocess_exec(sys.executable, "-m", "ose.cli", "run", "--plan", str(plan),
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
            client = None
            try:
                running = json.loads(await asyncio.wait_for(process.stdout.readline(), 5))
                api = SDL2(SDL_ROOT).api
                class Measurement(api.SdlMessage):
                    _SDL_NAME = "Measurement"
                    _SDL_DESCRIPTOR = "SDL2|Measurement{1:uint64:sequence;2:fl64:value;}"
                    _SDL_FIXED_SIZE = 16
                    _SDL_FIELDS = ((1, "sequence", "required", "uint64", ()), (2, "value", "required", "fl64", ()))
                client = await asyncio.to_thread(Client.connect, port=running["port"], client_id="timeout-driver")
                values, changed = [], threading.Event()
                def receive(message, info):
                    values.append(message.value)
                    changed.set()
                await asyncio.to_thread(client.subscribe, "pair.result", Measurement, receive)
                self.assertTrue(await asyncio.to_thread(changed.wait, 2))
                self.assertEqual(values[0], -3.)
                a = await asyncio.to_thread(client.publisher, "a", Measurement)
                b = await asyncio.to_thread(client.publisher, "b", Measurement)
                changed.clear()
                await asyncio.to_thread(a.publish, Measurement(sequence=1, value=10.))
                self.assertTrue(await asyncio.to_thread(changed.wait, 2))
                self.assertIn(8., values)  # Real A with a timed-out B.
                await asyncio.to_thread(a.publish, Measurement(sequence=2, value=10.))
                await asyncio.to_thread(b.publish, Measurement(sequence=3, value=20.))
                deadline = asyncio.get_running_loop().time() + 2
                while 30. not in values and asyncio.get_running_loop().time() < deadline:
                    await asyncio.sleep(.01)
                self.assertIn(30., values)
                self.assertIsNone(client.failure)
            finally:
                if client:
                    await asyncio.to_thread(client.close)
                if process.returncode is None:
                    process.terminate()
                communication = asyncio.create_task(process.communicate())
                try:
                    stdout, stderr = await asyncio.wait_for(asyncio.shield(communication), 5)
                except asyncio.TimeoutError:
                    process.kill()
                    await communication
                    raise
                self.assertEqual(process.returncode, 0, stderr.decode() + stdout.decode())
