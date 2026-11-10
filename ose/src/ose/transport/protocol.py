"""CENV/2: bounded binary metadata and an unchanged opaque message body."""
import math
import struct

from ose.delivery.messages import validate_app_id

MAGIC = b"CE\x02"
PREFIX_SIZE = 8
MAX_METADATA = 65536
MAX_DEPTH = 64

# Code, operation, required fields, optional fields. t = UTF-8/u16, u = u64.
SCHEMAS = (
    (1, "data", (("subscription", "t"), ("id", "t"), ("epoch", "t"),
                 ("sequence", "u"), ("topic", "t")), (("app_id", "u"),)),
    (2, "ack", (("subscription", "t"), ("id", "t")), ()),
    (3, "publish", (("request", "u"), ("topic", "t")), (("app_id", "u"),)),
    (4, "result", (("request", "u"),), (("content", "t"), ("error", "t"), ("error_code", "t"))),
    (5, "deliver", (("request", "u"), ("subscription", "t"), ("id", "t"),
                    ("epoch", "t"), ("sequence", "u"), ("source_token", "t"),
                    ("target_token", "t"), ("source", "t"), ("topic", "t")), (("app_id", "u"),)),
)
BY_CODE = {schema[0]: schema for schema in SCHEMAS}
_COMPACT = {operation: (code, frozenset(name for name, _ in required),
    frozenset(["op", *(name for name, _ in required + optional)]), required + optional, optional)
    for code, operation, required, optional in SCHEMAS}
_U16, _U32, _U64, _I64, _F64 = (struct.Struct(format) for format in (">H", ">I", ">Q", ">q", ">d"))


def _header(header):
    if not isinstance(header, dict) or not isinstance(header.get("op"), str) or not header["op"]:
        raise ValueError("invalid CENV/2 metadata")
    if header["op"] in ("data", "publish", "deliver") and "app_id" in header:
        validate_app_id(header["app_id"])


def _compact(header, schema):
    code, required, allowed, fields, optional = schema
    if not required.issubset(header) or not header.keys() <= allowed:
        return None
    parts = []
    if optional:
        parts.append(bytes([sum(1 << index for index, (name, _) in enumerate(optional) if name in header)]))
    for name, kind in fields:
        if name not in header:
            continue
        value = header[name]
        if kind == "t":
            if not isinstance(value, str):
                return None
            data = value.encode()
            if len(data) > 65535:
                return None
            parts.append(_U16.pack(len(data)) + data)
        else:
            if type(value) is not int or not 0 <= value <= 0xffffffffffffffff:
                return None
            parts.append(_U64.pack(value))
    return code, b"".join(parts)


class _Writer:
    def __init__(self):
        self.data = bytearray()

    def add(self, data):
        if len(data) > MAX_METADATA - len(self.data):
            raise ValueError("metadata exceeds 64 KiB")
        self.data.extend(data)

    def text(self, value, *, compact=False):
        if len(value) > MAX_METADATA:
            raise ValueError("metadata text exceeds 64 KiB")
        data = value.encode()
        if len(data) > (65535 if compact else MAX_METADATA):
            raise ValueError("metadata text exceeds size limit")
        self.add((_U16 if compact else _U32).pack(len(data)))
        self.add(data)

    def value(self, value, depth=0):
        if depth > MAX_DEPTH:
            raise ValueError("metadata nesting exceeds 64 levels")
        if value is None:
            self.add(b"\0")
        elif type(value) is bool:
            self.add(b"\x02" if value else b"\x01")
        elif type(value) is int:
            if 0 <= value <= 0xffffffffffffffff:
                self.add(b"\x03" + _U64.pack(value))
            elif -(1 << 63) <= value < 0:
                self.add(b"\x04" + _I64.pack(value))
            else:
                raise ValueError("metadata integer is outside int64/uint64")
        elif type(value) is float and math.isfinite(value):
            self.add(b"\x05" + _F64.pack(value))
        elif isinstance(value, str):
            self.add(b"\x06")
            self.text(value)
        elif isinstance(value, (list, tuple)):
            if len(value) > MAX_METADATA:
                raise ValueError("metadata array exceeds size limit")
            self.add(b"\x07" + _U32.pack(len(value)))
            for item in value:
                self.value(item, depth + 1)
        elif isinstance(value, dict):
            if len(value) > MAX_METADATA or any(not isinstance(key, str) for key in value):
                raise ValueError("metadata objects require string keys")
            self.add(b"\x08" + _U32.pack(len(value)))
            for key in sorted(value):
                self.text(key)
                self.value(value[key], depth + 1)
        else:
            raise ValueError("unsupported metadata value")


