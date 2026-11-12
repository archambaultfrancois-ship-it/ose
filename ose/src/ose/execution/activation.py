"""Per-input execution semantics, independent of CH and network wire versions.

All times are logical uint64 nanoseconds. The owner serializes calls on one
event loop and supplies validated publications; this module does not decode SDL.
"""
from collections import OrderedDict
from dataclasses import dataclass
from types import MappingProxyType

from ose.modeling.activation import component_inputs
from ose.delivery.messages import validate_app_id
from ose.timing import MAX_NS, seconds_ns


@dataclass(frozen=True)
class InputReference:
    port: str
    event: str
    time_ns: int
    due_ns: int
    elapsed: int
    record: object
    allowed_outputs: frozenset[str]

    @property
    def timed_out(self):
        return self.event == "timeout"


class InputScheduler:
    """One callback at a time per component, without joint input readiness."""
    def __init__(self, node, buffers):
        self.node = node
        self.inputs = component_inputs(node, compiled=node.get("execution_model") == 2)
        expected = {port for port, desc in self.inputs.items() if desc.kind == "message"}
        if set(buffers) != expected:
            raise ValueError("inboxes must cover exactly the message inputs")
        self.buffers, self.outputs = dict(buffers), frozenset(node["outputs"])
        self.deadlines, self.last, self._receipts = {}, {}, OrderedDict()
        self.active, self._views = None, {}
        self.time_ns, self.start_ns = 0, None
        self.suspended_at = None

    def enable(self, enabled, now_ns):
        if self.active is not None:
            raise RuntimeError('suspend only at a callback boundary')
        self._time(now_ns)
        if not enabled and self.suspended_at is None:
            self.suspended_at = now_ns
            for buffer in self.buffers.values():
                for record in buffer.store.records():
                    buffer.store.remove(record['id'])
        elif enabled and self.suspended_at is not None:
            delay = now_ns - self.suspended_at
            self.deadlines = {port: self._after(deadline, delay) if deadline is not None else None
                              for port, deadline in self.deadlines.items()}
            self.suspended_at = None

    def _time(self, now_ns):
        if type(now_ns) is not int or not self.time_ns <= now_ns <= MAX_NS:
            raise ValueError("execution time must be monotonic uint64 nanoseconds")
        if self.start_ns is None:
            raise RuntimeError("input scheduler has not started")
        self.time_ns = now_ns

    @staticmethod
    def _after(now_ns, duration):
        deadline = now_ns + duration
        return deadline if deadline <= MAX_NS else None

    def start(self, now_ns=0):
        if self.start_ns is not None:
            raise RuntimeError("input scheduler already started")
        if type(now_ns) is not int or not 0 <= now_ns <= MAX_NS:
            raise ValueError("execution time must be uint64 nanoseconds")
        self.time_ns = self.start_ns = now_ns
        self.deadlines = {port: self._after(now_ns, desc.timeout_ns)
                          for port, desc in self.inputs.items()
                          if desc.timeout_ns and (desc.kind == "message" or desc.automatic)}

    def admit(self, port, record, now_ns):
        """Receive one validated publication, even when admission returns full.

        Retries do not constitute new source activity within the bounded receipt
        history. That history is separate from queue deduplication: a full first
        attempt must remain admissible when its retry eventually finds space.
        """
        if self.suspended_at is not None:
            return 'disabled'
        self.receive(port, record, now_ns)
        return self.buffers[port].admit(dict(record, received_ns=now_ns))

    def receive(self, port, record, now_ns):
        """Observe a validated new receipt independently of eventual admission."""
        if port not in self.buffers:
            raise ValueError("publication must target a message input")
        if not isinstance(record.get("id"), str) or not record["id"]:
            raise ValueError("publication handle must be a nonempty string")
        if "app_id" in record:
            validate_app_id(record["app_id"])
        self._time(now_ns)
        identity = (port, record["id"])
        if not self.buffers[port].store.contains(record["id"]) and identity not in self._receipts:
            self._receipts[identity] = None
            if len(self._receipts) > 4096:
                self._receipts.popitem(last=False)
            desc = self.inputs[port]
            if desc.kind == "message" and desc.timeout_ns:
                # A new reception also cancels any not-yet-started expiration:
                # no pending timeout object survives resetting this deadline.
                self.deadlines[port] = self._after(now_ns, desc.timeout_ns)

    def _candidates(self, now_ns):
        for port, desc in self.inputs.items():
            if desc.kind == "message" and desc.mode == "push":
                record = self.buffers[port].take()
                if record is not None and (self.buffers[port].policy.buffer == "fifo"
                                           or self.last.get(port) != record["id"]):
                    yield (record.get("received_ns", self.start_ns), port, "message", record)
            deadline = self.deadlines.get(port)
            if deadline is not None and deadline <= now_ns:
                yield (deadline, port, "timeout", None)

    def select(self, now_ns):
        self._time(now_ns)
        if self.suspended_at is not None:
            return None
        if self.active is not None:
            return None
        selected = min(self._candidates(now_ns), key=lambda item: item[:3], default=None)
        if selected is None:
            return None
        due, port, event, record = selected
        desc, elapsed = self.inputs[port], 1
        if event == "timeout":
            if desc.timer_mode != "one_shot":
                elapsed = 1 + (now_ns - due) // desc.timeout_ns
            self.deadlines[port] = (None if desc.timer_mode == "one_shot" else
                                   self._after(due, elapsed * desc.timeout_ns))
        elif self.buffers[port].policy.buffer == "fifo":
            self.buffers[port].reserved.add(record["id"])
        self._views = {name: self.buffers[name].view(deferred=True) for name, item in self.inputs.items()
                       if item.mode == "pull"}
        self.active = InputReference(port, event, now_ns, due, elapsed,
            MappingProxyType(dict(record)) if record is not None else None,
            desc.outputs[event])
        return self.active

    def inbox(self, port):
        """Explicit Pull access is only valid for the lifetime of this callback."""
        if self.active is None:
            raise RuntimeError("input access requires an active callback")
        if port not in self._views:
            raise ValueError("explicit message access requires a Pull input")
        return self._views[port]

    def authorize_output(self, port):
        if self.active is None:
            raise RuntimeError("publication requires an active callback")
        if port not in self.outputs:
            raise ValueError("unknown output")
        if port not in self.active.allowed_outputs:
            raise ValueError(f"{self.active.port}:{self.active.event} cannot activate output {port}")

    def complete(self, reference, now_ns, *, success=True):
        if self.active is not reference or reference is None:
            raise RuntimeError("callback reference is no longer active")
        if type(success) is not bool:
            raise ValueError("callback success must be boolean")
        self._time(now_ns)
        if reference.record is not None:
            port, handle = reference.port, reference.record["id"]
            self.buffers[port].reserved.discard(handle)
            if success:
                self.last[port] = handle
                if self.buffers[port].policy.buffer == "fifo":
                    self.buffers[port].consumed(handle)
        for view in self._views.values():
            view.close(commit=success)
        self._views.clear()
        self.active = None
        # Callback completion deliberately does not rearm timers or inactivity.

    def arm(self, port, now_ns, *, delay=None, delay_ns=None):
        desc = self.inputs.get(port)
        if desc is None or desc.kind != "timer":
            raise ValueError("explicit arming requires a timer input")
        if delay is not None and delay_ns is not None:
            raise ValueError("specify one timer delay")
        duration = (delay_ns if delay_ns is not None else
                    (desc.timeout_ns if delay is None else seconds_ns(delay, positive=True)))
        if type(duration) is not int or not 0 < duration <= MAX_NS:
            raise ValueError("timer delay must be positive uint64 nanoseconds")
        if type(now_ns) is not int or not 0 <= now_ns <= MAX_NS or self._after(now_ns, duration) is None:
            raise ValueError("timer deadline exceeds uint64 nanoseconds")
        self._time(now_ns)
        self.deadlines[port] = now_ns + duration

    def disarm(self, port, now_ns):
        desc = self.inputs.get(port)
        if desc is None or desc.kind != "timer":
            raise ValueError("explicit disarming requires a timer input")
        self._time(now_ns)
        self.deadlines.pop(port, None)

    def next_event(self, now_ns):
        self._time(now_ns)
        if self.suspended_at is not None:
            return None
        if self.active is not None:
            return None
        if next(self._candidates(now_ns), None) is not None:
            return now_ns
        return min((deadline for deadline in self.deadlines.values() if deadline is not None), default=None)
