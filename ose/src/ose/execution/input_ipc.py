"""CH/2 binary input references, publication IDs and synchronous inbox requests.

The outer frame uses a u32 length and opcode; initialization uses JSON. Message
records carry their full dynamic catalogue and unchanged S2PS/2 envelope.
"""
from dataclasses import dataclass
import struct

from ose.delivery.messages import validate_app_id
from ose.execution.ipc import MAX_SIZE, frame
from ose.timing import MAX_NS

READY = b"\x02"
EVENTS = {"message": 1, "timeout": 2}
OPERATIONS = {"get": 1, "pull": 2, "remove": 3, "arm": 4, "disarm": 5}


def uint64(value):
    if type(value) is not int or not 0 <= value <= MAX_NS:
        raise ValueError("expected uint64")
    return struct.pack(">Q", value)


def text(value, *, wide=False):
    if not isinstance(value, str) or not value:
        raise ValueError("CH/2 text must be nonempty")
    data = value.encode()
    if len(data) > (MAX_SIZE if wide else 65535):
        raise ValueError("CH/2 text exceeds size limit")
    return struct.pack(">I" if wide else ">H", len(data)) + data


def optional_id(value):
    return b"\0" if value is None else b"\x01" + uint64(validate_app_id(value))


class Reader:
    def __init__(self, data):
        if len(data) > MAX_SIZE:
            raise ValueError("CH/2 body exceeds size limit")
        self.data, self.offset = data, 0

    def take(self, count):
        if count > len(self.data) - self.offset:
            raise ValueError("truncated CH/2 body")
        start, self.offset = self.offset, self.offset + count
        return self.data[start:self.offset]

    def number(self, format):
        return struct.unpack(format, self.take(struct.calcsize(format)))[0]

    def string(self, *, wide=False):
        size = self.number(">I" if wide else ">H")
        if not size or size > (MAX_SIZE if wide else 65535):
            raise ValueError("invalid CH/2 text length")
        return self.take(size).decode()

    def id(self):
        flag = self.number(">B")
        if flag not in (0, 1):
            raise ValueError("invalid CH/2 optional ID flag")
        return self.number(">Q") if flag else None

    def rest(self):
        return bytes(self.take(len(self.data) - self.offset))

    def end(self):
        if self.offset != len(self.data):
            raise ValueError("trailing CH/2 data")


def record_data(record):
    if record is None:
        return b"\0"
    body = bytes(record["body"])
    if not body:
        raise ValueError("empty CH/2 message envelope")
    return (b"\x01" + text(record["id"]) + text(record["topic"]) + optional_id(record.get("app_id"))
            + uint64(record.get("received_ns", 0)) + text(record["catalogue"], wide=True) + body)


def read_record(reader):
    flag = reader.number(">B")
    if flag == 0:
        reader.end()
        return None
    if flag != 1:
        raise ValueError("invalid CH/2 record flag")
    handle, topic, app_id = reader.string(), reader.string(), reader.id()
    received_ns, catalogue = reader.number(">Q"), reader.string(wide=True)
    body = reader.rest()
    if not body:
        raise ValueError("empty CH/2 message envelope")
    result = dict(id=handle, topic=topic, received_ns=received_ns, catalogue=catalogue, body=body)
    if app_id is not None:
        result["app_id"] = app_id
    return result


def unpack_record(data):
    return read_record(Reader(data))


@dataclass(frozen=True)
class Activation:
    time_ns: int
    index: int
    due_ns: int
    elapsed: int
    port: str
    event: str
    record: object


def activation_data(reference, index):
    if not index or not reference.elapsed or reference.due_ns > reference.time_ns:
        raise ValueError("invalid CH/2 activation context")
    return (uint64(reference.time_ns) + uint64(index) + uint64(reference.due_ns) + uint64(reference.elapsed)
            + bytes([EVENTS[reference.event]]) + text(reference.port) + record_data(reference.record))


def unpack_activation(data):
    reader = Reader(data)
    time_ns, index, due_ns, elapsed = [reader.number(">Q") for _ in range(4)]
    event, port = reader.number(">B"), reader.string()
    record = read_record(reader)
    if not index or not elapsed or due_ns > time_ns or event not in (1, 2):
        raise ValueError("invalid CH/2 activation context")
    if (event == 1) != (record is not None) or event == 1 and elapsed != 1:
        raise ValueError("activation record does not match event")
    if record is not None and record["received_ns"] > time_ns:
        raise ValueError("activation message is from the future")
    return Activation(time_ns, index, due_ns, elapsed, port, "message" if event == 1 else "timeout", record)


