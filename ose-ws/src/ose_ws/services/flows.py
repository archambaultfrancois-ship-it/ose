"""Leased, shared latest-message previews. No files or business subscriptions."""
import asyncio
from concurrent.futures import ThreadPoolExecutor
import math
import time
import uuid

from ose.transport.secure import connect, loopback
from ose.execution.identity import initialize_thread, thread_name
from ose_ws.schema.adapter import SchemaAdapter
from ose_ws.util import APIError, dumps, require, utc


def preview(adapter, envelope, offsets):
    """Decode once, then project bounded trees without converting entire arrays."""
    api = adapter.values.api
    identity, payload = api.unpack_envelope(envelope)
    if identity != api.catalogue_id(adapter.catalogue):
        raise ValueError("Source SDL catalogue changed; restart Live.")
    value = api.decode_dynamic(adapter.values.context, payload)
    if value.type_name != adapter.root:
        raise ValueError("Unexpected source SDL root.")
    result = {}
    for offset in offsets:
        remaining, budget = 128, 48000

        def node(name, kind, **data):
            nonlocal remaining, budget
            item = {"name": name[:256], "kind": kind, **data}
            size = len(dumps(item).encode()) + 64
            if remaining <= 0 or size > budget:
                return None
            remaining -= 1
            budget -= size
            return item

        def visit(name, item, type_name, depth=0):
            if depth >= 16:
                return node(name, "truncated", type=type_name[:256], truncated=True)
            if item is None:
                return node(name, "absent", type=type_name[:256])
            if isinstance(item, (list, tuple)):
                out = node(name, "array", type=type_name[:256], length=len(item), offset=min(offset, len(item)), children=[])
                if out is not None:
                    for index in range(min(offset, len(item)), min(offset + 16, len(item))):
                        child = visit(f"[{index}]", item[index], type_name, depth + 1)
                        if child is None:
                            break
                        out["children"].append(child)
                    out["truncated"] = len(out["children"]) < len(item)
                return out
            if isinstance(item, api.SdlDynamicMessage):
                out = node(name, "message", type=item.type_name[:256], children=[])
                if out is not None:
                    for field in adapter.values.messages[item.type_name]:
                        child = visit(field["name"], item.fields.get(field["name"]), field["type"], depth + 1)
                        if child is None:
                            out["truncated"] = True
                            break
                        out["children"].append(child)
                return out
            if isinstance(item, (api.Complex32, api.Complex64)):
                out = node(name, "complex", type=type_name[:256], children=[])
                if out is not None:
                    for part in ("real", "imag"):
                        child = visit(part, getattr(item, part), "fl64", depth + 1)
                        if child is None:
                            out["truncated"] = True
                            break
                        out["children"].append(child)
                return out
            if isinstance(item, api.SdlDynamicEnum):
                item = item.name
            elif type_name in ("int64", "uint64"):
                item = str(item)
            elif isinstance(item, float) and not math.isfinite(item):
                item = "NaN" if math.isnan(item) else ("+Infinity" if item > 0 else "-Infinity")
            truncated = isinstance(item, str) and len(item) > 1024
            if truncated:
                item = item[:1024] + "…"
            return node(name, "value", type=type_name[:256], value=item, **({"truncated": True} if truncated else {}))

        tree = visit(adapter.root, value, adapter.root)
        require(tree is not None and len(dumps(tree).encode()) < 60 * 1024,
                "SDL preview exceeds the display budget.", 413)
        result[offset] = tree
    return result


