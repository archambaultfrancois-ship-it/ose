"""CENV/2 cross-language vectors, bounds and dynamic-body preservation."""
import json
import math
from pathlib import Path
import random
import struct
import unittest

from ose.transport.protocol import decode, encode, MAGIC, MAX_METADATA

VECTORS = Path(__file__).resolve().parents[2] / "sdk/client/rust/tests/fixtures/cenv2.json"


def packet(code, metadata, body=b""):
    return MAGIC + bytes([code]) + struct.pack(">I", len(metadata)) + metadata + body


class Cenv2Tests(unittest.TestCase):
    def test_shared_python_rust_wire_vectors_and_buffer_ownership(self):
        for fixture in json.loads(VECTORS.read_text()):
            with self.subTest(name=fixture["name"]):
                header, body = fixture["header"], bytes.fromhex(fixture["body_hex"])
                expected = bytes.fromhex(fixture["packet_hex"])
                self.assertEqual(encode(header, body), expected)
                self.assertEqual(decode(expected), (header, body))
                storage = bytearray(b"before" + expected + b"next frame")
                received = decode(storage, start=6, end=6 + len(expected))
                storage.clear()
                self.assertEqual(received, (header, body))
                self.assertIs(type(received[1]), bytes)

    def test_dynamic_catalogue_and_sdl_bytes_are_opaque_on_the_same_route(self):
        header = dict(op="data", subscription="same", id="e:1", epoch="e", sequence=1, topic="source.out")
        bodies = [b"S2PS\x02\0\x01first-contract\0\xff", b"S2PS\x02\0\x02second-contract\xff\0",
                  b"S2PS\x02\0\x01first-contract\0\xff"]
        self.assertEqual([decode(encode(header, body))[1] for body in bodies], bodies)

    def test_each_metadata_truncation_unknown_flags_and_obsolete_versions_fail(self):
        for fixture in json.loads(VECTORS.read_text()):
            data = bytes.fromhex(fixture["packet_hex"])
            metadata_end = 8 + struct.unpack_from(">I", data, 4)[0]
            for length in range(metadata_end):
                with self.subTest(name=fixture["name"], length=length), self.assertRaises(ValueError):
                    decode(data[:length])
        for data in (b'CE\x01\0\0\0\x02{}', b'CE\x03\0\0\0\0\0',
                     packet(99, b""), packet(1, b"\x02"), packet(4, b"\x08"),
                     packet(2, b"\0\0\0\0trailing"), packet(0, b"\xff")):
            with self.subTest(data=data), self.assertRaises(ValueError):
                decode(data)
        with self.assertRaisesRegex(ValueError, "expected CENV/2"):
            decode(b'CE\x01\0\0\0\x02{}')

    def test_generic_metadata_rejects_duplicates_nonfinite_numbers_and_deep_nesting(self):
        key = struct.pack(">I", 2) + b"op"
        string = b"\x06\0\0\0\x04test"
        invalid = [b"\x08\0\0\0\x02" + key + string + key + string,
                   b"\x07\xff\xff\xff\xff", b"\x08\xff\xff\xff\xff",
                   b"\x04" + struct.pack(">q", 1),
                   b"\x05" + struct.pack(">d", float("inf")),
                   b"\x06\0\0\0\x01\xff", b"\x07\0\0\0\x01" * 66 + b"\0"]
        for metadata in invalid:
            with self.subTest(metadata=metadata[:20]), self.assertRaises(ValueError):
                decode(packet(0, metadata))
        nested = 1
        for _ in range(65):
            nested = [nested]
        for value in (nested, float("nan"), float("inf"), 1 << 64, -(1 << 63) - 1, {1: "non-string"}):
            with self.subTest(value=repr(value)[:80]), self.assertRaises(ValueError):
                encode(dict(op="control", value=value))

    def test_optional_ids_keep_zero_distinct_from_absence_and_reject_inexact_types(self):
        base = dict(op="publish", request=1, topic="source.out")
        for identity in (None, 0, (1 << 53) + 1, (1 << 64) - 1):
            header = dict(base, **({} if identity is None else dict(app_id=identity)))
            result, _ = decode(encode(header))
            self.assertEqual(result, header)
            self.assertEqual("app_id" in result, identity is not None)
        for identity in (True, False, 1., -1, 1 << 64, "1", None):
            with self.subTest(identity=identity), self.assertRaises(ValueError):
                encode(dict(base, app_id=identity))

    def test_metadata_and_whole_packet_limits_include_all_fields(self):
        header = dict(op="control", text="x" * (MAX_METADATA - 36))
        data = encode(header)
        # op/control/text string overhead is exactly 36 bytes.
        self.assertEqual(len(data), MAX_METADATA + 8)
        self.assertEqual(decode(data)[0], header)
        with self.assertRaises(ValueError):
            encode(dict(header, text=header["text"] + "x"))
        self.assertEqual(decode(data, len(data)), (header, b""))
        with self.assertRaises(ValueError):
            decode(data, len(data) - 1)
        with self.assertRaises(ValueError):
            encode(header, b"x", len(data))
        with self.assertRaises(ValueError):
            decode(MAGIC + b"\0" + struct.pack(">I", MAX_METADATA + 1))

    def test_malformed_mutations_never_leak_struct_errors_or_borrow_body_storage(self):
        randomizer = random.Random(42)
        fixtures = json.loads(VECTORS.read_text())
        for _ in range(500):
            data = bytearray.fromhex(randomizer.choice(fixtures)["packet_hex"])
            data[randomizer.randrange(len(data))] ^= 1 << randomizer.randrange(8)
            try:
                header, body = decode(data)
            except ValueError:
                continue
            data.clear()
            self.assertEqual(decode(encode(header, body)), (header, body))
            self.assertTrue(all(not isinstance(value, float) or math.isfinite(value) for value in header.values()))
