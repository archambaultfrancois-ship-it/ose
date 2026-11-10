"""Integer virtual deadlines and timestamped CH/2 input activations."""
import io
import unittest
from ose.delivery.buffers import Buffer
from ose.delivery.policies import SubscriptionPolicy
from ose.delivery.stores import MemoryStore
from ose.execution.input_process import InputSession
from ose.execution.ipc import frame
from ose.execution.simulation.clock import seconds_ns, VirtualClock, InputSchedule
from tests.component_driver import timer_frame


class SimulationClockTests(unittest.TestCase):
    def test_decimal_rounding_range_and_invalid_times(self):
        self.assertEqual(seconds_ns(.1), 100_000_000)
        self.assertEqual(seconds_ns(.0000000005), 1)
        self.assertEqual(seconds_ns(1e-15, positive=True), 1)
        self.assertEqual(seconds_ns(0), 0)
        for value in (True, None, "1", -1, float("nan"), float("inf"), 10 ** 1000, 1e30):
            with self.subTest(value=str(value)[:30]), self.assertRaises(ValueError):
                seconds_ns(value)
        with self.assertRaises(ValueError):
            seconds_ns(0, positive=True)

    def test_timestamped_session_rejects_time_reversal_and_old_ticks(self):
        data = frame("I", b"{}") + timer_frame(1, time_ns=123456789) + timer_frame(2, time_ns=123456789) + frame("X")
        writer = io.BytesIO()
        session = InputSession(io.BytesIO(data), writer)
        seen = []
        for reference in session.activations():
            seen.append((reference.port, session.time_ns, session.tick_index))
        self.assertEqual(seen, [("clock", 123456789, 1), ("clock", 123456789, 2)])
        self.assertEqual(writer.getvalue(), frame("R", b"\2") + frame("F") * 2)
        for second in (timer_frame(2, time_ns=123456788), timer_frame(3, time_ns=123456789),
                       frame("T"), frame("T", b"x" * 16)):
            session = InputSession(io.BytesIO(frame("I", b"{}") + timer_frame(1, time_ns=123456789) + second), io.BytesIO())
            with self.subTest(second=second), self.assertRaises(ValueError):
                list(session.activations())

    def test_exact_periods_and_passive_latest_quiescence(self):
        clock = VirtualClock()
        node = dict(execution_model=2, inputs={"clock": dict(kind="timer", timer=dict(mode="periodic", delay=.1))}, outputs={})
        schedule = InputSchedule(node, {}, clock)
        self.assertIsNone(schedule.select())
        for index in range(1, 1001):
            self.assertEqual(schedule.next_event(), index * 100_000_000)
            clock.time_ns = schedule.next_event()
            reference = schedule.select()
            self.assertEqual((reference.port, reference.event, reference.elapsed), ("clock", "timeout", 1))
            schedule.complete(reference)
        clock = VirtualClock()
        buffer = Buffer(SubscriptionPolicy.resolve(dict(buffer="latest")), MemoryStore(1), clock.seconds)
        node = dict(execution_model=2, inputs={"state": dict(root="Value", mode="pull"),
                    "clock": dict(kind="timer", timer=dict(mode="one_shot", delay=.1))}, outputs={})
        schedule = InputSchedule(node, {"state": buffer}, clock)
        schedule.core.admit("state", dict(id="1", epoch="sim", sequence=1), 0)
        self.assertIsNone(schedule.select())
        clock.time_ns = schedule.next_event()
        reference = schedule.select()
        self.assertEqual(schedule.core.inbox("state").get()["id"], "1")
        schedule.complete(reference)
        self.assertIsNone(schedule.select())
        self.assertIsNone(schedule.next_event())
        self.assertEqual(buffer.take()["id"], "1")

    def test_timeout_does_not_wait_for_another_input_or_spin(self):
        clock = VirtualClock()
        buffers = {port: Buffer(SubscriptionPolicy(), MemoryStore(), clock.seconds) for port in ("a", "b")}
        node = dict(execution_model=2, inputs={"a": dict(root="Value", timeout=.1), "b": dict(root="Value")}, outputs={})
        schedule = InputSchedule(node, buffers, clock)
        self.assertEqual(schedule.next_event(), 100_000_000)
        clock.time_ns = schedule.next_event()
        reference = schedule.select()
        self.assertEqual((reference.port, reference.event), ("a", "timeout"))
        schedule.complete(reference)
        self.assertIsNone(schedule.select())
        self.assertEqual(schedule.next_event(), 200_000_000)
        clock.time_ns = 150_000_000
        schedule.core.admit("b", dict(id="1", epoch="sim", sequence=1), clock.time_ns)
        reference = schedule.select()
        self.assertEqual((reference.port, reference.event), ("b", "message"))
        schedule.complete(reference)
        self.assertEqual(schedule.next_event(), 200_000_000)