class FlowSource:
    def __init__(self, manager, key, topic, component_generation):
        self.manager, self.key, self.topic = manager, key, topic
        self.component_generation = component_generation
        self.component = None
        self.client = self.task = self.token = None
        self.adapter = None
        self.generation = None
        self.error = None
        self.sample = None
        self.previews = {}
        self.envelope = None
        self.last_mono = None
        self.version = 0
        self.decode_ms = None
        self.sampled_out = 0

    async def open(self, subscription):
        manager = self.manager
        origin = await manager.connection.connect()
        self.client = origin
        try:
            desc, catalogue = await origin.request("subscription_schema", subscription=subscription, timeout=3)
            require(desc.get("epoch") == self.key[0] and desc["from"] == self.topic,
                    "The application changed; refresh its status.", 409, "APPLICATION_CHANGED")
            point, point_catalogue = await origin.request("recording_describe", topic=self.topic, timeout=3)
            require(point.get("available") and point.get("generation"), "The source is unavailable.", 503, "SOURCE_UNAVAILABLE")
            require(catalogue == point_catalogue and desc["root"] == point["root"], "The source contract changed.", 409)
            self.generation = point["generation"]
            endpoint = point.get("endpoint")
            if endpoint:
                require(manager.connection.context is not None or loopback(endpoint["host"]),
                        "Insecure Live requires loopback producer endpoints.", 503)
                self.client = await connect(endpoint["host"], endpoint["port"], 1024 * 1024,
                    manager.connection.context, endpoint["identity"], timeout=3)
                await origin.close()
                current, current_catalogue = await self.client.request("recording_describe", topic=self.topic, timeout=3)
                require(current.get("available") and current.get("generation") == self.generation and current_catalogue == catalogue,
                        "The source changed; restart Live.", 409, "SOURCE_CHANGED")
            self.adapter = await manager.work(SchemaAdapter, catalogue.decode(), desc["root"])
            result, _ = await self.client.request("recording_open", points={self.topic: self.generation},
                capacity=1, byte_budget=manager.settings["max_payload_bytes"], label="web-live", timeout=3)
            self.token = result["session"]
        except BaseException:
            await self.close_client()
            # If connecting to a worker failed, origin still needs closing.
            if origin is not self.client:
                await origin.close()
            raise

    def check(self):
        collector = self.manager.collector
        require(collector.error is None and collector.snapshot.get("epoch") == self.key[0],
                "The OSE application is unavailable or changed; restart Live.", 503, "SOURCE_CHANGED")
        if self.component:
            desc = collector.snapshot.get("observation", {}).get("components", {}).get(self.component, {})
            require(desc.get("generation") == self.component_generation,
                    "The source generation changed; restart Live.", 409, "SOURCE_CHANGED")

    async def run(self):
        try:
            while True:
                started = time.monotonic()
                self.check()
                current, _ = await self.client.request("recording_state", topic=self.topic, timeout=3)
                require(current.get("available") and current.get("generation") == self.generation,
                        "The source is unavailable or changed; restart Live.", 503, "SOURCE_CHANGED")
                result, envelope = await self.client.request("recording_next", session=self.token, timeout=3)
                require(not result.get("stopped"), result.get("capture_error") or "The source observation stopped.", 503)
                require(not result.get("counts", {}).get("oversize"), "Source messages exceed the preview frame limit.", 413)
                require(result.get("record") or result.get("counts", {}).get("dropped", 0) == self.sampled_out,
                        "Source messages exceed the preview byte budget.", 413)
                # A message larger than the reserved tap budget never reaches this reader.
                if result.get("record"):
                    record = result["record"]
                    require(record["generation"] == self.generation, "Source generation changed; restart Live.", 409)
                    require(len(envelope) <= self.manager.settings["max_payload_bytes"], "Source message is too large.", 413)
                    self.envelope = bytes(envelope)
                    self.last_mono = time.monotonic()
                    self.sample = {"epoch": self.key[0], "generation": self.component_generation or self.generation,
                        "schema_digest": self.adapter.digest, "source_sequence": str(record["sequence"]), "observed_at": utc()}
                    self.previews = {}
                    self.sampled_out = result.get("counts", {}).get("dropped", 0)
                offsets = {watch["offset"] for watch in self.manager.watches.values() if watch["source"] is self}
                if self.envelope is not None and offsets - self.previews.keys():
                    decoding = time.monotonic()
                    projections = await self.manager.work(preview, self.adapter, self.envelope, offsets)
                    self.check()
                    self.decode_ms = (time.monotonic() - decoding) * 1000
                    self.previews = projections
                    self.version += 1
                interval = min((w["interval_ms"] / 1000 for w in self.manager.watches.values() if w["source"] is self), default=.5)
                await asyncio.sleep(max(.01, interval - (time.monotonic() - started)))
        except asyncio.CancelledError:
            raise
        except Exception as error:
            self.error = str(error)[:500] or type(error).__name__
            self.version += 1
        finally:
            await self.close_client()

    async def close_client(self):
        if self.client:
            await self.client.close()  # OSE releases taps on connection close.
            self.client = None

    async def close(self):
        if self.task:
            self.task.cancel()
            await asyncio.gather(self.task, return_exceptions=True)
        await self.close_client()


