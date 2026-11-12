"""Bounded buffers; FIFO order is admission order, never publication order."""
from collections import Counter
import time
from ose.delivery.policies import reception_policies
from ose.delivery.messages import validate_app_id
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
        if "app_id" in record:
            validate_app_id(record["app_id"])
        if deduplicate and self.store.contains(record["id"]):
            self.metrics["duplicates"] += 1
            return "duplicate"
        if self.policy.buffer == "latest":
            # Source streams have independent sequences (and generations in
            # distributed runs). A different stream must not erase this history.
            source = record.get("stream", record.get("topic", ""))
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
            if 'stream' in record and len(self.store.meta) > max(4096, self.store.seen_limit):
                self.store.meta.pop(next(iter(self.store.meta)))
        self.store.put(record)
        if deduplicate:
            self.store.remember(record["id"])
        self.metrics["accepted"] += 1
        return "accepted"

    def take(self):
        return self.store.first()

    def _select(self, *, app_id=None, handle=None, source=None, eligible=None, excluded=()):
        if app_id is not None:
            validate_app_id(app_id)
        if handle is not None and (not isinstance(handle, str) or not handle):
            raise ValueError("message handle must be a nonempty string")
        if source is not None and (not isinstance(source, str) or not source):
            raise ValueError("message source must be a nonempty topic string")
        candidates = (self.store.get(handle),) if handle is not None else self.store.items.values()
        return next((record for record in candidates
                     if record is not None and record["id"] not in self.reserved
                     and record["id"] not in excluded
                     and (eligible is None or record["id"] in eligible)
                     and (app_id is None or record.get("app_id") == app_id)
                     and (source is None or record.get("topic") == source)), None)

    def get(self, *, app_id=None, handle=None, source=None):
        """Read the oldest matching unreserved record without consuming/reserving.

        The returned metadata is a shallow copy; immutable body bytes are shared.
        A later eviction or consumption can make the returned handle absent.
        """
        record = self._select(app_id=app_id, handle=handle, source=source)
        return dict(record) if record is not None else None

    def view(self, *, deferred=False):
        """Capture eligible handles without reserving messages or copying bodies."""
        return InboxView(self, deferred=deferred)

    def pull(self, *, app_id=None, handle=None, source=None):
        """Read and immediately consume; absence returns None without waiting."""
        record = self._select(app_id=app_id, handle=handle, source=source)
        if record is None:
            return None
        result = dict(record)
        self.consumed(record["id"])
        return result

    def remove(self, *, app_id=None, handle=None, source=None):
        """Immediately consume without reading; return whether a record matched."""
        record = self._select(app_id=app_id, handle=handle, source=source)
        if record is None:
            return False
        self.consumed(record["id"])
        return True

    def consumed(self, identity):
        self.reserved.discard(identity)
        self.store.remove(identity)

    def snapshot(self):
        first = self.store.first()
        return {**self.metrics, "depth": self.store.size(), "age_ms": (self.clock() - first["admitted_at"]) * 1000 if first else 0}


class InboxView:
    """Nonblocking access to surviving messages present at activation start."""
    def __init__(self, buffer, *, deferred=False):
        self._buffer = buffer
        self._eligible = frozenset(buffer.store.items)
        self._deferred, self._removed = deferred, {}

    def close(self, *, commit=False):
        if commit:
            for handle in self._removed:
                if self._buffer.store.has_pending(handle):
                    self._buffer.consumed(handle)
        self._removed.clear()
        self._eligible = None

    def _select(self, selectors):
        if self._eligible is None:
            raise RuntimeError("input access requires an active callback")
        return self._buffer._select(**selectors, eligible=self._eligible, excluded=self._removed)

    def get(self, *, app_id=None, handle=None, source=None):
        record = self._select(dict(app_id=app_id, handle=handle, source=source))
        return dict(record) if record is not None else None

    def pull(self, *, app_id=None, handle=None, source=None):
        record = self.get(app_id=app_id, handle=handle, source=source)
        if record is not None:
            self._consume(record["id"])
        return record

    def remove(self, *, app_id=None, handle=None, source=None):
        record = self._select(dict(app_id=app_id, handle=handle, source=source))
        if record is None:
            return False
        self._consume(record["id"])
        return True

    def _consume(self, handle):
        if self._deferred:
            self._removed[handle] = None
        else:
            self._buffer.consumed(handle)
