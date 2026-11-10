#!/usr/bin/env python3
"""Reproducible envelope and RAM inbox baseline; no sockets or SDL decoding."""
import argparse
import json
from pathlib import Path
import platform
import statistics
import struct
import timeit

from ose.delivery.buffers import Buffer
from ose.delivery.policies import SubscriptionPolicy
from ose.delivery.stores import MemoryStore
from ose.transport.protocol import decode, encode


def cenv1_encode(header, payload=b"", limit=1024 * 1024):
    """Frozen baseline used only for measurements, never by runtime transports."""
    metadata = json.dumps(header, separators=(",", ":"), allow_nan=False).encode()
    if len(metadata) > 65536:
        raise ValueError("metadata exceeds 64 KiB")
    packet = b"CE\x01" + struct.pack(">I", len(metadata)) + metadata + payload
    if len(packet) > limit:
        raise ValueError("transport message exceeds configured size limit")
    return packet


def cenv1_decode(packet, limit=1024 * 1024, *, start=0, end=None):
    end = len(packet) if end is None else end
    size = end - start
    if not 0 <= start <= end <= len(packet) or size > limit or size < 7 or packet[start:start + 3] != b"CE\x01":
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
        return header, bytes(packet[body_start:end])
    with memoryview(packet) as view:
        with view[body_start:end] as payload:
            return header, bytes(payload)


def samples(operation, iterations, repeats):
    values = [elapsed * 1e9 / iterations
              for elapsed in timeit.repeat(operation, number=iterations, repeat=repeats)]
    return {"median_ns_per_operation": statistics.median(values),
            "samples_ns_per_operation": values}


def benchmark(iterations, repeats):
    epoch = "0123456789abcdef0123456789abcdef"
    data = dict(op="data", subscription="signal_1", id=f"{epoch}:42",
                epoch=epoch, sequence=42, topic="sensor_1.signal")
    catalogue = b"SDL2|Value{1:uint32:count;}"
    fixtures = [("data_small", data, b"\0\xff" * 8),
                ("data_8k", data, b"\0\xff" * 4096),
                ("ack", dict(op="ack", subscription="signal_1", id=f"{epoch}:42"), b""),
                ("catalogue_result", dict(op="result", request=42), catalogue)]
    envelopes, baseline = [], []
    for name, header, body in fixtures:
        for encoder, decoder, target in ((cenv1_encode, cenv1_decode, baseline), (encode, decode, envelopes)):
            packet = encoder(header, body)
            if decoder(packet) != (header, body):
                raise RuntimeError(f"fixture does not round-trip: {name}")
            # Exercise the owned-payload path inside mutable TCP receive buffers.
            receive_buffer = bytearray(b"prefix" + packet + b"following frame")
            start, end = 6, 6 + len(packet)
            if decoder(receive_buffer, start=start, end=end) != (header, body):
                raise RuntimeError(f"buffer fixture does not round-trip: {name}")
            target.append(dict(name=name, body_bytes=len(body), packet_bytes=len(packet),
                envelope_overhead_bytes=len(packet) - len(body), tcp_prefix_bytes=4,
                encode=samples(lambda: encoder(header, body), iterations, repeats),
                decode_bytes=samples(lambda: decoder(packet), iterations, repeats),
                decode_buffer=samples(lambda: decoder(receive_buffer, start=start, end=end), iterations, repeats)))

    inboxes = []
    for name, policy in [("fifo_consume", SubscriptionPolicy(capacity=64)),
                         ("fifo_evict", SubscriptionPolicy(capacity=64, overflow="drop_oldest")),
                         ("latest_replace", SubscriptionPolicy.resolve({"buffer": "latest"}))]:
        buffer = Buffer(policy, MemoryStore(), clock=lambda: 0.0)
        sequence = 0
        body = b"\0\xff" * 8

        def cycle():
            nonlocal sequence
            sequence += 1
            record = dict(id=f"{epoch}:{sequence}", epoch=epoch, sequence=sequence,
                          topic=f"source_{sequence % 4}.out", body=body)
            if buffer.admit(record) != "accepted":
                raise RuntimeError("unexpected inbox refusal")
            current = buffer.take()
            if name == "fifo_consume":
                buffer.consumed(current["id"])

        inboxes.append(dict(name=name, producers=4, policy=policy.to_dict(),
            operation="build record, admit, peek; consume only for fifo_consume",
            timing=samples(cycle, iterations, repeats)))
    comparison = [dict(name=current["name"],
        saved_envelope_bytes=previous["envelope_overhead_bytes"] - current["envelope_overhead_bytes"],
        **{metric + "_ratio_v2_over_v1": current[metric]["median_ns_per_operation"] / previous[metric]["median_ns_per_operation"]
           for metric in ("encode", "decode_bytes", "decode_buffer")})
        for previous, current in zip(baseline, envelopes)]
    return dict(format_version=2, protocol="CENV/2", iterations=iterations, repeats=repeats,
                environment=dict(python=platform.python_version(),
                    implementation=platform.python_implementation(),
                    system=platform.system(), release=platform.release(), machine=platform.machine()),
                envelopes=envelopes, baseline=dict(protocol="CENV/1", envelopes=baseline),
                comparison=comparison, inboxes=inboxes)


def positive(value):
    result = int(value)
    if result <= 0:
        raise argparse.ArgumentTypeError("must be positive")
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--iterations", type=positive, default=10000)
    parser.add_argument("--repeats", type=positive, default=5)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    result = benchmark(args.iterations, args.repeats)
    report = json.dumps(result, indent=2, allow_nan=False) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(report)
    print(report, end="")


if __name__ == "__main__":
    main()
