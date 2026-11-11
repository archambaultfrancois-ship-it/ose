"""Component-owned diagnostic points, separate from application publications."""
import asyncio
import re
import time
from collections import Counter
from ose.transport.protocol import encode

NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)*\Z")
MAX_POINTS = 128
MAX_TICK_BYTES = 256 * 1024


def validate_name(name):
    if not isinstance(name, str) or len(name.encode()) > 200 or not NAME.fullmatch(name):
        raise ValueError("recording name must contain dot-separated ASCII identifiers (max 200 bytes)")
    return name


def declaration(body):
    from ose.execution.ipc import unpack_port
    name, rest = unpack_port(body)
    validate_name(name)
    root, description, catalogue = rest.decode().split("\0", 2)
    if not root or "\0" in catalogue or len(description.encode()) > 1024:
        raise ValueError("invalid recording declaration")
    return name, dict(root=root, description=description, catalogue=catalogue)


class ComponentRecording:
    """CH/2 recording service; emitting never waits for network or disk."""
    def __init__(self, codec, *, declare=None, active=None, emit=None):
        self.codec, self.on_declare, self.on_active, self.on_emit = codec, declare, active, emit
        self.points = {}

    async def declare(self, body):
        name, desc = declaration(body)
        if name in self.points or len(self.points) >= MAX_POINTS:
            raise ValueError("duplicate recording point or too many points")
        if self.codec is not None:
            prepared = self.codec.api.prepare(desc["catalogue"])
            if desc["root"] not in self.codec.api.message_names(prepared):
                raise ValueError("unknown recording message root")
        if self.on_declare:
            await self.on_declare(name, desc)
        self.points[name] = desc

    def activation(self):
        active = set(self.on_active() if self.on_active else ())
        return "\0".join(sorted(active & self.points.keys())).encode()

    def emit(self, name, payload):
        desc = self.points[name]
        if not payload:
            raise ValueError("empty recording payload")
        if self.on_emit:
            self.on_emit(name, desc, payload)


class RemoteRecording:
    """Bounded adapter queue and versioned activation watch for the local broker."""
    def __init__(self, codec, request, *, max_frame=1024 * 1024):
        self.request, self.max_frame = request, max_frame
        self.active = set()
        self.active_inputs = set()
        self.queue = asyncio.Queue(maxsize=64)
        self.bytes = 0
        self.counts = Counter()
        self.closed = False
        self.endpoint = ComponentRecording(codec, declare=self.declare,
            active=lambda: self.active, emit=self.emit)

    async def declare(self, name, desc):
        await self.request("recording_declare", desc["catalogue"].encode(),
            name=name, root=desc["root"], description=desc["description"])

    def emit(self, name, desc, payload):
        self.enqueue(dict(op="recording_emit", name=name, publish_time_ns=time.time_ns()), payload)

    def emit_input(self, port, desc, envelope, tick_index, *, app_id=None):
        from ose.delivery.messages import validate_app_id
        extra = {"app_id": validate_app_id(app_id)} if app_id is not None else {}
        self.enqueue(dict(op="recording_input", port=port, sequence=tick_index,
                          publish_time_ns=time.time_ns(), **extra), envelope)

    def enqueue(self, header, payload):
        if self.queue.full() or self.bytes + len(payload) > 1024 * 1024:
            self.counts["dropped"] += 1
            return
        try:
            size = len(encode({**header, "request": 2**63 - 1}, limit=self.max_frame))
            if size + len(payload) > self.max_frame:
                raise ValueError("recording frame too large")
        except ValueError:
            self.counts["oversize"] += 1
            return
        self.queue.put_nowait((header, payload))
        self.bytes += len(payload)

    async def poll(self):
        version = None
        self.active.clear()
        self.active_inputs.clear()
        while not self.closed:
            result, _ = await self.request("recording_component_state", watch=True, after=version)
            if self.closed:
                return
            next_version = result.get('version')
            if next_version is None:
                # Older brokers ignore watch/after and return an immediate snapshot.
                self.active = set(result['active'])
                self.active_inputs = set(result.get('inputs', ()))
                await asyncio.sleep(.05)
                continue
            if type(next_version) is not int or next_version < 0:
                raise ValueError('invalid recording activation version')
            if version is not None and next_version < version:
                raise ValueError('recording activation generation changed')
            version = next_version
            self.active = set(result["active"])
            self.active_inputs = set(result.get("inputs", ()))

    def close(self):
        self.closed = True
        self.active.clear()
        self.active_inputs.clear()

    async def send(self):
        while not self.closed:
            header, payload = await self.queue.get()
            try:
                if self.closed:
                    return
                await self.request(header["op"], payload, **{key: value for key, value in header.items() if key != "op"})
            finally:
                self.bytes -= len(payload)
