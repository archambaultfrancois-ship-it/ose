"""Shared component framing and named service fields on a private socketpair."""
import struct
from collections import deque
from weakref import WeakKeyDictionary
from ose.acceleration import native

_receivers = WeakKeyDictionary()

MAX_SIZE = 1024 * 1024
def frame(op, data=b""):
    body = op.encode("ascii") + data
    if len(body) > MAX_SIZE:
        raise ValueError("IPC frame exceeds 1 MiB")
    return struct.pack(">I", len(body)) + body


def port_data(port, payload, catalogue=None):
    name = port.encode()
    if len(name) > 65535:
        raise ValueError("port name too long")
    result = struct.pack(">H", len(name)) + name
    if catalogue is not None:
        text = catalogue.encode()
        result += struct.pack(">I", len(text)) + text
    return result + payload


def unpack_port(data, with_catalogue=False):
    if len(data) < 2:
        raise ValueError("truncated IPC port")
    length = struct.unpack(">H", data[:2])[0]
    if len(data) < 2 + length:
        raise ValueError("truncated IPC port name")
    port, rest = data[2:2 + length].decode(), data[2 + length:]
    if not with_catalogue:
        return port, rest
    if len(rest) < 4:
        raise ValueError("truncated IPC catalogue")
    size = struct.unpack(">I", rest[:4])[0]
    if size > len(rest) - 4:
        raise ValueError("truncated IPC catalogue")
    return port, rest[4:4 + size].decode(), rest[4 + size:]


async def receive(reader):
    if native is not None:
        import asyncio
        if reader not in _receivers:
            _receivers[reader] = bytearray(), deque()
        buffer, pending = _receivers[reader]
        while not pending:
            if buffer:
                frames, consumed = native.unpack_frames(buffer)
                pending.extend(frames)
                del buffer[:consumed]
                if pending:
                    break
            data = await reader.read(65536)
            if not data:
                if len(buffer) < 4:
                    raise asyncio.IncompleteReadError(bytes(buffer), 4)
                raise asyncio.IncompleteReadError(bytes(buffer[4:]), int.from_bytes(buffer[:4], 'big'))
            buffer.extend(data)
        return pending.popleft()
    size = struct.unpack(">I", await reader.readexactly(4))[0]
    if not 1 <= size <= MAX_SIZE:
        raise ValueError("invalid IPC size")
    packet = await reader.readexactly(size)
    return chr(packet[0]), packet[1:]


async def send(writer, op, data=b""):
    writer.write(frame(op, data))
    await writer.drain()
