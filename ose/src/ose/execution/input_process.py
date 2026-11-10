"""Typed Python callbacks over CH/2 with parent-owned, deferred inbox access."""
from ose.codec.runtime import TypedCodec
from ose.codec.sdl2 import SDL2
from ose.execution.context import ComponentContext, _load_handler, _callback, _call
from ose.execution.component_io import SessionIO
from ose.execution import input_ipc as wire
from ose.execution.input_callbacks import ReceivedInput
from ose.modeling.activation import component_inputs
from ose.timing import seconds_ns


class InputSession(SessionIO):
    def __init__(self, reader=None, writer=None, *, auto_ready=True):
        super().__init__(reader, writer)
        if auto_ready:
            self.ready()

    def ready(self):
        if not self.is_ready:
            self.send("R", wire.READY)
            self.is_ready = True

    def input_request(self, operation, port, **arguments):
        self.send("Q", wire.request_data(operation, port, **arguments))
        op, body = self.receive()
        if op != "A" or not body or body[0] not in (0, 1):
            raise ValueError("invalid CH/2 input reply")
        if body[0] == 0:
            raise ValueError(body[1:].decode() or "input operation failed")
        result = body[1:]
        if operation in ("get", "pull"):
            return wire.unpack_record(result)
        if operation == "remove":
            if result not in (b"\0", b"\x01"):
                raise ValueError("invalid CH/2 removal reply")
            return result == b"\x01"
        if result:
            raise ValueError("unexpected CH/2 timer reply body")

    def activations(self):
        while True:
            op, data = self.receive()
            if op == "T":
                reference = wire.unpack_activation(data)
                if reference.index != self.tick_index + 1 or reference.time_ns < self.time_ns:
                    raise ValueError("non-monotonic CH/2 activation")
                self.time_ns, self.tick_index = reference.time_ns, reference.index
                self._in_callback, self._recording_bytes = True, 0
                try:
                    yield reference
                finally:
                    self._in_callback = False
                self.send("F")
            elif op == "U":
                from ose.execution.ipc import unpack_port
                alias, catalogue, payload = unpack_port(data, True)
                binding = self.config.get("parameter_bindings", {}).get(alias)
                if binding is None or "read" not in binding["access"] or catalogue != binding["catalogue"] or not payload:
                    raise ValueError("invalid shared parameter update")
                self.parameter_values[alias] = (catalogue, payload)
            elif op == "E":
                active = data.decode().split("\0") if data else []
                if len(active) != len(set(active)) or set(active) - self.recording_points.keys():
                    raise ValueError("unknown or duplicate active recording point")
                self.recording_points.update((name, name in active) for name in self.recording_points)
            elif op == "X" and not data:
                return
            else:
                raise ValueError("unexpected CH/2 adapter event")


class RemoteInbox:
    def __init__(self, context, port):
        self.context, self.port, self.index = context, port, context.tick_index

    def _request(self, operation, *, id=None, handle=None, source=None):
        if not self.context._active or self.context.tick_index != self.index:
            raise RuntimeError("input access requires its active callback")
        return self.context.session.input_request(operation, self.port, app_id=id, handle=handle, source=source)

    def get(self, **selectors):
        record = self._request("get", **selectors)
        return self.context._decode_record(self.port, record) if record is not None else None

    def pull(self, **selectors):
        record = self._request("pull", **selectors)
        return self.context._decode_record(self.port, record) if record is not None else None

    def remove(self, **selectors):
        return self._request("remove", **selectors)


class InputCallbackContext(ComponentContext):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._declarations = component_inputs(self.config, compiled=True)
        self._input_codecs = {port: TypedCodec(cls) for port, cls in self.input_types.items()}
        self._origin = None

    @property
    def activation_index(self):
        return self.tick_index

    def publish(self, port, message, *, id=None):
        if not self._active or self._origin is None:
            raise RuntimeError("publication requires an active callback")
        if port not in self._declarations[self._origin.port].outputs[self._origin.event]:
            raise ValueError(f"{self._origin.port}:{self._origin.event} cannot activate output {port}")
        if port not in self.output_types or not isinstance(message, self.output_types[port]):
            raise TypeError("wrong publication message type")
        payload = self.codec.api.encode(self._encoders[port], message)
        self.session.send("P", wire.publication_data(port, payload, id))

    def inbox(self, port):
        if not self._active:
            raise RuntimeError("input access requires an active callback")
        desc = self._declarations.get(port)
        if desc is None or desc.mode != "pull":
            raise ValueError("explicit message access requires a Pull input")
        return RemoteInbox(self, port)

    def _timer(self, operation, port, delay=None):
        if not self._active and not (self._parameter_writes and not self.session.is_ready):
            raise RuntimeError("timer control requires initialization or an active callback")
        desc = self._declarations.get(port)
        if desc is None or desc.kind != "timer":
            raise ValueError("timer control requires a timer input")
        arguments = {"delay_ns": seconds_ns(delay, positive=True) if delay is not None else None} if operation == "arm" else {}
        return self.session.input_request(operation, port, **arguments)

    def arm(self, port, *, delay=None):
        return self._timer("arm", port, delay)

    def disarm(self, port):
        return self._timer("disarm", port)

    def _decode_record(self, port, record):
        value = self._input_codecs[port].decode(record["catalogue"], record["body"])
        return ReceivedInput(port, "message", value, record["id"], record.get("app_id"), record.get("topic"),
                             due_ns=record.get("received_ns", 0))


def run_input_callbacks(*, name, handler, sdl_root, input_types, output_types,
                        context_type=InputCallbackContext, reader=None, writer=None, parameter_types=None):
    session = InputSession(reader, writer, auto_ready=False)
    if session.config.get("execution_model") != 2:
        raise ValueError("CH/2 callbacks require execution model 2")
    context = context_type(session, name, input_types, output_types, SDL2(sdl_root), parameter_types)
    module = _load_handler(handler)
    if hasattr(module, "on_tick"):
        raise ValueError("on_tick is removed from the v2 API; declare timer inputs")
    callbacks = {}
    for port, desc in context._declarations.items():
        if desc.mode == "push":
            callbacks[port] = _callback(module, "on_" + port, 2, required=True)
        elif hasattr(module, "on_" + port):
            raise ValueError("Pull inputs cannot declare an activating callback")
    start, stop = _callback(module, "on_start", 1), _callback(module, "on_stop", 1)
    try:
        if start:
            _call(start, context)
        context._parameter_writes = False
        session.ready()
        for reference in session.activations():
            desc = context._declarations.get(reference.port)
            if (desc is None or desc.mode != "push" or reference.event not in desc.outputs
                    or reference.event == "timeout" and not desc.timeout_ns):
                raise ValueError("activation does not match input declaration")
            value = (context._decode_record(reference.port, reference.record) if reference.record is not None else
                     ReceivedInput(reference.port, reference.event, None, elapsed=reference.elapsed, due_ns=reference.due_ns))
            context._active = context._parameter_writes = True
            context._origin = reference
            try:
                _call(callbacks[reference.port], context, value)
            finally:
                context._active = context._parameter_writes = False
                context._origin = None
    finally:
        context._parameter_writes = False
        if stop:
            _call(stop, context)
