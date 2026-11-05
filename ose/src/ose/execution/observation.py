"""Bounded real-time observations, independent of delivery and logical time."""
from collections import deque
import asyncio
import math
import time


class ReportSchedule:
    """Coalesce dirty metrics, send logs promptly, and retain an idle heartbeat."""
    def __init__(self, interval=.5, heartbeat=1., clock=time.monotonic):
        self.interval, self.heartbeat, self.clock = interval, heartbeat, clock
        self.changed = None
        self.dirty, self.closed = True, False
        self.managed, self.initial = False, True
        self.version, self.observed_until = 0, 0.
        self.next_full = self.next_heartbeat = 0.

    def configure(self, state):
        # Missing negotiation retains the cadence expected by older servers.
        if state is None or self.closed:
            return
        if not isinstance(state, dict):
            raise ValueError('invalid reporting demand')
        version, seconds = state.get('version'), state.get('seconds')
        if (type(version) is not int or version < 0 or type(seconds) not in (int, float)
                or not math.isfinite(seconds) or seconds < 0):
            raise ValueError('invalid reporting demand')
        if version < self.version:
            return
        now = self.clock()
        active = not self.managed or now < self.observed_until
        if seconds and (version > self.version or not active):
            self.dirty = True
            self.next_full = 0.
        self.managed, self.version = True, version
        self.observed_until = now + seconds
        self.wake()

    def event(self, header, body):
        if header.get('event') == 'reporting':
            self.configure(header.get('reporting'))

    def mark(self):
        if not self.dirty:
            self.dirty = True
            self.wake()

    def wake(self):
        if self.changed is not None:
            self.changed.set()

    def close(self):
        self.closed = True
        self.wake()

    async def next(self, logs):
        """True: full snapshot; False: logs/presence only; None: shutdown."""
        if self.changed is None:
            self.changed = asyncio.Event()
        while not self.closed:
            self.changed.clear()
            now = self.clock()
            observed = not self.managed or now < self.observed_until
            if self.initial or observed and (now >= self.next_heartbeat or self.dirty and now >= self.next_full):
                self.initial = False
                self.dirty = False
                self.next_full, self.next_heartbeat = now + self.interval, now + self.heartbeat
                return True
            if not observed and (now >= self.next_heartbeat or logs.pending):
                self.next_heartbeat = now + self.heartbeat
                return False
            if logs.pending:
                return False
            deadline = min(self.next_full, self.next_heartbeat) if observed and self.dirty else self.next_heartbeat
            if observed and self.managed:
                deadline = min(deadline, self.observed_until)
            try:
                await asyncio.wait_for(self.changed.wait(), max(0, deadline - now))
            except asyncio.TimeoutError:
                pass
        return None


class ObservationDemand:
    """Bounded refresh leases shared by status readers; one notifier per runtime."""
    def __init__(self, lease=3., timeout=.75, clock=time.monotonic):
        self.lease, self.timeout, self.clock = lease, timeout, clock
        self.version, self.until = 0, 0.
        self.members, self.notified, self.notifications = {}, {}, {}
        self.changed = None
        self.closed = False

    def state(self):
        return dict(version=self.version, seconds=max(0., self.until - self.clock()))

    def register(self, peer):
        self.members[peer] = -1
        return self.state()

    def wake(self):
        if self.changed is not None:
            self.changed.set()

    def forget(self, peer):
        self.members.pop(peer, None)
        self.notified.pop(peer, None)
        task = self.notifications.get(peer)
        if task is not None:
            task.cancel()
        self.wake()

    def received(self, peer, version):
        if peer in self.members and type(version) is int and version == self.version:
            self.members[peer] = version
            self.wake()

    def _notify(self, peer):
        if self.notified.get(peer) == self.version or peer in self.notifications:
            return
        self.notified[peer] = self.version

        async def send():
            await peer.send(dict(op='event', event='reporting', reporting=self.state()))

        task = asyncio.create_task(send())
        self.notifications[peer] = task

        def completed(task):
            self.notifications.pop(peer, None)
            if not task.cancelled():
                task.exception()

        task.add_done_callback(completed)

    async def refresh(self, peers):
        if self.closed:
            return
        now = self.clock()
        if now >= self.until:
            self.version += 1
        self.until = now + self.lease
        version = self.version
        targets = set(peers).intersection(self.members)
        for peer in targets:
            if self.members[peer] < version:
                self._notify(peer)
        if self.changed is None:
            self.changed = asyncio.Event()
        deadline = self.clock() + self.timeout
        while not self.closed:
            self.changed.clear()
            if all(peer not in self.members or self.members[peer] >= version for peer in targets):
                return
            remaining = deadline - self.clock()
            if remaining <= 0:
                return
            try:
                await asyncio.wait_for(self.changed.wait(), remaining)
            except asyncio.TimeoutError:
                return

    async def close(self):
        self.closed = True
        self.wake()
        tasks = list(self.notifications.values())
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        self.members.clear()
        self.notified.clear()


