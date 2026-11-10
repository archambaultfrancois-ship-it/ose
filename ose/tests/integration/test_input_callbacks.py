"""V2 reference callbacks with real dynamic SDL and a finite feedback loop."""
import os
from types import SimpleNamespace
import unittest

from ose.codec.runtime import TypedCodec
from ose.codec.sdl2 import SDL2
from ose.delivery.buffers import Buffer
from ose.delivery.policies import SubscriptionPolicy as Policy
from ose.delivery.stores import MemoryStore
from ose.execution.input_callbacks import InputCallbacks
from ose.modeling.validation import validate_graph

SDL_ROOT = os.environ.get("SDL_ROOT")
MS = 1_000_000
CAT = "SDL2|Value{1:uint32:count;}"


@unittest.skipUnless(SDL_ROOT, "set SDL_ROOT")
class InputCallbackTests(unittest.TestCase):
    def setUp(self):
        self.codec = SDL2(SDL_ROOT)
        api = self.codec.api

        class Value(api.SdlMessage):
            _SDL_NAME = "Value"
            _SDL_DESCRIPTOR = CAT
            _SDL_FIXED_SIZE = 4
            _SDL_FIELDS = ((1, "count", "required", "uint32", ()),)

        class Extended(api.SdlMessage):
            _SDL_NAME = "Value"
            _SDL_DESCRIPTOR = "SDL2|Value{1:uint32:count;2:uint32:extra;}"
            _SDL_FIXED_SIZE = 8
            _SDL_FIELDS = ((1, "count", "required", "uint32", ()), (2, "extra", "required", "uint32", ()))

        self.Value, self.Extended = Value, Extended
        self.now, self.emitted = 0, []

    def record(self, number, *, cls=None, app_id=None):
        cls = cls or self.Value
        codec = TypedCodec(cls)
        value = cls(count=number, **({"extra": 7} if cls is self.Extended else {}))
        result = dict(id=f"run:{number}", epoch="run", sequence=number, topic="source.out",
                      catalogue=codec.catalogue, body=codec.encode(value))
        if app_id is not None:
            result["app_id"] = app_id
        return result

    def host(self, node, handler, *, policies=None, emit=None):
        buffers = {port: Buffer(Policy.resolve((policies or {}).get(port)), MemoryStore(), clock=lambda: self.now / 1e9)
                   for port, desc in node["inputs"].items() if desc.get("kind") != "timer"}
        host = InputCallbacks(node, buffers, handler,
            {port: self.Value for port in buffers}, {port: self.Value for port in node["outputs"]},
            clock=lambda: self.now, emit=emit or self.emitted.append)
        self.addCleanup(host.close)
        return host

    def test_message_and_timeout_use_one_callback_with_one_input_reference(self):
        seen = []

        def callback(ctx, input):
            seen.append((input.port, input.event, input.message.count if input.message else None, input.elapsed))
            ctx.publish("alarm" if input.timed_out else "out", self.Value(count=1), id=input.app_id)

        host = self.host(dict(inputs=dict(input=dict(root="Value", timeout=.01,
            activates=dict(message=["out"], timeout=["alarm"]))),
            outputs=dict(out=dict(root="Value"), alarm=dict(root="Value"))), SimpleNamespace(on_input=callback))
        host.admit("input", self.record(1, app_id=(1 << 64) - 1))
        self.assertTrue(host.dispatch())
        self.now = 35 * MS
        self.assertTrue(host.dispatch())
        self.assertEqual(seen, [("input", "message", 1, 1), ("input", "timeout", None, 3)])
        self.assertEqual([item.port for item in self.emitted], ["out", "alarm"])
        self.assertEqual(self.emitted[0].app_id, (1 << 64) - 1)

    def test_each_message_keeps_dynamic_contract_decoding_and_exact_ids(self):
        seen = []

        def callback(ctx, input):
            seen.append((input.message.count, input.app_id))

        host = self.host(dict(inputs=dict(input=dict(root="Value")), outputs={}), SimpleNamespace(on_input=callback))
        for number, cls, app_id in ((1, self.Value, 0), (2, self.Extended, (1 << 64) - 1), (3, self.Value, 0)):
            record = self.record(number, cls=cls, app_id=app_id)
            record["body"] = bytearray(record["body"])
            host.admit("input", record)
            record["body"].clear()  # The inbox owns its immutable envelope bytes.
        while host.dispatch():
            pass
        self.assertEqual(seen, [(1, 0), (2, (1 << 64) - 1), (3, 0)])

    def test_invalid_sdl_neither_enters_inbox_nor_resets_inactivity(self):
        host = self.host(dict(inputs=dict(input=dict(root="Value", timeout=.01)), outputs={}),
                         SimpleNamespace(on_input=lambda ctx, input: None))
        self.now = 9 * MS
        invalid = self.record(1)
        invalid["body"] = invalid["body"][:-1]
        with self.assertRaises(ValueError):
            host.admit("input", invalid)
        self.assertEqual(host.scheduler.next_event(self.now), 10 * MS)
        self.assertEqual(host.scheduler.buffers["input"].store.size(), 0)

    def test_valid_receipt_resets_inactivity_even_if_the_inbox_is_full(self):
        host = self.host(dict(inputs=dict(input=dict(root="Value", timeout=.01)), outputs={}),
                         SimpleNamespace(on_input=lambda ctx, input: None), policies=dict(input=dict(capacity=1)))
        host.admit("input", self.record(1))
        self.now = 9 * MS
        self.assertEqual(host.admit("input", self.record(2, cls=self.Extended)), "full")
        host.dispatch()
        self.assertEqual(host.scheduler.next_event(self.now), 19 * MS)

    def test_pull_decodes_fresh_values_and_cancels_consumption_on_failure(self):
        saved = []

        def callback(ctx, input):
            inbox = ctx.inbox("feedback")
            first = inbox.get(id=0)
            first.message.count = 999
            self.assertEqual(inbox.get(id=0).message.count, 1)
            saved.append(inbox)
            self.assertEqual(inbox.pull(id=0).message.count, 1)
            raise RuntimeError("callback failed after consumption")

        host = self.host(dict(inputs=dict(input=dict(root="Value"), feedback=dict(root="Value", mode="pull")),
                              outputs={}), SimpleNamespace(on_input=callback))
        host.admit("feedback", self.record(1, app_id=0))
        host.admit("input", self.record(2))
        with self.assertRaisesRegex(RuntimeError, "failed after consumption"):
            host.dispatch()
        self.assertEqual(host.scheduler.buffers["feedback"].store.size(), 1)
        self.assertEqual(host.scheduler.buffers["input"].store.size(), 1)
        with self.assertRaises(RuntimeError):
            saved[0].get()

    def test_failed_callback_discards_outputs_and_false_return_still_succeeds(self):
        def callback(ctx, input):
            ctx.publish("out", self.Value(count=7))
            if ctx.parameters["fail"]:
                raise RuntimeError("failed after publication")
            return False

        host = self.host(dict(inputs=dict(input=dict(root="Value", activates=dict(message=["out"]))),
            outputs=dict(out=dict(root="Value")), parameters=dict(fail=True)), SimpleNamespace(on_input=callback))
        host.admit("input", self.record(1))
        with self.assertRaisesRegex(RuntimeError, "failed after publication"):
            host.dispatch()
        self.assertEqual(self.emitted, [])
        self.assertEqual(host.scheduler.buffers["input"].store.size(), 1)
        host.context.parameters["fail"] = False
        self.assertTrue(host.dispatch())
        self.assertEqual(len(self.emitted), 1)
        self.assertEqual(host.scheduler.buffers["input"].store.size(), 0)

    def test_manual_timer_initialization_and_removed_legacy_callbacks(self):
        node = dict(inputs=dict(clock=dict(kind="timer", timer=dict(mode="one_shot", delay=.01, start="manual"))), outputs={})
        events = []
        host = self.host(node, SimpleNamespace(on_start=lambda ctx: ctx.arm("clock"),
            on_clock=lambda ctx, input: events.append(input.event), on_stop=lambda ctx: events.append("stop")))
        self.assertFalse(host.dispatch())
        self.now = 10 * MS
        self.assertTrue(host.dispatch())
        host.close()
        host.close()
        self.assertEqual(events, ["timeout", "stop"])
        with self.assertRaises(RuntimeError):
            host.context.arm("clock")
        with self.assertRaisesRegex(ValueError, "on_tick is removed"):
            self.host(node, SimpleNamespace(on_tick=lambda ctx: None))

    def test_callbacks_cannot_publish_without_an_event_link(self):
        host = self.host(dict(inputs=dict(input=dict(root="Value")), outputs=dict(out=dict(root="Value"))),
                         SimpleNamespace(on_input=lambda ctx, input: ctx.publish("out", self.Value(count=1))))
        host.admit("input", self.record(1))
        with self.assertRaisesRegex(ValueError, "input:message cannot activate output out"):
            host.dispatch()

    def test_feedback_loop_runs_without_an_initial_message_then_quiesces(self):
        traces = []
        for dispatch_order in (("a", "b"), ("b", "a")):
            with self.subTest(order=dispatch_order):
                self.now = 0
                models = dict(
                    a=dict(inputs=dict(clock=dict(kind="timer", timer=dict(mode="periodic", delay=.01),
                                                  activates=dict(timeout=["out"])),
                                       feedback=dict(root="Value", mode="pull")), outputs=dict(out=dict(root="Value"))),
                    b=dict(inputs=dict(input=dict(root="Value", activates=dict(message=["out"]))),
                           outputs=dict(out=dict(root="Value"))))
                subscriptions = [{"id": "forward", "from": "a.out", "to": "b.input"},
                                 {"id": "feedback", "from": "b.out", "to": "a.feedback"}]
                model = dict(version=2, schemas="value.sdl", components=models, subscriptions=subscriptions)
                deployment = dict(version=1, components={name: dict(command=["unused"]) for name in models})
                validate_graph(model, deployment)
                hosts, trace, feedback = {}, [], []
                sequence, iterations = 0, 0

                def emit(source, publication):
                    nonlocal sequence
                    sequence += 1
                    trace.append((self.now, source, publication.app_id,
                                  self.codec.validate(publication.catalogue, "Value", publication.body).fields["count"]))
                    target, port = ("b", "input") if source == "a" else ("a", "feedback")
                    record = dict(id=f"run:{sequence}", epoch="run", sequence=sequence, topic=f"{source}.out",
                                  catalogue=publication.catalogue, body=publication.body, app_id=publication.app_id)
                    self.assertEqual(hosts[target].admit(port, record), "accepted")

                def on_clock(ctx, input):
                    previous = ctx.inbox("feedback").pull()
                    feedback.append(previous.message.count if previous else None)
                    ctx.publish("out", self.Value(count=ctx.activation_index), id=ctx.activation_index)
                    if ctx.activation_index == 3:
                        ctx.disarm("clock")

                def on_input(ctx, input):
                    ctx.publish("out", self.Value(count=input.message.count + 100), id=input.app_id)

                hosts["a"] = self.host(models["a"], SimpleNamespace(on_clock=on_clock), emit=lambda item: emit("a", item))
                hosts["b"] = self.host(models["b"], SimpleNamespace(on_input=on_input), emit=lambda item: emit("b", item))
                while True:
                    times = [time for host in hosts.values()
                             if (time := host.scheduler.next_event(self.now)) is not None]
                    if not times:
                        break
                    self.now = min(times)
                    for name in dispatch_order:
                        iterations += hosts[name].dispatch()
                    self.assertLess(iterations, 20, "feedback did not quiesce")
                self.assertEqual(iterations, 6)
                self.assertEqual(feedback, [None, 101, 102])
                self.assertEqual(hosts["a"].scheduler.buffers["feedback"].store.size(), 1)
                traces.append(trace)
        self.assertEqual(traces[0], traces[1])

    def test_push_feedback_can_update_state_without_activating_an_output(self):
        nodes = dict(
            a=dict(inputs=dict(clock=dict(kind="timer", timer=dict(mode="one_shot", delay=.01),
                                          activates=dict(timeout=["out"])), feedback=dict(root="Value")),
                   outputs=dict(out=dict(root="Value"))),
            b=dict(inputs=dict(input=dict(root="Value", activates=dict(message=["out"]))),
                   outputs=dict(out=dict(root="Value"))))
        validate_graph(dict(version=2, schemas="value.sdl", components=nodes, subscriptions=[
            {"id": "forward", "from": "a.out", "to": "b.input"},
            {"id": "feedback", "from": "b.out", "to": "a.feedback"}]),
            dict(version=1, components={name: dict(command=["unused"]) for name in nodes}))
        hosts, state, emitted = {}, [], []

        def emit(source, publication):
            emitted.append(publication)
            target, port = ("b", "input") if source == "a" else ("a", "feedback")
            hosts[target].admit(port, dict(id=f"run:{len(emitted)}", epoch="run", sequence=len(emitted),
                topic=source + ".out", catalogue=publication.catalogue, body=publication.body))

        def on_feedback(ctx, input):
            state.append(input.message.count)
            with self.assertRaisesRegex(ValueError, "feedback:message cannot activate output out"):
                ctx.publish("out", self.Value(count=99))

        hosts["a"] = self.host(nodes["a"], SimpleNamespace(
            on_clock=lambda ctx, input: ctx.publish("out", self.Value(count=1)), on_feedback=on_feedback),
            emit=lambda item: emit("a", item))
        hosts["b"] = self.host(nodes["b"], SimpleNamespace(
            on_input=lambda ctx, input: ctx.publish("out", self.Value(count=input.message.count + 1))),
            emit=lambda item: emit("b", item))
        self.now = 10 * MS
        self.assertTrue(hosts["a"].dispatch())
        self.assertTrue(hosts["b"].dispatch())
        self.assertTrue(hosts["a"].dispatch())
        self.assertEqual(state, [2])
        self.assertEqual(len(emitted), 2)
        self.assertTrue(all(host.scheduler.next_event(self.now) is None for host in hosts.values()))
