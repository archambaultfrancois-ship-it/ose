"""Exact virtual deadlines and the shared component readiness rules."""
from ose.timing import MAX_NS, seconds_ns


class VirtualClock:
    def __init__(self):
        self.time_ns = 0
        self.microstep = 0

    def seconds(self):
        return self.time_ns / 1_000_000_000


class InputSchedule:
    """V2 scheduling with the same parent-owned core as real-time adapters."""
    def __init__(self, node, buffers, clock):
        from ose.execution.activation import InputScheduler
        self.node, self.buffers, self.clock = node, buffers, clock
        self.core = InputScheduler(node, buffers)
        self.core.start(clock.time_ns)
        self.tick_index = 0

    def select(self):
        return self.core.select(self.clock.time_ns)

    def complete(self, reference, *, success=True):
        self.core.complete(reference, self.clock.time_ns, success=success)

    def next_event(self):
        return self.core.next_event(self.clock.time_ns)
