"""Input callbacks, independent deadlines and deferred, bounded Pull access."""
import unittest

from ose.delivery.buffers import Buffer
from ose.delivery.policies import SubscriptionPolicy as Policy
from ose.delivery.stores import MemoryStore
from ose.execution.activation import InputScheduler
from ose.timing import MAX_NS

MS = 1_000_000


def message(number, *, topic="source.out", **metadata):
    return dict(id=f"session:{number}", epoch="session", sequence=number,
                topic=topic, body=b"\0\xff", **metadata)


class InputSchedulerTests(unittest.TestCase):
    def scheduler(self, inputs, *, policies=None, outputs=()):
        node = dict(inputs=inputs, outputs={port: dict(root="Value") for port in outputs})
        buffers = {port: Buffer(Policy.resolve((policies or {}).get(port)), MemoryStore(), clock=lambda: 0.)
                   for port, desc in inputs.items() if desc.get("kind") != "timer"}
        scheduler = InputScheduler(node, buffers)
        scheduler.start()
        return scheduler

    def test_push_is_independent_and_pull_is_neither_activating_nor_consumed(self):
        scheduler = self.scheduler(dict(a=dict(root="Value"), b=dict(root="Value"),
                                         feedback=dict(root="Value", mode="pull")))
        scheduler.admit("feedback", message(1), 0)
        self.assertIsNone(scheduler.select(0))
        self.assertIsNone(scheduler.next_event(0))
        scheduler.admit("a", message(2), 0)
        reference = scheduler.select(0)
        self.assertEqual((reference.port, reference.event), ("a", "message"))
        self.assertEqual(reference.record["id"], "session:2")
        with self.assertRaises(TypeError):
            reference.record["id"] = "changed"
        scheduler.complete(reference, 0)
        self.assertEqual(scheduler.buffers["feedback"].store.size(), 1)
        self.assertIsNone(scheduler.select(0))

    def test_pull_visibility_cuts_off_new_arrivals_without_reserving_messages(self):
        scheduler = self.scheduler(dict(trigger=dict(root="Value"), inbox=dict(root="Value", mode="pull")))
        scheduler.admit("inbox", message(1, app_id=7), 0)
        scheduler.admit("trigger", message(10), 0)
        reference = scheduler.select(0)
        view = scheduler.inbox("inbox")
        scheduler.admit("inbox", message(2, app_id=8), 1)
        self.assertIsNone(view.get(app_id=8))
        self.assertEqual(view.pull(app_id=7)["sequence"], 1)
        self.assertIsNone(view.get())
        self.assertEqual(scheduler.buffers["inbox"].reserved, set())
        scheduler.complete(reference, 1)
        with self.assertRaises(RuntimeError):
            view.remove()
        scheduler.admit("trigger", message(11), 1)
        reference = scheduler.select(1)
        self.assertEqual(scheduler.inbox("inbox").get()["sequence"], 2)
        scheduler.complete(reference, 1)

    def test_get_does_not_protect_against_eviction_and_never_exposes_later_latest(self):
        scheduler = self.scheduler(dict(trigger=dict(root="Value"), state=dict(root="Value", mode="pull")),
                                   policies=dict(state=dict(buffer="latest")))
        scheduler.admit("state", message(1), 0)
        scheduler.admit("trigger", message(10), 0)
        reference = scheduler.select(0)
        view = scheduler.inbox("state")
        old = view.get()
        scheduler.admit("state", message(2), 1)
        self.assertIsNone(view.get())
        self.assertFalse(view.remove(handle=old["id"]))
        self.assertIsNone(scheduler.next_event(1))  # No concurrent callback.
        scheduler.complete(reference, 1)
        scheduler.admit("trigger", message(11), 1)
        reference = scheduler.select(1)
        self.assertEqual(scheduler.inbox("state").pull()["sequence"], 2)
        scheduler.complete(reference, 1)

    def test_failed_callback_cancels_deferred_pull_and_keeps_push(self):
        scheduler = self.scheduler(dict(trigger=dict(root="Value"), inbox=dict(root="Value", mode="pull")),
                                   policies=dict(inbox=dict(capacity=1, overflow="wait")))
        scheduler.admit("trigger", message(10), 0)
        scheduler.admit("inbox", message(1), 0)
        reference = scheduler.select(0)
        self.assertEqual(scheduler.admit("inbox", message(2), 1), "full")
        self.assertEqual(scheduler.inbox("inbox").pull()["sequence"], 1)
        self.assertEqual(scheduler.admit("inbox", message(2), 2), "full")
        self.assertIsNone(scheduler.inbox("inbox").get())
        scheduler.complete(reference, 2, success=False)
        self.assertEqual(scheduler.buffers["trigger"].reserved, set())
        retried = scheduler.select(2)
        self.assertEqual(retried.record["id"], "session:10")
        self.assertEqual(scheduler.inbox("inbox").pull()["sequence"], 1)
        self.assertEqual(scheduler.buffers["inbox"].store.size(), 1)
        scheduler.complete(retried, 2)
        self.assertEqual(scheduler.buffers["inbox"].store.size(), 0)
        self.assertEqual(scheduler.admit("inbox", message(2), 2), "accepted")

    def test_publication_rights_follow_the_single_input_event(self):
        scheduler = self.scheduler(dict(input=dict(root="Value", timeout=.01,
            activates=dict(message=["normal"], timeout=["alarm"]))), outputs=("normal", "alarm"))
        with self.assertRaises(RuntimeError):
            scheduler.authorize_output("normal")
        scheduler.admit("input", message(1), 0)
        reference = scheduler.select(0)
        scheduler.authorize_output("normal")
        with self.assertRaisesRegex(ValueError, "input:message"):
            scheduler.authorize_output("alarm")
        with self.assertRaises(ValueError):
            scheduler.inbox("input")
        scheduler.complete(reference, 0)
        reference = scheduler.select(10 * MS)
        self.assertTrue(reference.timed_out)
        self.assertIsNone(reference.record)
        scheduler.authorize_output("alarm")
        with self.assertRaisesRegex(ValueError, "input:timeout"):
            scheduler.authorize_output("normal")
        scheduler.complete(reference, 10 * MS)

    def test_inactivity_includes_processing_time_repeats_and_coalesces(self):
        scheduler = self.scheduler(dict(input=dict(root="Value", timeout=.01)),
                                   policies=dict(input=dict(buffer="latest")))
        scheduler.admit("input", message(1), 0)
        reference = scheduler.select(0)
        scheduler.complete(reference, 35 * MS)
        reference = scheduler.select(35 * MS)
        self.assertEqual((reference.event, reference.due_ns, reference.elapsed), ("timeout", 10 * MS, 3))
        self.assertIsNone(reference.record)
        scheduler.complete(reference, 35 * MS)
        self.assertEqual(scheduler.next_event(35 * MS), 40 * MS)
        reference = scheduler.select(40 * MS)
        self.assertEqual(reference.elapsed, 1)
        scheduler.complete(reference, 40 * MS)
        self.assertEqual(scheduler.buffers["input"].take()["id"], "session:1")

    def test_new_receipt_cancels_unstarted_timeouts_while_another_callback_runs(self):
        scheduler = self.scheduler(dict(work=dict(root="Value"), watched=dict(root="Value", timeout=.01)))
        scheduler.admit("work", message(10), 0)
        work = scheduler.select(0)
        scheduler.admit("watched", message(1), 12 * MS)
        scheduler.complete(work, 13 * MS)
        watched = scheduler.select(13 * MS)
        self.assertEqual((watched.port, watched.event), ("watched", "message"))
        scheduler.complete(watched, 13 * MS)
        self.assertEqual(scheduler.next_event(13 * MS), 22 * MS)

    def test_new_receipt_does_not_interrupt_a_timeout_that_has_already_started(self):
        scheduler = self.scheduler(dict(input=dict(root="Value", timeout=.01)))
        expired = scheduler.select(10 * MS)
        scheduler.admit("input", message(1), 12 * MS)
        self.assertIsNone(scheduler.select(12 * MS))
        scheduler.complete(expired, 13 * MS)
        arrived = scheduler.select(13 * MS)
        self.assertEqual(arrived.event, "message")
        scheduler.complete(arrived, 13 * MS)
        self.assertEqual(scheduler.next_event(13 * MS), 22 * MS)

    def test_full_new_publication_resets_inactivity_but_retries_do_not(self):
        scheduler = self.scheduler(dict(input=dict(root="Value", timeout=.01)),
                                   policies=dict(input=dict(capacity=1)))
        scheduler.admit("input", message(1), 0)
        self.assertEqual(scheduler.admit("input", message(2), 9 * MS), "full")
        self.assertEqual(scheduler.deadlines["input"], 19 * MS)
        self.assertEqual(scheduler.admit("input", message(2), 12 * MS), "full")
        self.assertEqual(scheduler.deadlines["input"], 19 * MS)
        reference = scheduler.select(12 * MS)
        scheduler.complete(reference, 13 * MS)
        self.assertEqual(scheduler.admit("input", message(2), 14 * MS), "accepted")
        self.assertEqual(scheduler.deadlines["input"], 19 * MS)
        self.assertEqual(scheduler.admit("input", message(1), 15 * MS), "duplicate")
        self.assertEqual(scheduler.deadlines["input"], 19 * MS)

    def test_periodic_timer_keeps_its_cadence_across_unrelated_callbacks(self):
        scheduler = self.scheduler(dict(feedback=dict(root="Value"), clock=dict(kind="timer",
            timer=dict(mode="periodic", delay=.01))))
        scheduler.admit("feedback", message(1), 5 * MS)
        work = scheduler.select(5 * MS)
        scheduler.complete(work, 7 * MS)
        self.assertEqual(scheduler.next_event(7 * MS), 10 * MS)
        timer = scheduler.select(35 * MS)
        self.assertEqual((timer.port, timer.due_ns, timer.elapsed), ("clock", 10 * MS, 3))
        scheduler.complete(timer, 35 * MS)
        self.assertEqual(scheduler.next_event(35 * MS), 40 * MS)

    def test_first_late_latest_receipt_is_activity_even_if_its_value_is_stale(self):
        scheduler = self.scheduler(dict(input=dict(root="Value", timeout=.01)),
                                   policies=dict(input=dict(buffer="latest")))
        scheduler.admit("input", message(5), 0)
        self.assertEqual(scheduler.admit("input", message(4), 9 * MS), "stale")
        self.assertEqual(scheduler.deadlines["input"], 19 * MS)
        self.assertEqual(scheduler.buffers["input"].take()["sequence"], 5)
        self.assertEqual(scheduler.admit("input", message(4), 12 * MS), "stale")
        self.assertEqual(scheduler.deadlines["input"], 19 * MS)

    def test_receipt_tracking_is_bounded_without_deduplicating_refused_messages(self):
        scheduler = self.scheduler(dict(input=dict(root="Value", timeout=.01)),
                                   policies=dict(input=dict(capacity=1)))
        for number in range(5000):
            scheduler.admit("input", message(number), number)
        self.assertEqual(len(scheduler._receipts), 4096)
        self.assertEqual(scheduler.buffers["input"].store.size(), 1)
        reference = scheduler.select(5000)
        scheduler.complete(reference, 5000)
        self.assertEqual(scheduler.admit("input", message(4999), 5001), "accepted")

    def test_one_shot_fires_once_when_late_and_can_be_rearmed_from_its_callback(self):
        scheduler = self.scheduler(dict(clock=dict(kind="timer", timer=dict(mode="one_shot", delay=.01))))
        timer = scheduler.select(35 * MS)
        self.assertEqual(timer.elapsed, 1)
        scheduler.arm("clock", 35 * MS, delay=.005)
        scheduler.complete(timer, 36 * MS)
        self.assertEqual(scheduler.next_event(36 * MS), 40 * MS)
        timer = scheduler.select(40 * MS)
        scheduler.complete(timer, 40 * MS)
        self.assertIsNone(scheduler.next_event(40 * MS))

    def test_manual_timer_and_disarm(self):
        scheduler = self.scheduler(dict(clock=dict(kind="timer",
            timer=dict(mode="periodic", delay=.01, start="manual"))))
        self.assertIsNone(scheduler.next_event(0))
        scheduler.arm("clock", 0)
        self.assertEqual(scheduler.next_event(0), 10 * MS)
        scheduler.disarm("clock", 5 * MS)
        self.assertIsNone(scheduler.select(20 * MS))

    def test_ties_are_stable_and_callbacks_cannot_overlap_or_reuse_references(self):
        scheduler = self.scheduler(dict(z=dict(root="Value"), a=dict(root="Value")))
        scheduler.admit("z", message(1), 0)
        scheduler.admit("a", message(2), 0)
        first = scheduler.select(0)
        self.assertEqual(first.port, "a")
        self.assertIsNone(scheduler.select(0))
        scheduler.complete(first, 0)
        with self.assertRaises(RuntimeError):
            scheduler.complete(first, 0)
        second = scheduler.select(0)
        self.assertEqual(second.port, "z")
        scheduler.complete(second, 0)

    def test_clock_range_invalid_arming_and_missing_inboxes(self):
        scheduler = self.scheduler(dict(clock=dict(kind="timer",
            timer=dict(mode="one_shot", delay=.01, start="manual"))))
        for value in (-1, True, 1.0, MAX_NS + 1):
            with self.subTest(time=value), self.assertRaises(ValueError):
                scheduler.select(value)
        with self.assertRaises(ValueError):
            scheduler.arm("clock", MAX_NS)
        scheduler.arm("clock", 0)
        with self.assertRaises(ValueError):
            scheduler.arm("clock", 1, delay=0)
        with self.assertRaises(ValueError):
            scheduler.admit("clock", message(1), 0)
        with self.assertRaises(ValueError):
            InputScheduler(dict(inputs=dict(input=dict(root="Value")), outputs={}), {})

    def test_periodic_deadlines_do_not_wrap_at_uint64_boundary(self):
        node = dict(inputs=dict(clock=dict(kind="timer", timer=dict(mode="periodic", delay=1e-9))), outputs={})
        scheduler = InputScheduler(node, {})
        scheduler.start(MAX_NS - 2)
        for now in (MAX_NS - 1, MAX_NS):
            self.assertEqual(scheduler.next_event(now - 1), now)
            reference = scheduler.select(now)
            scheduler.complete(reference, now)
        self.assertIsNone(scheduler.next_event(MAX_NS))
