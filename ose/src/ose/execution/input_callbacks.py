"""In-process Python reference host for v2 single-input callbacks.

This exercises the shared execution contract without component subprocesses.
It is not a replacement transport or an application CLI backend.
"""
from dataclasses import dataclass

from ose.codec.runtime import TypedCodec
from ose.delivery.messages import validate_app_id
from ose.execution.activation import InputScheduler
from ose.execution.context import _callback, _call


@dataclass(frozen=True)
class ReceivedInput:
    port: str
    event: str
    message: object
    handle: str | None = None
    app_id: int | None = None
    source: str | None = None
    elapsed: int = 1
    due_ns: int = 0

    @property
    def timed_out(self):
        return self.event == "timeout"


@dataclass(frozen=True)
class Publication:
    port: str
    catalogue: str
    body: bytes
    app_id: int | None


class PullInput:
    def __init__(self, context, port, view):
        self._context, self._port, self._view = context, port, view

    def get(self, *, id=None, handle=None, source=None):
        record = self._view.get(app_id=id, handle=handle, source=source)
        return self._context._decode(self._port, record) if record is not None else None

    def pull(self, *, id=None, handle=None, source=None):
        record = self._view.pull(app_id=id, handle=handle, source=source)
        return self._context._decode(self._port, record) if record is not None else None

    def remove(self, *, id=None, handle=None, source=None):
        return self._view.remove(app_id=id, handle=handle, source=source)


class InputContext:
    def __init__(self, node, scheduler, input_types, output_types, clock, emit):
        self._scheduler, self._clock, self._emit = scheduler, clock, emit
        self.parameters = node.get("parameters", {})
        self.activation_index = 0
        self._pending, self._pending_bytes = None, 0
        self._initializing, self._closed = False, False
        self._inputs = {port: TypedCodec(cls) for port, cls in input_types.items()}
        self._outputs = {port: TypedCodec(cls) for port, cls in output_types.items()}
        for direction, codecs in (("inputs", self._inputs), ("outputs", self._outputs)):
            expected = {port for port, desc in node[direction].items() if desc.get("kind") != "timer"}
            if expected != set(codecs):
                raise ValueError("typed bindings must cover exactly the message ports")
            if any(codec.root != node[direction][port]["root"] for port, codec in codecs.items()):
                raise ValueError("typed binding root differs from the component model")

    @property
    def time_ns(self):
        return self._clock()

    def now(self):
        return self.time_ns / 1_000_000_000

    def inbox(self, port):
        self._check_open()
        return PullInput(self, port, self._scheduler.inbox(port))

    def publish(self, port, message, *, id=None):
        self._check_open()
        self._scheduler.authorize_output(port)
        if id is not None:
            validate_app_id(id)
        codec = self._outputs[port]
        publication = Publication(port, codec.catalogue, codec.encode(message), id)
        self._pending_bytes += len(publication.body) + len(port.encode()) + 12
        if self._pending_bytes > 16 * 1024 * 1024:
            raise ValueError("callback outputs exceed 16 MiB")
        self._pending.append(publication)

    def arm(self, port, *, delay=None):
        self._check_timer_access()
        self._scheduler.arm(port, self.time_ns, delay=delay)

    def disarm(self, port):
        self._check_timer_access()
        self._scheduler.disarm(port, self.time_ns)

    def _check_open(self):
        if self._closed:
            raise RuntimeError("component callback host is closed")

    def _check_timer_access(self):
        self._check_open()
        if not self._initializing and self._scheduler.active is None:
            raise RuntimeError("timer control requires initialization or an active callback")

    def _decode(self, port, record):
        return ReceivedInput(port, "message", self._inputs[port].decode(record["catalogue"], record["body"]),
                             record["id"], record.get("app_id"), record.get("topic"),
                             due_ns=record.get("received_ns", 0))


class InputCallbacks:
    """Reference dispatcher with typed SDL and one origin reference per callback."""
    def __init__(self, node, buffers, handler, input_types, output_types, *, clock, emit):
        if hasattr(handler, "on_tick"):
            raise ValueError("on_tick is removed from the v2 API; declare timer inputs")
        if node.get("parameter_bindings") or node.get("recording_types"):
            raise ValueError("the in-process reference host does not implement shared parameters or recording")
        self.scheduler = InputScheduler(node, buffers)
        self.context = InputContext(node, self.scheduler, input_types, output_types, clock, emit)
        self._clock = clock
        self._callbacks = {}
        for port, desc in self.scheduler.inputs.items():
            if desc.mode == "push":
                self._callbacks[port] = _callback(handler, "on_" + port, 2, required=True)
            elif hasattr(handler, "on_" + port):
                raise ValueError("Pull inputs cannot declare an activating callback")
        start = _callback(handler, "on_start", 1)
        self._stop = _callback(handler, "on_stop", 1)
        self._closed = False
        self.scheduler.start(clock())
        self.context._initializing = True
        try:
            if start:
                _call(start, self.context)
        finally:
            self.context._initializing = False

    def admit(self, port, record):
        if self._closed:
            raise RuntimeError("component callback host is closed")
        if port not in self.context._inputs:
            raise ValueError("publication must target a message input")
        if not isinstance(record["body"], (bytes, bytearray, memoryview)):
            raise ValueError("publication body must contain binary envelope bytes")
        record = dict(record, body=bytes(record["body"]))
        # Validate every publication before treating it as source activity, even
        # if the inbox is full. Dynamic catalogue checks are not route constants.
        self.context._inputs[port].decode(record["catalogue"], record["body"])
        return self.scheduler.admit(port, record, self._clock())

    def dispatch(self):
        if self._closed:
            raise RuntimeError("component callback host is closed")
        reference = self.scheduler.select(self._clock())
        if reference is None:
            return False
        self.context.activation_index += 1
        self.context._pending, self.context._pending_bytes = [], 0
        try:
            value = (self.context._decode(reference.port, reference.record) if reference.record is not None else
                     ReceivedInput(reference.port, reference.event, None, elapsed=reference.elapsed, due_ns=reference.due_ns))
            _call(self._callbacks[reference.port], self.context, value)
        except BaseException:
            self.context._pending = None
            self.scheduler.complete(reference, self._clock(), success=False)
            raise
        publications, self.context._pending = self.context._pending, None
        self.scheduler.complete(reference, self._clock())
        for publication in publications:
            self.context._emit(publication)
        return True

    def close(self):
        if not self._closed:
            if self.scheduler.active is not None:
                raise RuntimeError("cannot close a component during its callback")
            self._closed = True
            self.context._closed = True
            if self._stop:
                _call(self._stop, self.context)