def publication_data(port, payload, app_id=None):
    if not payload:
        raise ValueError("empty CH/2 publication")
    return text(port) + optional_id(app_id) + payload


def unpack_publication(data):
    reader = Reader(data)
    port, app_id, payload = reader.string(), reader.id(), reader.rest()
    if not payload:
        raise ValueError("empty CH/2 publication")
    return port, payload, app_id


def request_data(operation, port, *, app_id=None, handle=None, source=None, delay_ns=None):
    if operation not in OPERATIONS:
        raise ValueError("unknown CH/2 input operation")
    result = bytes([OPERATIONS[operation]]) + text(port)
    if operation == "arm":
        if any(value is not None for value in (app_id, handle, source)) or delay_ns == 0:
            raise ValueError("invalid timer arming request")
        return result + optional_id(delay_ns)
    if operation == "disarm":
        if any(value is not None for value in (app_id, handle, source, delay_ns)):
            raise ValueError("invalid timer disarming request")
        return result
    if delay_ns is not None:
        raise ValueError("message access cannot declare a timer delay")
    return (result + bytes([(1 if app_id is not None else 0) | (2 if handle is not None else 0) | (4 if source is not None else 0)])
            + (uint64(validate_app_id(app_id)) if app_id is not None else b"")
            + (text(handle) if handle is not None else b"") + (text(source) if source is not None else b""))


def unpack_request(data):
    reader = Reader(data)
    code, port = reader.number(">B"), reader.string()
    operation = next((name for name, number in OPERATIONS.items() if number == code), None)
    if operation is None:
        raise ValueError("unknown CH/2 input operation")
    arguments = {}
    if operation == "arm":
        arguments["delay_ns"] = reader.id()
        if arguments["delay_ns"] == 0:
            raise ValueError("timer delay must be positive")
    elif operation != "disarm":
        flags = reader.number(">B")
        if flags & ~7:
            raise ValueError("invalid CH/2 selector flags")
        if flags & 1:
            arguments["app_id"] = reader.number(">Q")
        if flags & 2:
            arguments["handle"] = reader.string()
        if flags & 4:
            arguments["source"] = reader.string()
    reader.end()
    return operation, port, arguments


def check_record_size(port, record):
    # Account for the larger activation message even for a passive inbox.
    size = 33 + len(text(port)) + len(record_data(record))
    if size + 1 > MAX_SIZE:
        raise ValueError("message exceeds CH/2 input reference limit")


class InputServices:
    """Parent-owned inbox access and timer control; removals commit at FINISHED."""
    def __init__(self, scheduler, clock, *, on_change=None, trace=False, on_read=None):
        self.scheduler, self.clock, self.on_change, self.trace = scheduler, clock, on_change, trace
        self.on_read = on_read
        self.begin()

    def begin(self):
        self.operations, self.spent, self.requests = [], 0, 0

    def handle(self, body, *, initializing=False):
        self.requests += 1
        self.spent += len(body)
        if self.requests > 65536 or self.spent > 16 * 1024 * 1024:
            raise ValueError("callback exceeded CH/2 input operation budget")
        try:
            operation, port, arguments = unpack_request(body)
            if self.scheduler.active is None and not initializing:
                raise ValueError("input operation requires an active callback")
            record = None
            if operation in ("arm", "disarm"):
                if operation == "arm":
                    self.scheduler.arm(port, self.clock(), **arguments)
                else:
                    self.scheduler.disarm(port, self.clock())
                response = b"\x01"
            else:
                view = self.scheduler.inbox(port)
                record = view.get(**arguments)
                response = b"\x01" + (bytes([record is not None]) if operation == "remove" else record_data(record))
                frame("A", response)  # Validate size before recording a withdrawal.
                if operation != "get" and record is not None:
                    view.remove(handle=record["id"])
                if self.on_read is not None and record is not None and operation in ('get', 'pull'):
                    self.on_read(port, record, self.clock())
            if self.trace:
                self.operations.append(dict(operation=operation, port=port,
                    **({"handle": record["id"]} if record is not None else {})))
            if self.on_change is not None:
                self.on_change()
        except (ValueError, RuntimeError, KeyError) as error:
            response = b"\0" + str(error).encode()[:4096]
        self.spent += len(response)
        if self.spent > 16 * 1024 * 1024:
            raise ValueError("callback exceeded CH/2 input operation budget")
        return response