class FlowManager:
    def __init__(self, config, connection, collector, auth):
        self.settings, self.connection, self.collector, self.auth = config["flows"], connection, collector, auth
        self.max_watches = config["auth"]["max_sessions"] * self.settings["per_session"]
        self.watches, self.sources = {}, {}
        self.lock = asyncio.Lock()
        label = thread_name("live")
        self.executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix=label,
                                           initializer=initialize_thread, initargs=(label,))
        self.task = None

    async def work(self, function, *args):
        return await asyncio.get_running_loop().run_in_executor(self.executor, function, *args)

    async def start(self):
        self.task = asyncio.create_task(self.maintain())

    def allowed(self, session, topic):
        session.check("flows.read", topic)

    def get(self, session, identity):
        watch = self.watches.get(identity)
        require(watch is not None and watch["owner"] == session.id and watch["until"] > time.monotonic(),
                "Live watch expired or not found.", 404, "WATCH_EXPIRED")
        self.allowed(session, watch["topic"])
        return watch

    async def watch(self, session, data):
        require(isinstance(data.get("subscription"), str) and isinstance(data.get("epoch"), str), "Subscription and epoch are required.")
        interval = data.get("interval_ms", 500)
        offset = data.get("array_offset", 0)
        require(type(interval) is int and 250 <= interval <= 1000, "Live cadence must be between 250 and 1000 ms.")
        require(type(offset) is int and 0 <= offset <= 1000000, "Invalid array preview offset.")
        async with self.lock:
            await self.prune()
            if data.get("id"):
                watch = self.get(session, data["id"])
                require((data["epoch"], data["subscription"]) == (watch["epoch"], watch["subscription"]), "Live watch selection changed.", 409)
                watch.update(until=time.monotonic() + self.settings["lease_s"], interval_ms=interval, offset=offset)
                return {"id": data["id"], "lease_s": self.settings["lease_s"]}
            require(self.collector.error is None, "The OSE application is unavailable.", 503, "SOURCE_UNAVAILABLE")
            require(self.collector.snapshot.get("epoch") == data["epoch"], "The application changed; refresh its status.", 409, "APPLICATION_CHANGED")
            observation = self.collector.snapshot.get("observation", {})
            sub = observation.get("subscriptions", {}).get(data["subscription"])
            require(sub is not None, "Subscription not found.", 404, "NOT_FOUND")
            topic = sub["from"]
            self.allowed(session, topic)
            require(len(self.watches) < self.max_watches and sum(w["owner"] == session.id for w in self.watches.values()) < self.settings["per_session"],
                    "Live watch quota reached.", 429, "FLOW_QUOTA")
            key = (data["epoch"], topic)
            source = self.sources.get(key)
            require(source is None or source.error is None, "Previous source observation failed; stop Live before retrying.", 409)
            if source is None:
                require(len(self.sources) < self.settings["max_sources"], "Live source quota reached.", 429, "FLOW_QUOTA")
                component = topic.split(".")[0]
                external = "from_topic" in sub or topic in observation.get("external_topics", {}) or component not in observation.get("components", {})
                generation = observation.get("components", {}).get(component, {}).get("generation") if not external else None
                source = FlowSource(self, key, topic, generation)
                source.component = None if external else component
                await asyncio.wait_for(source.open(data["subscription"]), 10)
                # No stale graph can acquire a source after a slow connection attempt.
                try:
                    source.check()
                except BaseException:
                    await source.close()
                    raise
                self.sources[key] = source
            identity = uuid.uuid4().hex
            self.watches[identity] = {"owner": session.id, "topic": topic, "subscription": data["subscription"],
                "epoch": data["epoch"], "source": source, "until": time.monotonic() + self.settings["lease_s"],
                "interval_ms": interval, "offset": offset}
            if source.task is None:
                source.task = asyncio.create_task(source.run())
            return {"id": identity, "lease_s": self.settings["lease_s"]}

    def snapshot(self, session, identity):
        watch = self.get(session, identity)
        source = watch["source"]
        error = source.error
        try:
            source.check()
        except APIError as failure:
            error = failure.message
        tree = source.previews.get(watch["offset"])
        sample = {**source.sample, "value": tree} if source.sample and tree else None
        return {"watch_id": identity, "topic": watch["topic"], "epoch": watch["epoch"],
            "status": "unavailable" if error else ("live" if sample else "waiting"), "error": error,
            "sample": sample, "age_ms": (time.monotonic() - source.last_mono) * 1000 if sample else None,
            "version": str(source.version), "sampled_out": str(source.sampled_out), "decode_ms": source.decode_ms}

    async def remove(self, session, identity):
        async with self.lock:
            watch = self.watches.get(identity)
            require(watch is None or watch["owner"] == session.id, "Live watch not found.", 404)
            self.watches.pop(identity, None)
            await self.close_unused()

    async def release_session(self, identity):
        async with self.lock:
            self.watches = {key: w for key, w in self.watches.items() if w["owner"] != identity}
            await self.close_unused()

    async def close_unused(self):
        used = {w["source"] for w in self.watches.values()}
        for key, source in list(self.sources.items()):
            if source not in used:
                self.sources.pop(key)
                await source.close()

    async def prune(self):
        for key, watch in list(self.watches.items()):
            session = self.auth.sessions.get(watch["owner"])
            if (watch["until"] <= time.monotonic() or session is None or
                    time.time() >= min(session.touched + 1800, session.created + 28800) or not session.allows("flows.read", watch["topic"])):
                self.watches.pop(key)
        await self.close_unused()

    async def maintain(self):
        while True:
            await asyncio.sleep(.5)
            async with self.lock:
                await self.prune()

    async def close(self):
        if self.task:
            self.task.cancel()
            await asyncio.gather(self.task, return_exceptions=True)
        async with self.lock:
            self.watches.clear()
            await self.close_unused()
        self.executor.shutdown(wait=True, cancel_futures=True)
