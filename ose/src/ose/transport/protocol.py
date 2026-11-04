"""CENV/1: bounded JSON metadata plus an opaque binary S2PS/2 body."""
import json
import struct

MAGIC = b"CE\x01"


def encode(header, payload=b"", limit=1024 * 1024):
    metadata = json.dumps(header, separators=(",", ":"), allow_nan=False).encode()
    if len(metadata) > 65536:
        raise ValueError("metadata exceeds 64 KiB")
    packet = MAGIC + struct.pack(">I", len(metadata)) + metadata + payload
    if len(packet) > limit:
        raise ValueError("transport message exceeds configured size limit")
    return packet


def decode(packet, limit=1024 * 1024, *, start=0, end=None):
    """Decode one bounded packet, optionally inside a mutable receive buffer."""
    end = len(packet) if end is None else end
    size = end - start
    if not 0 <= start <= end <= len(packet) or size > limit or size < 7 or packet[start:start + 3] != MAGIC:
        raise ValueError("invalid CENV/1 packet")
    length = struct.unpack_from(">I", packet, start + 3)[0]
    if length > 65536 or length > size - 7:
        raise ValueError("invalid metadata length")
    body_start = start + 7 + length
    header = json.loads(packet[start + 7:body_start])
    if not isinstance(header, dict) or not isinstance(header.get("op"), str):
        raise ValueError("invalid metadata")
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
