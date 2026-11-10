"""V2 Python subprocesses through CH/2, real transports and parallel simulation."""
import asyncio
import copy
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest

from ose.delivery.policies import SubscriptionPolicy
from ose.delivery.buffers import Buffer
from ose.delivery.stores import MemoryStore
from ose.codec.runtime import TypedCodec
from ose.codec.sdl2 import SDL2
from ose.execution.activation import InputScheduler
from ose.execution.input_ipc import InputServices
from ose.execution.process import start_component, stop_component, input_events
from ose.execution.adapter import Adapter
from ose.execution.broker import Broker
from ose.execution.simulation import Simulation
from ose.modeling.compiler import build
from ose.parameters.service import ParameterService
from ose.parameters.bindings import LocalBindings
from ose.recording.component import ComponentRecording

SDL_ROOT = os.environ.get("SDL_ROOT")
SCHEMA = "message Value {1: required uint32 count;}"
A = '''import time
from value import Value
IDENTITY = (1 << 64) - 1
def on_start(ctx):
    ctx.declare_measurement("feedback.count")
    if ctx.parameters.get("arm"): ctx.arm("clock")
def on_clock(ctx, input):
    time.sleep(ctx.parameters.get("delay", 0))
    if ctx.parameters.get("bypass"):
        from ose.execution.input_ipc import publication_data
        payload = ctx.codec.api.encode(ctx._encoders["out"], Value(count=9))
        ctx.session.send("P", publication_data("out", payload))
        return
    inbox = ctx.inbox("feedback")
    previous = inbox.get(id=IDENTITY)
    if previous is not None:
        assert inbox.get(handle=previous.handle).message.count == previous.message.count
        assert previous.app_id == IDENTITY
        assert inbox.pull(handle=previous.handle).handle == previous.handle
        assert inbox.get(handle=previous.handle) is None
        ctx.measure("feedback.count", previous.message.count)
        if ctx.parameters.get("fail"): raise RuntimeError("failed after deferred pull")
    ctx.publish_out(Value(count=ctx.activation_index), id=IDENTITY)
    if ctx.activation_index == 3: ctx.disarm("clock")
'''
B = '''import time
from value import Value
def on_input(ctx, input):
    if input.timed_out:
        assert input.message is None and input.elapsed == 1
        return False
    time.sleep(ctx.parameters.get("delay", 0))
    assert input.app_id == (1 << 64) - 1
    ctx.publish_out(Value(count=input.message.count + 100), id=input.app_id)
'''


