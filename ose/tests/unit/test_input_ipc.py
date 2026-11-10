"""CH/2 bounds, exact IDs, opaque dynamic contracts and deferred withdrawals."""
import struct
import unittest

from ose.delivery.buffers import Buffer
from ose.delivery.policies import SubscriptionPolicy
from ose.delivery.stores import MemoryStore
from ose.execution.activation import InputScheduler
from ose.execution import input_ipc as wire


class InputIPCTests(unittest.TestCase):
    def record(self, app_id=0):
        return dict(id="epoch:1", topic="source.out", catalogue="SDL2|Value{1:uint32:count;}",
                    body=b"S2PS\x02\0\0\0\x01\0\xff", received_ns=12, app_id=app_id)

    def test_record_keeps_envelope_catalogue_and_exact_uint64(self):
        for value in (0, (1 << 53) + 1, (1 << 64) - 1):
            record = self.record(value)
            self.assertEqual(wire.unpack_record(wire.record_data(record)), record)
        self.assertIsNone(wire.unpack_record(wire.record_data(None)))
        original = self.record()
        data = wire.record_data(original)
        for length in range(len(data) - len(original["body"])):
            with self.subTest(length=length), self.assertRaises(ValueError):
                wire.unpack_record(data[:length])
        with self.assertRaises(ValueError):
            wire.unpack_record(b"\x02")
        with self.assertRaises(ValueError):
            wire.unpack_record(b"\0trailing")

    def test_selectors_and_timer_requests_reject_truncation_or_extra_data(self):
        fixtures = [("get", dict(app_id=0, handle="epoch:1", source="source.out")),
                    ("pull", dict(app_id=(1 << 64) - 1)), ("remove", {}),
                    ("arm", dict(delay_ns=123)), ("arm", dict(delay_ns=None)), ("disarm", {})]
        for operation, arguments in fixtures:
            data = wire.request_data(operation, "input", **arguments)
            decoded = wire.unpack_request(data)
            self.assertEqual(decoded, (operation, "input", arguments))
            for length in range(len(data)):
                with self.subTest(operation=operation, length=length), self.assertRaises(ValueError):
                    wire.unpack_request(data[:length])
            with self.assertRaises(ValueError):
                wire.unpack_request(data + b"x")
        for operation, arguments in (("arm", dict(delay_ns=0)), ("get", dict(app_id=True)),
                                     ("pull", dict(delay_ns=1)), ("disarm", dict(handle="x"))):
            with self.subTest(operation=operation), self.assertRaises(ValueError):
                wire.request_data(operation, "input", **arguments)

    def test_publication_keeps_numeric_id_and_owned_payload(self):
        payload = b"\0\xffpayload"
        for identity in (None, 0, (1 << 64) - 1):
            data = wire.publication_data("out", payload, identity)
            self.assertEqual(wire.unpack_publication(data), ("out", payload, identity))
        for invalid in (b"", b"\0\x01x\x02", b"\0\x01x\x01\0"):
            with self.assertRaises(ValueError):
                wire.unpack_publication(invalid)

    def test_parent_view_consumes_only_after_finished_and_rolls_back_failure(self):
        node = dict(inputs=dict(clock=dict(kind="timer", timer=dict(mode="periodic", delay=.01)),
                                inbox=dict(root="Value", mode="pull")), outputs={})
        buffer = Buffer(SubscriptionPolicy(capacity=1), MemoryStore())
        scheduler = InputScheduler(node, {"inbox": buffer})
        scheduler.start()
        record = dict(self.record(), epoch="epoch", sequence=1)
        scheduler.admit("inbox", record, 0)
        services = wire.InputServices(scheduler, lambda: 10_000_000)
        reference = scheduler.select(10_000_000)
        reply = services.handle(wire.request_data("pull", "inbox", app_id=0))
        self.assertEqual(wire.unpack_record(reply[1:])["id"], "epoch:1")
        self.assertEqual(buffer.store.size(), 1)
        self.assertEqual(services.handle(wire.request_data("get", "inbox")), b"\x01\0")
        scheduler.complete(reference, 10_000_000, success=False)
        self.assertEqual(buffer.store.size(), 1)
        reference = scheduler.select(20_000_000)
        reply = services.handle(wire.request_data("remove", "inbox", handle="epoch:1"))
        self.assertEqual(reply, b"\x01\x01")
        scheduler.complete(reference, 20_000_000)
        self.assertEqual(buffer.store.size(), 0)

    def test_activation_round_trip_and_invalid_context(self):
        node = dict(inputs=dict(clock=dict(kind="timer", timer=dict(mode="one_shot", delay=.01))), outputs={})
        scheduler = InputScheduler(node, {})
        scheduler.start()
        reference = scheduler.select(10_000_000)
        data = wire.activation_data(reference, 1)
        decoded = wire.unpack_activation(data)
        self.assertEqual((decoded.port, decoded.event, decoded.elapsed, decoded.record), ("clock", "timeout", 1, None))
        for length in range(len(data)):
            with self.subTest(length=length), self.assertRaises(ValueError):
                wire.unpack_activation(data[:length])
        invalid = bytearray(data)
        struct.pack_into(">Q", invalid, 8, 0)
        with self.assertRaises(ValueError):
            wire.unpack_activation(invalid)

    def test_parent_rejects_access_outside_callback_and_bounds_operation_budget(self):
        scheduler = InputScheduler(dict(inputs={}, outputs={}), {})
        scheduler.start()
        services = wire.InputServices(scheduler, lambda: 0)
        self.assertEqual(services.handle(wire.request_data("get", "missing"))[:1], b"\0")
        services.spent = 16 * 1024 * 1024
        with self.assertRaisesRegex(ValueError, "budget"):
            services.handle(wire.request_data("get", "missing"))
