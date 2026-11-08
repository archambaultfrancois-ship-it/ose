"""Bounded buffers; FIFO order is admission order, never publication order."""
from collections import Counter
import time
from ose.delivery.policies import reception_policies
from ose.delivery.stores import MemoryStore


def input_buffers(subscriptions, on_change=None, clock=time.monotonic):
    """One shared inbox per qualified input; sender outboxes remain per link."""
    result = {}
    for target, policy in reception_policies(subscriptions).items():
        store = MemoryStore(policy.capacity)
        store.on_change = on_change
        result[target] = Buffer(policy, store, clock)
    return result


class Buffer:
    def __init__(self, policy, store, clock=time.monotonic):
        self.policy, self.store, self.clock = policy, store, clock
        if store.size() > policy.capacity:
            raise ValueError("RAM queue already exceeds policy capacity")
        store.capacity = policy.capacity
        self.metrics = Counter()
        self.reserved = set()

    def admit(self, record, deduplicate=True):
        if deduplicate and self.store.contains(record["id"]):
            self.metrics["duplicates"] += 1
            return "duplicate"
        if self.policy.buffer == "latest":
            # Source streams have independent sequences (and generations in
            # distributed runs). A different stream must not erase this history.
            source = record.get("topic", "")
            last_epoch, last_sequence = self.store.meta.get(source, (None, -1))
            if record["epoch"] == last_epoch and record["sequence"] <= last_sequence:
                self.metrics["stale"] += 1
                return "stale"
        if self.store.size() >= self.policy.capacity:
            if self.policy.overflow in ("reject_new", "wait"):
                self.metrics["refused"] += 1
                return "full"
            victim = self.store.first(self.reserved)
            if victim is None:
                self.metrics["refused"] += 1
                return "full"
            self.store.remove(victim["id"])
            self.metrics["replaced" if self.policy.buffer == "latest" else "dropped_oldest"] += 1
        record = dict(record, admitted_at=self.clock())
        if self.policy.buffer == "latest":
            self.store.meta[source] = (record["epoch"], record["sequence"])
        self.store.put(record)
        if deduplicate:
            self.store.remember(record["id"])
        self.metrics["accepted"] += 1
        return "accepted"

    def take(self):
        return self.store.first()

    def consumed(self, identity):
        self.reserved.discard(identity)
        self.store.remove(identity)

    def snapshot(self):
        first = self.store.first()
        return {**self.metrics, "depth": self.store.size(), "age_ms": (self.clock() - first["admitted_at"]) * 1000 if first else 0}