class _Reader:
    def __init__(self, data, start, end):
        self.data, self.offset, self.end = data, start, end

    def number(self, codec):
        if codec.size > self.end - self.offset:
            raise ValueError("truncated CENV/2 metadata")
        value = codec.unpack_from(self.data, self.offset)[0]
        self.offset += codec.size
        return value

    def byte(self):
        if self.offset == self.end:
            raise ValueError("truncated CENV/2 metadata")
        value = self.data[self.offset]
        self.offset += 1
        return value

    def text(self, *, compact=False):
        codec = _U16 if compact else _U32
        if codec.size > self.end - self.offset:
            raise ValueError("truncated CENV/2 text length")
        length = codec.unpack_from(self.data, self.offset)[0]
        self.offset += codec.size
        if length > self.end - self.offset:
            raise ValueError("truncated CENV/2 text")
        start, self.offset = self.offset, self.offset + length
        return self.data[start:self.offset].decode("utf-8")

    def value(self, depth=0):
        if depth > MAX_DEPTH:
            raise ValueError("metadata nesting exceeds 64 levels")
        tag = self.byte()
        if tag == 0:
            return None
        if tag in (1, 2):
            return tag == 2
        if tag in (3, 4, 5):
            value = self.number({3: _U64, 4: _I64, 5: _F64}[tag])
            if tag == 4 and value >= 0 or tag == 5 and not math.isfinite(value):
                raise ValueError("invalid CENV/2 number")
            return value
        if tag == 6:
            return self.text()
        if tag in (7, 8):
            count = self.number(_U32)
            if count > (self.end - self.offset) // (5 if tag == 8 else 1):
                raise ValueError("invalid CENV/2 container length")
            if tag == 7:
                return [self.value(depth + 1) for _ in range(count)]
            result = {}
            for _ in range(count):
                key = self.text()
                if key in result:
                    raise ValueError("duplicate CENV/2 metadata key")
                result[key] = self.value(depth + 1)
            return result
        raise ValueError("unknown CENV/2 value tag")


def encode(header, payload=b"", limit=1024 * 1024):
    _header(header)
    schema = _COMPACT.get(header["op"])
    compact = _compact(header, schema) if schema is not None else None
    if compact is None:
        writer = _Writer()
        writer.value(header)
        code, metadata = 0, writer.data
    else:
        code, metadata = compact
    if len(metadata) > MAX_METADATA:
        raise ValueError("metadata exceeds 64 KiB")
    if PREFIX_SIZE + len(metadata) + len(payload) > limit:
        raise ValueError("transport message exceeds configured size limit")
    return MAGIC + bytes([code]) + _U32.pack(len(metadata)) + metadata + payload


def _read_compact(schema, data, offset, end):
    _, operation, required, optional = schema
    flags = 0
    if optional:
        if offset == end:
            raise ValueError("truncated CENV/2 flags")
        flags, offset = data[offset], offset + 1
        if flags >> len(optional):
            raise ValueError("unknown CENV/2 flags")
    header = {"op": operation}
    fields = required + tuple(field for index, field in enumerate(optional) if flags & (1 << index))
    for name, kind in fields:
        if kind == "t":
            if offset + 2 > end:
                raise ValueError("truncated CENV/2 text length")
            length = _U16.unpack_from(data, offset)[0]
            offset += 2
            if length > end - offset:
                raise ValueError("truncated CENV/2 text")
            header[name] = data[offset:offset + length].decode("utf-8")
            offset += length
        else:
            if offset + 8 > end:
                raise ValueError("truncated CENV/2 number")
            header[name] = _U64.unpack_from(data, offset)[0]
            offset += 8
    return header, offset


def decode(packet, limit=1024 * 1024, *, start=0, end=None):
    """Decode one bounded packet; returned body bytes own their storage."""
    end = len(packet) if end is None else end
    size = end - start
    if not 0 <= start <= end <= len(packet) or size > limit or size < PREFIX_SIZE:
        raise ValueError("invalid CENV/2 packet")
    if packet[start:start + 3] != MAGIC:
        raise ValueError("unsupported CENV version; expected CENV/2")
    code, length = packet[start + 3], _U32.unpack_from(packet, start + 4)[0]
    if length > MAX_METADATA or length > size - PREFIX_SIZE:
        raise ValueError("invalid metadata length")
    body_start = start + PREFIX_SIZE + length
    if code == 0:
        reader = _Reader(packet, start + PREFIX_SIZE, body_start)
        header = reader.value()
        offset = reader.offset
        _header(header)
    else:
        schema = BY_CODE.get(code)
        if schema is None:
            raise ValueError("unknown CENV/2 opcode")
        header, offset = _read_compact(schema, packet, start + PREFIX_SIZE, body_start)
    if offset != body_start:
        raise ValueError("trailing CENV/2 metadata")
    if body_start == end:
        return header, b""
    if isinstance(packet, bytes):
        return header, packet[body_start:end]
    if end - body_start <= 256:
        # Tiny copies cost less than constructing and releasing two buffer views.
        return header, bytes(packet[body_start:end])
    # Returned payloads must outlive compaction and reuse of the receive buffer.
    with memoryview(packet) as view:
        with view[body_start:end] as payload:
            return header, bytes(payload)