@unittest.skipUnless(SDL_ROOT, "set SDL_ROOT")
class InputProcessTests(unittest.IsolatedAsyncioTestCase):
    SOURCE_LANGUAGE = RESPONSE_LANGUAGE = "python"
    SOURCE_HANDLER, RESPONSE_HANDLER = A, B
    SOURCE_SPEC = RESPONSE_SPEC = None

    @classmethod
    def setUpClass(cls):
        temporary = tempfile.TemporaryDirectory(prefix="ose input v2 ")
        cls.addClassCleanup(temporary.cleanup)
        cls.root = Path(temporary.name)
        (cls.root / "value.sdl").write_text(SCHEMA)
        source_suffix = {"python": ".py", "rust": ".rs", "c": ".c", "java": ".java", "ada": ".adb"}[cls.SOURCE_LANGUAGE]
        response_suffix = {"python": ".py", "rust": ".rs", "c": ".c", "java": ".java", "ada": ".adb"}[cls.RESPONSE_LANGUAGE]
        if cls.SOURCE_LANGUAGE not in ("java", "ada"):
            (cls.root / ("a" + source_suffix)).write_text(cls.SOURCE_HANDLER)
        if cls.RESPONSE_LANGUAGE not in ("java", "ada"):
            (cls.root / ("b" + response_suffix)).write_text(cls.RESPONSE_HANDLER)
        components, subscriptions = {}, []
        for source, sink in (("a", "b"), ("c", "d")):
            components[source] = dict(inputs=dict(
                clock=dict(kind="timer", timer=dict(mode="periodic", delay=.01), activates=dict(timeout=["out"])),
                feedback=dict(root="Value", mode="pull")), outputs=dict(out=dict(root="Value")))
            components[sink] = dict(inputs=dict(input=dict(root="Value", activates=dict(message=["out"]))),
                                    outputs=dict(out=dict(root="Value")))
            subscriptions += [dict(id=source, **{"from": source + ".out", "to": sink + ".input"}),
                              dict(id=sink, **{"from": sink + ".out", "to": source + ".feedback"})]
        model = dict(version=2, schemas="value.sdl", components=components, subscriptions=subscriptions)
        def container(name):
            source = name in ("a", "c")
            language = cls.SOURCE_LANGUAGE if source else cls.RESPONSE_LANGUAGE
            if language == "ada":
                unit = name.upper() + "_Handler"
                body = cls.SOURCE_HANDLER if source else cls.RESPONSE_HANDLER
                spec = cls.SOURCE_SPEC if source else cls.RESPONSE_SPEC
                for suffix, text in ((".ads", spec), (".adb", body)):
                    (cls.root / (unit.lower() + suffix)).write_text(text.replace("COMPONENT_NAME", name).replace("UNIT_NAME", unit))
                return dict(language="ada", handler=unit.lower() + ".adb", unit=unit)
            if language == "java":
                identity = name.upper()
                text = cls.SOURCE_HANDLER if source else cls.RESPONSE_HANDLER
                (cls.root / (identity + ".java")).write_text(text.replace("COMPONENT_NAME", name).replace("HANDLER_NAME", identity))
                return dict(language="java", handler=identity + ".java", **{"class": identity})
            return dict(language=language, handler="a" + source_suffix if source else "b" + response_suffix)
        deployment = dict(version=1, components={name: dict(container=container(name)) for name in components})
        (cls.root / "model.json").write_text(json.dumps(model))
        (cls.root / "deployment.json").write_text(json.dumps(deployment))
        plan = build(cls.root / "model.json", cls.root / "deployment.json", SDL_ROOT, cls.root / "build")
        cls.template = json.loads(plan.read_text())

    async def test_feedback_traces_match_with_one_and_four_workers_and_reversed_delays(self):
        traces = []
        for workers, delays in ((1, (0., .005)), (4, (.01, 0.))):
            plan = copy.deepcopy(self.template)
            for name, delay in zip(("a", "c"), delays):
                plan["components"][name]["parameters"] = dict(delay=delay)
            path = self.root / f"trace-{workers}.jsonl"
            engine = Simulation(plan, .1, workers=workers, trace=path)
            summary = await engine.execute()
            self.assertEqual((summary["ticks"], summary["publications"], summary["stop_reason"]), (12, 12, "quiescent"))
            self.assertEqual(summary["inputs"]["a"]["feedback"]["depth"], 1)
            self.assertEqual(summary["inputs"]["c"]["feedback"]["depth"], 1)
            trace = [json.loads(line) for line in path.read_text().splitlines()]
            self.assertTrue(any(item["event"] == "wave" and len(item["components"]) == 2 for item in trace))
            for item in trace:
                if item["event"] == "publication":
                    self.assertEqual(item["app_id"], (1 << 64) - 1)
            traces.append(path.read_bytes())
        self.assertEqual(traces[0], traces[1])

    async def test_failed_subprocess_cancels_its_deferred_withdrawal(self):
        plan = copy.deepcopy(self.template)
        plan["components"]["a"]["parameters"] = dict(fail=True)
        engine = Simulation(plan, .1, workers=1)
        with self.assertRaises(asyncio.IncompleteReadError):
            await engine.execute()
        self.assertEqual(engine.summary["stop_reason"], "failed")
        self.assertEqual(engine.summary["inputs"]["a"]["feedback"]["depth"], 1)

    async def test_manual_timer_can_be_armed_during_initialization(self):
        plan = copy.deepcopy(self.template)
        for name in ("a", "c"):
            plan["components"][name]["inputs"]["clock"]["timer"]["start"] = "manual"
            plan["components"][name]["parameters"] = dict(arm=True)
        summary = await Simulation(plan, .1, workers=4).execute()
        self.assertEqual((summary["ticks"], summary["stop_reason"]), (12, "quiescent"))

    async def test_empty_push_inactivity_repeats_without_a_message(self):
        plan = copy.deepcopy(self.template)
        plan["components"] = {"b": plan["components"]["b"]}
        plan["subscriptions"] = []
        plan["components"]["b"]["inputs"]["input"]["timeout"] = .01
        summary = await Simulation(plan, .035).execute()
        self.assertEqual((summary["ticks"], summary["publications"], summary["stop_reason"]), (3, 0, "duration"))

    async def test_parent_checks_output_rights_even_if_the_sdk_is_bypassed(self):
        plan = copy.deepcopy(self.template)
        node = plan["components"]["a"]
        node["inputs"]["clock"]["activates"] = {}
        node["parameters"] = dict(bypass=True)
        plan["components"], plan["subscriptions"] = {"a": node}, []
        with self.assertRaisesRegex(ValueError, "clock:timeout cannot activate output out"):
            await Simulation(plan, .1).execute()

    async def test_model_two_explicitly_rejects_an_old_ready(self):
        plan = copy.deepcopy(self.template)
        node = plan["components"]["a"]
        node["command"] = [sys.executable, "-c", "from ose.execution.component_io import SessionIO; s=SessionIO(); s.send('R')"]
        plan["components"], plan["subscriptions"] = {"a": node}, []
        with self.assertRaisesRegex(ValueError, "requires a CH/2 component SDK"):
            await Simulation(plan, .1).execute()

    async def test_pull_over_ch_two_decodes_a_different_catalogue_on_each_message(self):
        node = copy.deepcopy(self.template["components"]["a"])
        codec = SDL2(SDL_ROOT)
        api = codec.api

        class Base(api.SdlMessage):
            _SDL_NAME = "Value"
            _SDL_DESCRIPTOR = "SDL2|Value{1:uint32:count;}"
            _SDL_FIXED_SIZE = 4
            _SDL_FIELDS = ((1, "count", "required", "uint32", ()),)

        class Extended(api.SdlMessage):
            _SDL_NAME = "Value"
            _SDL_DESCRIPTOR = "SDL2|Value{1:uint32:count;2:uint32:extra;}"
            _SDL_FIXED_SIZE = 8
            _SDL_FIELDS = ((1, "count", "required", "uint32", ()), (2, "extra", "required", "uint32", ()))

        buffer = Buffer(SubscriptionPolicy(), MemoryStore())
        self.addCleanup(buffer.store.close)
        scheduler = InputScheduler(node, {"feedback": buffer})
        scheduler.start()
        now = 0
        services = InputServices(scheduler, lambda: now)
        process = await start_component(node, inputs=services)
        try:
            for index, cls in enumerate((Base, Extended, Base), 1):
                now = index * 10_000_000
                typed = TypedCodec(cls)
                message = cls(count=index, **({"extra": 7} if cls is Extended else {}))
                body = typed.encode(message)
                codec.compare(typed.catalogue, node["inputs"]["feedback"]["catalogue"], "Value")
                codec.validate_only(typed.catalogue, "Value", body)
                scheduler.admit("feedback", dict(id=f"run:{index}", epoch="run", sequence=index,
                    topic="external.out", catalogue=typed.catalogue, body=body, app_id=(1 << 64) - 1), now)
                reference = scheduler.select(now)
                outputs = [item async for item in input_events(process, node, reference, index, services)]
                self.assertEqual(len(outputs), 1)
                self.assertEqual(outputs[0][2], (1 << 64) - 1)
                self.assertEqual(buffer.store.size(), 1)
                scheduler.complete(reference, now)
                self.assertEqual(buffer.store.size(), 0)
            self.assertIsNone(scheduler.next_event(now))
        finally:
            await stop_component(process)

    async def test_input_callbacks_keep_parameter_recording_log_and_measurement_services(self):
        with tempfile.TemporaryDirectory(prefix="ose v2 services ") as directory:
            root = Path(directory)
            (root / "value.sdl").write_text(SCHEMA)
            (root / "handler.py").write_text('''from value import Value
def on_start(ctx):
    value = ctx.parameters_settings()
    assert value.count == 40
    value.count = 41
    ctx.set_parameters_settings(value)
    ctx.declare_recording_point("debug.value", Value)
    ctx.declare_measurement("observed.count")
    ctx.log("INFO", "initialized")
def on_clock(ctx, input):
    value = ctx.parameters_settings()
    ctx.publish_out(value)
    ctx.record("debug.value", value)
    ctx.measure("observed.count", value.count)
    value.count += 1
    ctx.set_parameters_settings(value)
''')
            node = dict(inputs=dict(clock=dict(kind="timer", timer=dict(mode="periodic", delay=.01),
                activates=dict(timeout=["out"]))), outputs=dict(out=dict(root="Value")),
                parameter_bindings=dict(settings=dict(path="shared/settings", access="read_write")))
            model = dict(version=2, schemas="value.sdl", components=dict(probe=node), subscriptions=[],
                shared_parameters={"shared/settings": dict(root="Value", initial=dict(count=40))})
            deployment = dict(version=1, components=dict(probe=dict(container=dict(language="python", handler="handler.py"))))
            (root / "model.json").write_text(json.dumps(model))
            (root / "deployment.json").write_text(json.dumps(deployment))
            path = build(root / "model.json", root / "deployment.json", SDL_ROOT, root / "build")
            plan = json.loads(path.read_text())
            node, codec = plan["components"]["probe"], SDL2(SDL_ROOT)
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
                    payload = outputs[0][1]
                    value = codec.api.decode_dynamic(codec.api.prepare(node["outputs"]["out"]["catalogue"]), payload)
                    observed.append(value.fields["count"])
                    self.assertEqual(captured[-1], ("debug.value", payload))
                self.assertEqual(observed, [41, 42])
                self.assertEqual(len(captured), 2)
                self.assertEqual(process.measurements.snapshot(), {"observed.count": 42.})
            finally:
                await stop_component(process)

    async def test_real_tcp_udp_feedback_preserves_ids_and_does_not_reactivate_pull(self):
        plan = copy.deepcopy(self.template)
        plan["components"] = {name: plan["components"][name] for name in ("a", "b")}
        plan["subscriptions"] = [sub for sub in plan["subscriptions"] if sub["id"] in ("a", "b")]
        plan["components"]["a"]["inputs"]["clock"]["timer"]["delay"] = .1
        plan["subscriptions"][1]["policy"] = SubscriptionPolicy.resolve(
            dict(ack="received", retry=dict(ack_timeout_ms=50)), "udp").to_dict()
        broker, tasks = Broker(plan), []
        adapters = {name: Adapter(plan, name) for name in plan["components"]}

        async def until(predicate):
            async def wait():
                while not predicate():
                    for task in tasks:
                        if task.done():
                            task.result()
                    await asyncio.sleep(.005)
            await asyncio.wait_for(wait(), 8)

        try:
            await broker.start()
            tasks = [asyncio.create_task(adapter.run("127.0.0.1", broker.port)) for adapter in adapters.values()]
            await until(lambda: broker.ready == {"a", "b"})
            broker.enabled = True
            await until(lambda: adapters["a"].metrics.get("ticks") == 3 and adapters["b"].metrics.get("ticks") == 3
                        and broker.engines["b"].metrics["acknowledged"] == 3)
            self.assertEqual(adapters["a"].buffers["feedback"].metrics["accepted"], 3)
            self.assertGreaterEqual(adapters["a"].buffers["feedback"].store.size(), 1)
            self.assertIsNone(adapters["a"].scheduler.next_event(adapters["a"]._input_now()))
            self.assertEqual(broker.retained["b.out"]["app_id"], (1 << 64) - 1)
            self.assertFalse(broker.errors)
        finally:
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            await broker.close()
