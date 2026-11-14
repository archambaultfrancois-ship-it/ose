"""Deterministic direct admission, with independent bounded virtual waits."""
from collections import Counter
from dataclasses import dataclass
import hashlib
from ose.delivery.buffers import Buffer, input_buffers
from ose.delivery.messages import validate_app_id
from ose.delivery.policies import SubscriptionPolicy
from ose.delivery.stores import MemoryStore
from ose.execution.ipc import MAX_SIZE


@dataclass
class Publication:
    record: dict
    pending: dict


class Router:
    def __init__(self, plan, codec, clock, trace):
        self.codec, self.clock, self.trace = codec, clock, trace
        self.subscriptions = {s["id"]: s for s in sorted(plan["subscriptions"], key=lambda s: s["id"])}
        self.buffers = {name: {} for name in plan["components"]}
        self.by_topic, self.by_id = {}, {}
        self.topics = dict(plan.get("external_topics", {}))
        self.capture = None
        self.sequence = 0
        self.topic_counts_by_component = Counter()
        self.component_topics = {f'{name}.{port}': name for name, node in plan['components'].items()
                                 for port in node['outputs']}
        self.external_deliveries = Counter()
        self.schedulers = {}
        inboxes = input_buffers(self.subscriptions.values(), clock=clock.seconds)
        for name, node in plan["components"].items():
            self.topics.update({f"{name}.{port}": desc for port, desc in node["outputs"].items()})
        for identity, sub in self.subscriptions.items():
            name, port = sub["to"].split(".")
            buffer = inboxes[sub["to"]]
            self.buffers[name][port] = self.by_id[identity] = buffer
            self.by_topic.setdefault(sub["from"], []).append(identity)
            publisher, consumer = self.topics[sub["from"]], plan["components"][name]["inputs"][port]
            codec.compare(publisher["catalogue"], consumer["catalogue"], consumer["root"])
        for name, node in plan["components"].items():
            for port, desc in node["inputs"].items():
                if desc.get("kind") != "timer" and port not in self.buffers[name]:
                    self.buffers[name][port] = Buffer(SubscriptionPolicy(), MemoryStore(), clock.seconds)

    def publish(self, topic, body, identity, *, app_id=None):
        if app_id is not None:
            validate_app_id(app_id)
        desc = self.topics[topic]
        self.codec.validate_only(desc["catalogue"], desc["root"], body)
        if self.capture is not None:
            self.capture.emit(topic, body, app_id=app_id)
        self.sequence += 1
        if topic in self.component_topics:
            self.topic_counts_by_component[self.component_topics[topic]] += 1
        record = dict(id=identity, epoch="simulated", sequence=self.sequence, topic=topic,
                      body=body, payload=memoryview(body)[self.codec.api.ENVELOPE_HEADER_SIZE:], catalogue=desc["catalogue"])
        if app_id is not None:
            record["app_id"] = app_id
        self.trace.emit("publication", id=identity, topic=topic, sha256=hashlib.sha256(body).hexdigest(),
                        **({"app_id": app_id} if app_id is not None else {}))
        pending = {sub: self.clock.time_ns + self.by_id[sub].policy.wait_timeout_ms * 1_000_000
                   for sub in self.by_topic.get(topic, ())}
        return Publication(record, pending)

    def advance(self, publication):
        changed = False
        for identity, deadline in list(publication.pending.items()):
            buffer, record = self.by_id[identity], publication.record
            name, port = self.subscriptions[identity]["to"].split(".")
            size = 7 + len(port.encode()) + len(record["catalogue"].encode()) + len(record["payload"])
            from ose.execution.input_ipc import check_record_size
            try:
                check_record_size(port, record)
            except ValueError:
                size = MAX_SIZE + 1
            inactive = self.schedulers[name].suspended_at is not None
            if size <= MAX_SIZE and not inactive:
                self.schedulers[name].receive(port, record, self.clock.time_ns)
            if inactive:
                result = 'disabled'
            elif size > MAX_SIZE:
                result = "oversize"
            elif (buffer.policy.overflow == "wait" and buffer.store.size() >= buffer.policy.capacity
                  and self.clock.time_ns < deadline):
                continue
            else:
                result = self.schedulers[name].admit(port, record, self.clock.time_ns)
            self.trace.emit("admission", id=record["id"], subscription=identity, result=result)
            if record["id"].startswith("scenario:"):
                self.external_deliveries[result] += 1
            del publication.pending[identity]
            changed = True
        return changed

    def close(self):
        for ports in self.buffers.values():
            for buffer in ports.values():
                buffer.store.close()
