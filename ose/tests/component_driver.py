"""Drive generated timer callbacks through the real CH/2 scheduler and services."""
from ose.execution.activation import InputReference, InputScheduler
from ose.execution.input_ipc import InputServices, activation_data
from ose.execution.ipc import frame
from ose.execution.process import input_events, start_component


class ComponentDriver:
    def __init__(self, node):
        self.node, self.now, self.index = node, 0, 0
        self.scheduler = InputScheduler(node, {})
        self.scheduler.start(0)
        self.services = InputServices(self.scheduler, lambda: self.now)

    async def start(self, **options):
        return await start_component(self.node, inputs=self.services, **options)

    async def events(self, process, *, parameters=None):
        deadline = self.scheduler.next_event(self.now)
        if deadline is None:
            raise AssertionError("test component has no scheduled activation")
        self.now = deadline
        reference = self.scheduler.select(self.now)
        if reference is None:
            raise AssertionError("timer deadline did not select an activation")
        self.index += 1
        success = False
        try:
            async for item in input_events(process, self.node, reference, self.index,
                                           self.services, parameters=parameters):
                yield item
            success = True
        finally:
            self.scheduler.complete(reference, self.now, success=success)


def timer_frame(index, *, port="clock", time_ns=None):
    timestamp = index * 10_000_000 if time_ns is None else time_ns
    origin = InputReference(port, "timeout", timestamp, timestamp, 1, None, frozenset())
    return frame("T", activation_data(origin, index))


def message_frame(index, port, catalogue, body, *, app_id=None, time_ns=10_000_000,
                  handle="source:1", topic="source.out"):
    """Encode a Push activation; body is the complete SDL transport envelope."""
    record = dict(id=handle, topic=topic, catalogue=catalogue, body=body,
                  received_ns=time_ns)
    if app_id is not None:
        record["app_id"] = app_id
    origin = InputReference(port, "message", time_ns, time_ns, 1, record, frozenset())
    return frame("T", activation_data(origin, index))
