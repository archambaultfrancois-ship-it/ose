"""Bounded local measurements; no clock synchronization or optional dependency."""
from collections import deque
from contextlib import contextmanager
import math
import os
from pathlib import Path
import time


class Durations:
    def __init__(self, clock=time.monotonic):
        self.clock = clock
        self.samples = deque(maxlen=256)

    def add(self, milliseconds):
        self.samples.append((self.clock(), max(0., milliseconds)))

    def snapshot(self):
        now = self.clock()
        while self.samples and now - self.samples[0][0] > 5:
            self.samples.popleft()
        values = sorted(value for _, value in self.samples)
        return dict(p95_ms=values[math.ceil(.95 * len(values)) - 1] if values else None,
                    max_ms=values[-1] if values else None, samples=len(values), window_seconds=5)


class ProcessResources:
    """Read each process on its own host; identify PID reuse by start time."""
    def __init__(self, clock=time.monotonic, proc=Path('/proc')):
        self.clock, self.proc, self.previous = clock, Path(proc), {}
        try:
            self.ticks = os.sysconf('SC_CLK_TCK')
            self.page_size = os.sysconf('SC_PAGE_SIZE')
        except (AttributeError, OSError, ValueError):
            self.ticks = self.page_size = None

    def snapshot(self, child=None):
        now, previous = self.clock(), self.previous
        self.previous = {}
        result = {}
        for role, pid in (('runtime', os.getpid()), ('component', child)):
            if pid is None:
                continue
            values = dict(pid=pid, cpu_percent=None, rss_bytes=None, cpu_span_seconds=None)
            try:
                fields = (self.proc / str(pid) / 'stat').read_text().rsplit(')', 1)[1].split()
                ticks, start, pages = int(fields[11]) + int(fields[12]), int(fields[19]), int(fields[21])
                if self.ticks and self.page_size:
                    values['rss_bytes'] = max(0, pages) * self.page_size
                    old = previous.get((pid, start))
                    if old is not None and now > old[0] and ticks >= old[1]:
                        values['cpu_span_seconds'] = now - old[0]
                        values['cpu_percent'] = (ticks - old[1]) / self.ticks / (now - old[0]) * 100
                    self.previous[pid, start] = now, ticks
            except (OSError, ValueError, IndexError):
                pass  # An exited process or inaccessible /proc must not fail execution.
            result[role] = values
        return result


class ExecutionTelemetry:
    def __init__(self, node, clock=time.monotonic):
        self.clock = clock
        self.timers = {port: Durations(clock) for port, desc in node['inputs'].items() if desc.get('kind') == 'timer'}
        self.input_wait = {port: Durations(clock) for port in node['inputs'] if port not in self.timers}
        self.coalesced = dict.fromkeys(self.timers, 0)
        self.publication = Durations(clock)
        self.publication_started, self.publication_calls, self.publication_total_ms = None, 0, 0.
        self.reads = set()
        self.resources = ProcessResources(clock)

    def activate(self, reference):
        self.reads.clear()
        if reference.port in self.timers and reference.event == 'timeout':
            self.timers[reference.port].add((reference.time_ns - reference.due_ns) / 1e6)
            self.coalesced[reference.port] += reference.elapsed - 1
        if reference.record is not None:
            self.read(reference.port, reference.record, reference.time_ns)

    def read(self, port, record, now_ns):
        identity = port, record['id']
        if identity not in self.reads:
            self.reads.add(identity)
            self.input_wait[port].add((now_ns - record['received_ns']) / 1e6)

    @contextmanager
    def publishing(self):
        self.publication_started = self.clock()
        try:
            yield
        finally:
            elapsed = (self.clock() - self.publication_started) * 1000
            self.publication.add(elapsed)
            self.publication_total_ms += elapsed
            self.publication_calls += 1
            self.publication_started = None

    def snapshot(self, child=None):
        return dict(
            timers={port: dict(values.snapshot(), coalesced=self.coalesced[port]) for port, values in self.timers.items()},
            input_wait={port: values.snapshot() for port, values in self.input_wait.items()},
            publication_wait=dict(self.publication.snapshot(), calls=self.publication_calls,
                total_ms=self.publication_total_ms,
                current_ms=(self.clock() - self.publication_started) * 1000 if self.publication_started is not None else None),
            resources=self.resources.snapshot(child))
