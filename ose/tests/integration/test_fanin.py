"""Multiple producers through real transports and unchanged generated callbacks."""
import asyncio
import copy
import json
import os
from pathlib import Path
import struct
import tempfile
import time
import unittest

from ose.codec.sdl2 import SDL2
from ose.delivery.policies import SubscriptionPolicy as Policy
from ose.execution.adapter import Adapter
from ose.execution.broker import Broker
from ose.execution.distributed import Coordinator, Worker
from ose.modeling.compiler import build

SDL_ROOT = os.environ.get("SDL_ROOT")


class FaninFixture:
    @classmethod
    def setUpClass(cls):
        temporary = tempfile.TemporaryDirectory()
        cls.addClassCleanup(temporary.cleanup)
        cls.root = Path(temporary.name)
        (cls.root / "measurement.sdl").write_text("message Measurement {uint64 sequence; fl64 value;}")
        (cls.root / "idle.py").write_text("def on_tick(ctx): pass\n")
        (cls.root / "sink.py").write_text(
            "import json\ndef on_input(ctx, message):\n"
            " with open(ctx.parameters['path'], 'a') as stream: stream.write(json.dumps(message.sequence)+'\\n')\n")
        desc = {"root": "Measurement"}
        model = dict(version=1, schemas="measurement.sdl", components={
            name: dict(inputs={}, outputs={"out": desc}, trigger=dict(mode="periodic", period_ms=1000))
            for name in ("left", "right")}, subscriptions=[
                dict(id=name, **{"from": name + ".out", "to": "sink.input"}) for name in ("left", "right")])
        model["components"]["sink"] = dict(inputs={"input": desc},
            outputs={"input": desc} if getattr(cls, "sink_output", False) else {},
            trigger=dict(mode="periodic", period_ms=5), parameters={"path": "unused"})
        deployment = dict(version=1, components={name: dict(container=dict(language="python",
            handler="sink.py" if name == "sink" else "idle.py")) for name in model["components"]})
        (cls.root / "model.json").write_text(json.dumps(model))
        (cls.root / "deployment.json").write_text(json.dumps(deployment))
        path = build(cls.root / "model.json", cls.root / "deployment.json", SDL_ROOT, cls.root / "build")
        cls.template = json.loads(path.read_text())
        cls.codec = SDL2(SDL_ROOT)

    async def asyncSetUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.path = Path(temporary.name) / "values"
        self.plan = copy.deepcopy(self.template)
        self.plan["components"]["sink"]["parameters"]["path"] = str(self.path)
        self.tasks, self.workers = [], {}
        self.server = None
        self.addAsyncCleanup(self.shutdown)

    async def shutdown(self):
        for task in self.tasks:
            task.cancel()
        await asyncio.gather(*self.tasks, return_exceptions=True)
        if self.server is not None:
            await self.server.close()

    async def until(self, predicate):
        deadline = time.monotonic() + 8
        while not predicate():
            for task in self.tasks:
                if task.done():
                    task.result()
                    self.fail("receiver stopped")
            if time.monotonic() >= deadline:
                self.fail("fan-in condition timed out")
            await asyncio.sleep(.01)

    async def start(self, qos=None, *, distributed=False):
        self.distributed = distributed
        for sub in self.plan["subscriptions"]:
            settings = dict(qos or {})
            if sub["id"] == "right":
                settings.update(ack="received", retry={"ack_timeout_ms": 2000})
            transport = ("udp" if sub["id"] == "right" and not distributed
                         and settings.get("overflow") != "wait" else "tcp")
            sub["policy"] = Policy.resolve(settings, transport).to_dict()
        if distributed:
            self.plan["execution"] = "distributed"
            self.plan["subscriptions"][0]["routing"] = "direct"
            self.server = Coordinator(self.plan, lease_seconds=2)
            await self.server.start()
            for name in self.plan["components"]:
                worker = Worker(self.plan, name, "127.0.0.1", self.server.port)
                self.workers[name] = worker
                self.tasks.append(asyncio.create_task(worker.run()))
                await self.until(worker.started.is_set)
            self.sink = self.workers["sink"]
            await self.until(lambda: all(worker.started_at for worker in self.workers.values()))
        else:
            self.server = Broker(self.plan)
            await self.server.start()
            self.sink = Adapter(self.plan, "sink")
            self.tasks.append(asyncio.create_task(self.sink.run("127.0.0.1", self.server.port)))
            await self.until(lambda: "sink" in self.server.ready)
            self.server.enabled = True
            await self.until(lambda: self.sink.activated_at_ns)
        # Accumulate admissions before allowing the unchanged callback to run.
        self.sink.scheduler.next_tick = float("inf")
        self.buffer = self.sink.buffers["input"]

    async def publish(self, source, number):
        desc = self.plan["components"][source]["outputs"]["out"]
        body = self.codec.wrap(desc["catalogue"], b"\x01" + struct.pack(">Qd", number, float(number)))
        before = self.buffer.metrics["accepted"] + self.buffer.metrics["refused"]
        if self.distributed:
            await self.workers[source].publish(source + ".out", body)
        else:
            await self.server.publish(source + ".out", body)
        await self.until(lambda: self.buffer.metrics["accepted"] + self.buffer.metrics["refused"] > before)

    def values(self):
        return [json.loads(line) for line in self.path.read_text().splitlines()] if self.path.exists() else []

    async def resume(self, expected):
        self.sink.scheduler.next_tick = 0
        self.sink.activity.set()
        await self.until(lambda: len(self.values()) >= len(expected))
        self.assertEqual(self.values()[:len(expected)], expected)

    async def exercise(self, *, latest=False, distributed=False):
        await self.start({"buffer": "latest"} if latest else {}, distributed=distributed)
        for source, value in (("left", 10), ("right", 20), ("left", 30)):
            await self.publish(source, value)
        self.assertEqual(list(self.sink.buffers), ["input"])
        self.assertEqual(self.buffer.store.size(), 1 if latest else 3)
        await self.until(lambda: self.server.engines["right"].metrics["acknowledged"] == 1)
        await self.resume([30] if latest else [10, 20, 30])
        if latest:
            self.assertEqual(set(self.values()), {30})