def describe(plan):
    """Expose scheduling and queue policies, without commands, secrets or schemas."""
    return {
        "version": 1,
        "components": {name: {"trigger": node.get("trigger", {}), "inputs": {
            port: {key: desc[key] for key in ("root", "required", "timeout") if key in desc}
            for port, desc in node["inputs"].items()}, "outputs": {
            port: {"root": desc["root"]} for port, desc in node.get("outputs", {}).items()}}
            for name, node in plan["components"].items()},
        "external_topics": {topic: {"root": desc["root"]}
                            for topic, desc in plan.get("external_topics", {}).items()},
        "subscriptions": {sub["id"]: {key: sub[key] for key in ("from", "from_topic", "to", "policy", "routing") if key in sub}
                          for sub in plan["subscriptions"]},
    }


def subscription_schema(plan, sub, side="source"):
    """Describe either port contract of a declared subscription on demand."""
    if side == "destination":
        component, port = sub["to"].split(".")
        desc = plan["components"][component]["inputs"][port]
    elif side != "source":
        raise ValueError("subscription schema side must be source or destination")
    elif "from_topic" in sub:
        desc = plan["external_topics"][sub["from_topic"]]
    else:
        component, port = sub["from"].split(".")
        desc = plan["components"][component]["outputs"][port]
    return {"from": sub["from"], "to": sub["to"], "side": side, "root": desc["root"]}, desc["catalogue"].encode()


class TickTimings:
    """Wall time driving CH/1, excluding caller-side publication/delivery waits.

    Includes IPC and waiting for the component process; this is not CPU time.
    Percentiles cover at most 256 completed ticks from the last five seconds.
    """
    def __init__(self, clock=time.monotonic):
        self.clock = clock
        self.samples = deque(maxlen=256)
        self.started = None

    async def events(self, events):
        self.started = self.clock()
        elapsed = 0
        try:
            while True:
                start = self.clock()
                try:
                    event = await events.__anext__()
                except StopAsyncIteration:
                    elapsed += self.clock() - start
                    self.samples.append((self.clock(), elapsed * 1000))
                    return
                elapsed += self.clock() - start
                yield event
        finally:
            self.started = None
            await events.aclose()

    def snapshot(self, scheduler):
        now = self.clock()
        while self.samples and now - self.samples[0][0] > 5:
            self.samples.popleft()
        values = sorted(value for _, value in self.samples)
        return {"tick_p95_ms": values[math.ceil(.95 * len(values)) - 1] if values else None,
                "tick_samples": len(values), "tick_window_seconds": 5,
                "busy_ms": (now - self.started) * 1000 if self.started is not None else None,
                "waiting_inputs": [port for port, desc in scheduler.node["inputs"].items()
                    if desc.get("required", True) and scheduler.buffers.get(port) is not None
                    and scheduler.buffers[port].take() is None
                    and now < scheduler.deadlines.get(port, float("inf"))]
                    if self.started is None else []}
