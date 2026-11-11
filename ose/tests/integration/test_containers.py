"""Exercise generated executables through their real SDL and binary IPC boundary."""
from tests.component_driver import run_component
import io
import json
import os
from pathlib import Path
import struct
import subprocess
import sys
import tempfile
import unittest
from ose.codec.sdl2 import SDL2
from ose.execution.component_io import read_exact
from ose.execution.input_ipc import unpack_publication
from ose.execution.ipc import frame
from ose.modeling.compiler import build, compile_plan, validate
from tests.component_driver import message_frame, timer_frame

SDL_ROOT = os.environ.get("SDL_ROOT")
SCHEMA = "message Measurement {1: required uint64 sequence; 2: required fl64 value;}"
SOURCE = '''from measurement import Measurement
def on_clock(ctx, input):
    ctx.publish_out(Measurement(sequence=7, value=3.5))
'''


@unittest.skipUnless(SDL_ROOT, "set SDL_ROOT to an independent SDL checkout")
class ContainerTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        (self.root / "measurement.sdl").write_text(SCHEMA)
        self.codec = SDL2(SDL_ROOT)
        self.model = {"version": 2, "schemas": "measurement.sdl", "components": {
            "source": {"inputs": {"clock": {"kind": "timer", "timer": {"mode": "periodic", "delay": .01},
                                          "activates": {"timeout": ["out"]}}},
                       "outputs": {"out": {"root": "Measurement"}}},
            "sink": {"inputs": {"input": {"root": "Measurement", "activates": {"message": ["result"]}}},
                     "outputs": {"result": {"root": "Measurement"}}}},
            "subscriptions": [{"id": "link", "from": "source.out", "to": "sink.input"}]}
        self.deployment = {"version": 1, "components": {
            name: {"container": {"language": "python", "handler": f"{name}.py"}}
            for name in self.model["components"]}}
        self.model_path, self.deployment_path = self.root / "model.json", self.root / "deployment.json"
        self.destination = self.root / "build"
        project = Path(__file__).resolve().parents[2]
        self.env = dict(os.environ, PYTHONPATH=os.pathsep.join(str(project / path) for path in ("src", "sdk/python")))

    def documents(self):
        self.model_path.write_text(json.dumps(self.model))
        self.deployment_path.write_text(json.dumps(self.deployment))

    def compile(self):
        self.documents()
        return compile_plan(self.model_path, self.deployment_path, SDL_ROOT, self.destination)

    def handler(self, name, source):
        (self.root / f"{name}.py").write_text(source)

    def run_node(self, plan, name, events=b""):
        node = plan["components"][name]
        result = run_component(node["command"], input=frame("I", json.dumps(node).encode()) + events + frame("X"),
                                cwd=node["cwd"], env=self.env, capture_output=True, timeout=10)
        stream, messages = io.BytesIO(result.stdout), []
        while stream.tell() < len(result.stdout):
            size = struct.unpack(">I", read_exact(stream, 4))[0]
            body = read_exact(stream, size)
            messages.append((chr(body[0]), body[1:]))
        return result, messages

    def source_payload(self, plan):
        self.handler("source", SOURCE)
        result, messages = self.run_node(plan, "source", timer_frame(1))
        self.assertEqual(result.returncode, 0, result.stderr.decode())
        self.assertEqual([op for op, _ in messages], ["R", "P", "F"])
        self.assertEqual(messages[0][1], b"\2")
        return unpack_publication(messages[1][1])[1]

    def push(self, plan, payload, *, index=1, port="input", app_id=None):
        catalogue = plan["components"]["source"]["outputs"]["out"]["catalogue"]
        return message_frame(index, port, catalogue, self.codec.wrap(catalogue, payload), app_id=app_id)

    def test_generated_port_callback_and_typed_publication(self):
        plan = self.compile()
        payload = self.source_payload(plan)
        self.handler("sink", '''from measurement import Measurement
from sdl_ose_component import ReceivedInput
def on_start(ctx):
    assert ctx.name == "sink"
def on_input(ctx, input):
    assert isinstance(input, ReceivedInput)
    message = input.message
    assert isinstance(message, Measurement)
    assert input.port == "input" and not input.timed_out and input.app_id == 0
    ctx.publish_result(Measurement(sequence=message.sequence, value=message.value * 2), id=input.app_id)
def on_stop(ctx):
    import sys
    print("stopped", file=sys.stderr)
''')
        catalogue = plan["components"]["source"]["outputs"]["out"]["catalogue"]
        result, messages = self.run_node(plan, "sink", self.push(plan, payload, app_id=0))
        self.assertEqual(result.returncode, 0, result.stderr.decode())
        self.assertEqual([op for op, _ in messages], ["R", "P", "F"])
        port, wire, app_id = unpack_publication(messages[1][1])
        self.assertEqual((port, app_id), ("result", 0))
        value = self.codec.api.decode_dynamic(self.codec.api.prepare(catalogue), wire)
        self.assertEqual(value.fields, {"sequence": 7, "value": 7.0})
        self.assertIn(b"stopped", result.stderr)

    def test_input_callbacks_keep_owned_state_without_waiting_for_other_inputs(self):
        sink = self.model["components"]["sink"]
        sink["inputs"] = {"first": {"root": "Measurement", "activates": {"message": ["result"]}},
                          "second": {"root": "Measurement"}, "passive": {"root": "Measurement", "mode": "pull"}}
        self.model["subscriptions"] = [{"id": port, "from": "source.out", "to": f"sink.{port}"} for port in ("first", "second")]
        plan = self.compile()
        payload = self.source_payload(plan)
        self.handler("sink", '''from measurement import Measurement
state = 0.0
def on_second(ctx, input):
    global state
    assert input.port == "second"
    state = input.message.value
def on_first(ctx, input):
    assert input.port == "first"
    ctx.publish_result(Measurement(sequence=input.message.sequence, value=input.message.value + state))
''')
        catalogue = plan["components"]["source"]["outputs"]["out"]["catalogue"]
        events = self.push(plan, payload, port="first") + self.push(plan, payload, port="second", index=2) + \
                 self.push(plan, payload, port="first", index=3)
        result, messages = self.run_node(plan, "sink", events)
        self.assertEqual(result.returncode, 0, result.stderr.decode())
        self.assertEqual([op for op, _ in messages], ["R", "P", "F", "F", "P", "F"])
        values = [self.codec.api.decode_dynamic(self.codec.api.prepare(catalogue), unpack_publication(body)[1]).fields["value"]
                  for op, body in messages if op == "P"]
        self.assertEqual(values, [3.5, 7.0])
        # A bad second activation does not invalidate the first completed callback.
        bad = self.push(plan, payload, port="first") + self.push(plan, b"invalid", port="second", index=2)
        result, messages = self.run_node(plan, "sink", bad)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual([op for op, _ in messages], ["R", "P", "F"])

    def test_callback_failures_never_finish_an_activation(self):
        plan = self.compile()
        self.handler("source", 'def on_clock(ctx, input):\n    raise RuntimeError("user failure")\n')
        result, messages = self.run_node(plan, "source", timer_frame(1))
        self.assertNotEqual(result.returncode, 0)
        self.assertIn(b"user failure", result.stderr)
        self.assertEqual([op for op, _ in messages], ["R"])

    def test_invalid_callbacks_and_initialization_fail_before_ready(self):
        plan = self.compile()
        for source, error in [('def on_clock(): pass\n', b"invalid callback signature"),
                              ('async def on_clock(ctx, input): pass\n', b"synchronous on_clock"),
                              ('def on_start(ctx): raise RuntimeError("init failure")\ndef on_clock(ctx, input): pass\n', b"init failure"),
                              ('def on_tick(ctx): pass\n', b"on_tick is removed")]:
            with self.subTest(error=error):
                self.handler("source", source)
                result, messages = self.run_node(plan, "source")
                self.assertNotEqual(result.returncode, 0)
                self.assertIn(error, result.stderr)
                self.assertEqual(messages, [])
        self.handler("sink", "# Missing on_input\n")
        result, messages = self.run_node(plan, "sink")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn(b"synchronous on_input", result.stderr)
        self.assertEqual(messages, [])

    def test_publication_is_forbidden_outside_activation_and_checks_output_type(self):
        plan = self.compile()
        for source, expected in [('from measurement import Measurement\ndef on_start(ctx): ctx.publish_out(Measurement())\ndef on_clock(ctx, input): pass\n', []),
                                 ('def on_clock(ctx, input): ctx.publish_out(object())\n', ["R"])]:
            with self.subTest(expected=expected):
                self.handler("source", source)
                result, messages = self.run_node(plan, "source", timer_frame(1))
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual([op for op, _ in messages], expected)

    def test_validation_does_not_create_handlers_and_build_never_overwrites_them(self):
        self.documents()
        validate(self.model_path, self.deployment_path, SDL_ROOT)
        self.assertFalse((self.root / "source.py").exists())
        self.assertFalse((self.root / "sink.py").exists())
        build(self.model_path, self.deployment_path, SDL_ROOT, self.destination)
        self.assertIn("def on_clock(ctx, input)", (self.root / "source.py").read_text())
        self.assertIn("def on_input(ctx, input)", (self.root / "sink.py").read_text())
        self.handler("sink", "# user code to keep\ndef on_input(ctx, input): pass\n")
        original = (self.root / "sink.py").read_bytes()
        self.model["components"]["sink"]["outputs"]["extra"] = {"root": "Measurement"}
        self.documents()
        build(self.model_path, self.deployment_path, SDL_ROOT, self.destination)
        self.assertEqual((self.root / "sink.py").read_bytes(), original)
        self.assertIn("def publish_extra", (self.destination / "components/sink/container.py").read_text())

    def test_handler_in_generated_directory_is_refused_before_generation(self):
        handler = self.destination / "generated/shared/python/measurement.py"
        handler.parent.mkdir(parents=True)
        handler.write_text("# user code to preserve\n")
        self.deployment["components"]["source"]["container"]["handler"] = str(handler)
        with self.assertRaisesRegex(ValueError, "outside the generated build directory"):
            self.compile()
        self.assertEqual(handler.read_text(), "# user code to preserve\n")

    def test_scaffolded_timed_callback_imports_and_accepts_timeout_reference(self):
        self.model["components"]["sink"]["inputs"]["input"]["timeout"] = .05
        self.documents()
        plan_path = build(self.model_path, self.deployment_path, SDL_ROOT, self.destination)
        plan = json.loads(plan_path.read_text())
        handler = self.root / "sink.py"
        handler.write_text(handler.read_text().replace(
            'raise NotImplementedError("Implement the input callback")',
            'assert input.timed_out and input.message is None and input.port == "input"'))
        result, messages = self.run_node(plan, "sink", timer_frame(1, port="input", time_ns=50_000_000))
        self.assertEqual(result.returncode, 0, result.stderr.decode())
        self.assertEqual([op for op, _ in messages], ["R", "F"])

    def test_received_catalogue_drives_typed_decode_after_schema_evolution(self):
        (self.root / "measurement.sdl").write_text("message Alpha {1: int32 x;} " +
            "message Measurement {1: required uint64 sequence; 2: required fl64 value; 3: optional string note;}")
        (self.root / "consumer").mkdir()
        (self.root / "consumer/measurement.sdl").write_text(SCHEMA)
        self.model["components"]["sink"]["inputs"]["input"]["schema"] = "consumer/measurement.sdl"
        self.model["components"]["sink"]["inputs"]["input"].pop("activates")
        self.model["components"]["sink"]["outputs"] = {}
        plan = self.compile()
        self.handler("source", SOURCE.replace("value=3.5", 'value=3.5, note="extra"'))
        result, messages = self.run_node(plan, "source", timer_frame(1))
        self.assertEqual(result.returncode, 0, result.stderr.decode())
        payload = unpack_publication(messages[1][1])[1]
        self.handler("sink", '''from measurement import Measurement
def on_input(ctx, input):
    message = input.message
    assert isinstance(message, Measurement)
    assert message.sequence == 7 and message.value == 3.5
''')
        catalogue = plan["components"]["source"]["outputs"]["out"]["catalogue"]
        result, messages = self.run_node(plan, "sink", self.push(plan, payload))
        self.assertEqual(result.returncode, 0, result.stderr.decode())
        self.assertEqual([op for op, _ in messages], ["R", "F"])

    def test_conflicting_python_modules_are_reported_during_compilation(self):
        (self.root / "other").mkdir()
        (self.root / "other/measurement.sdl").write_text(SCHEMA + "message Extra {1: int32 x;}")
        self.model["components"]["sink"]["outputs"]["result"]["schema"] = "other/measurement.sdl"
        with self.assertRaisesRegex(ValueError, "conflicting generated module"):
            self.compile()

    def test_python_stdlib_module_collision_is_reported_during_compilation(self):
        for module in ("types", "json", "sys", "math", "_socket", "email", "winreg", "winsound", "msvcrt",
                       "sdl_runtime", "ose"):
            with self.subTest(module=module):
                (self.root / f"{module}.sdl").write_text(SCHEMA)
                self.model["schemas"] = f"{module}.sdl"
                with self.assertRaisesRegex(ValueError, "shadows the standard library"):
                    self.compile()