@unittest.skipUnless(SDL_ROOT, "set SDL_ROOT")
class FaninTests(FaninFixture, unittest.IsolatedAsyncioTestCase):
    async def test_local_fifo_merges_tcp_udp_with_independent_ack(self):
        await self.exercise()

    async def test_local_latest_merges_tcp_udp(self):
        await self.exercise(latest=True)

    async def test_required_input_runs_while_another_producer_is_silent(self):
        await self.start()
        await self.publish("left", 10)
        await self.resume([10])

    async def test_distributed_fifo_merges_direct_and_broker_routes(self):
        await self.exercise(distributed=True)

    async def test_distributed_latest_merges_independent_source_sequences(self):
        await self.exercise(latest=True, distributed=True)

    async def check_obsolete_latest(self, *, distributed=False):
        await self.start({"buffer": "latest"}, distributed=distributed)
        self.buffer.store.seen_limit = 1
        await self.publish("left", 10)
        old = dict(self.buffer.take())
        await self.publish("right", 20)
        if distributed:
            old["epoch"] = self.sink.epoch
            result = await self.sink._admit("left", old, old["body"])
            self.assertEqual(result, "stale")
        else:
            await self.sink._tcp_data(dict(old, subscription="left", op="data"), old["body"])
            await self.until(lambda: self.buffer.metrics["stale"] == 1)
        await self.resume([20])

    async def test_local_latest_rejects_old_source_value_after_another_source(self):
        await self.check_obsolete_latest()

    async def test_distributed_latest_rejects_old_source_value_after_another_source(self):
        await self.check_obsolete_latest(distributed=True)

    async def test_shared_fifo_rejects_new_message_at_global_capacity(self):
        await self.start({"capacity": 2})
        for source, value in (("left", 10), ("right", 20), ("left", 30)):
            await self.publish(source, value)
        self.assertEqual(self.buffer.metrics["refused"], 1)
        await self.resume([10, 20])

    async def test_shared_fifo_drops_oldest_across_sources(self):
        await self.start({"capacity": 2, "overflow": "drop_oldest"})
        for source, value in (("left", 10), ("right", 20), ("left", 30)):
            await self.publish(source, value)
        self.assertEqual(self.buffer.metrics["dropped_oldest"], 1)
        await self.resume([20, 30])

    async def test_shared_fifo_waits_for_space_freed_by_another_source(self):
        await self.start({"capacity": 1, "overflow": "wait", "wait_timeout_ms": 1000})
        await self.publish("left", 10)
        await self.publish("right", 20)
        self.assertGreater(self.buffer.metrics["refused"], 0)
        self.assertEqual(self.buffer.store.size(), 1)
        await self.resume([10, 20])
        await self.until(lambda: self.server.engines["right"].metrics["acknowledged"] == 1)
