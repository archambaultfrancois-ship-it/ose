"""Explicit inbox access preserves delivery identities and immediate consumption."""
import unittest

from ose.delivery.buffers import Buffer
from ose.delivery.messages import MAX_APP_ID
from ose.delivery.policies import SubscriptionPolicy as Policy
from ose.delivery.stores import MemoryStore


def message(number, *, source="a.out", **metadata):
    return dict(id=f"session:{number}", epoch="session", sequence=number,
                topic=source, body=b"\0\xffpayload", **metadata)


class InboxAccessTests(unittest.TestCase):
    def inbox(self, **policy):
        return Buffer(Policy.resolve(policy), MemoryStore(), clock=lambda: 0.0)

    def test_repeated_get_neither_consumes_reserves_nor_mutates_queue_metadata(self):
        buffer = self.inbox(capacity=1)
        original = message(1, app_id=0)
        buffer.admit(original)
        buffer.store.changed.clear()
        viewed = buffer.get(app_id=0)
        self.assertEqual(viewed, buffer.get())
        self.assertIs(viewed["body"], original["body"])
        viewed["app_id"], viewed["id"] = 99, "changed"
        self.assertEqual(buffer.get()["app_id"], 0)
        self.assertEqual(buffer.get()["id"], original["id"])
        self.assertEqual(buffer.reserved, set())
        self.assertFalse(buffer.store.changed.is_set())
        self.assertEqual(buffer.admit(message(2)), "full")

    def test_selective_pull_keeps_remaining_order_and_duplicate_application_ids(self):
        buffer = self.inbox()
        for record in (message(3, app_id=7), message(1, source="b.out", app_id=7),
                       message(2, app_id=7), message(4)):
            self.assertEqual(buffer.admit(record), "accepted")
        self.assertEqual(buffer.pull(app_id=7, source="b.out")["sequence"], 1)
        self.assertEqual(buffer.get(app_id=7)["sequence"], 3)
        self.assertEqual([record["sequence"] for record in buffer.store.records()], [3, 2, 4])
        self.assertTrue(buffer.remove(handle="session:2", app_id=7, source="a.out"))
        self.assertEqual(buffer.pull()["sequence"], 3)
        # Missing application IDs are distinct from zero.
        self.assertIsNone(buffer.get(app_id=0))
        self.assertEqual(buffer.pull()["sequence"], 4)

    def test_consumption_frees_capacity_at_call_and_does_not_undo_deduplication(self):
        for operation in ("pull", "remove"):
            with self.subTest(operation=operation):
                buffer = self.inbox(capacity=1, overflow="wait")
                buffer.admit(message(1, app_id=42))
                self.assertEqual(buffer.admit(message(2)), "full")
                buffer.store.changed.clear()
                self.assertTrue(getattr(buffer, operation)(app_id=42))
                self.assertTrue(buffer.store.changed.is_set())
                self.assertEqual(buffer.store.size(), 0)
                self.assertEqual(buffer.admit(message(2, app_id=42)), "accepted")
                self.assertEqual(buffer.admit(message(1, app_id=42)), "duplicate")

    def test_evicted_handle_cannot_consume_another_message_with_the_same_app_id(self):
        buffer = self.inbox(capacity=1, overflow="drop_oldest")
        buffer.admit(message(1, app_id=7))
        viewed = buffer.get()
        buffer.admit(message(2, app_id=7))
        self.assertIsNone(buffer.pull(handle=viewed["id"]))
        self.assertFalse(buffer.remove(handle=viewed["id"]))
        self.assertEqual(buffer.get(app_id=7)["sequence"], 2)

    def test_absence_or_selector_mismatch_is_nonblocking_and_has_no_effect(self):
        buffer = self.inbox()
        buffer.admit(message(1, app_id=7))
        buffer.store.changed.clear()
        for selector in ({"app_id": 8}, {"source": "b.out"}, {"handle": "missing"},
                         {"handle": "session:1", "app_id": 8},
                         {"handle": "session:1", "source": "b.out"}):
            with self.subTest(selector=selector):
                self.assertIsNone(buffer.get(**selector))
                self.assertIsNone(buffer.pull(**selector))
                self.assertFalse(buffer.remove(**selector))
        self.assertFalse(buffer.store.changed.is_set())
        self.assertEqual(buffer.store.size(), 1)

    def test_push_reservations_cannot_be_consumed_by_explicit_access(self):
        buffer = self.inbox(capacity=2)
        buffer.admit(message(1, app_id=7))
        buffer.admit(message(2, app_id=7))
        buffer.reserved.add("session:1")
        self.assertEqual(buffer.pull(app_id=7)["sequence"], 2)
        self.assertIsNone(buffer.get(handle="session:1"))
        self.assertFalse(buffer.remove(handle="session:1"))
        self.assertEqual(buffer.take()["sequence"], 1)

    def test_latest_clear_keeps_source_history_and_push_peek_behavior(self):
        for operation in ("pull", "remove"):
            with self.subTest(operation=operation):
                buffer = self.inbox(buffer="latest")
                buffer.admit(message(3, app_id=7))
                self.assertEqual(buffer.take(), buffer.take())
                self.assertTrue(getattr(buffer, operation)())
                self.assertIsNone(buffer.get())
                self.assertEqual(buffer.admit(message(2)), "stale")
                self.assertEqual(buffer.admit(message(4, app_id=7)), "accepted")

    def test_uint64_boundaries_and_invalid_ids_do_not_change_inbox(self):
        buffer = self.inbox()
        for number, app_id in enumerate((0, (1 << 53) + 1, MAX_APP_ID), 1):
            self.assertEqual(buffer.admit(message(number, app_id=app_id)), "accepted")
            self.assertEqual(buffer.get(app_id=app_id)["sequence"], number)
        before, metrics = buffer.store.records(), dict(buffer.metrics)
        for value in (True, False, -1, 1 << 64, 1.0, "7", None):
            with self.subTest(value=value), self.assertRaisesRegex(ValueError, "uint64"):
                buffer.admit(message(4, app_id=value))
        for value in (True, False, -1, 1 << 64, 1.0, "7"):
            for operation in ("get", "pull", "remove"):
                with self.subTest(value=value, operation=operation), self.assertRaisesRegex(ValueError, "uint64"):
                    getattr(buffer, operation)(app_id=value)
        self.assertEqual(buffer.store.records(), before)
        self.assertEqual(dict(buffer.metrics), metrics)

    def test_invalid_handle_and_source_fail_before_consumption(self):
        buffer = self.inbox()
        buffer.admit(message(1))
        for field in ("handle", "source"):
            for value in ("", 42, False):
                with self.subTest(field=field, value=value), self.assertRaises(ValueError):
                    buffer.remove(**{field: value})
        self.assertEqual(buffer.store.size(), 1)
