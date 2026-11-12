"""Inline batch capture selection and simulated/live recording lifecycles."""
import asyncio
from bisect import bisect_left, bisect_right
import math
import time
from fnmatch import fnmatchcase
from pathlib import Path

from ose.recording.filters import prepare_filter
from ose.recording.inputs import input_points
from ose.recording.replay import recording_reader


def destination(clauses, path=None, *, protected=()):
    if not clauses:
        if path is not None:
            raise ValueError('--record-file requires --record')
        return None
    target = Path(path or 'capture.pcapng').resolve()
    if target in {Path(value).resolve() for value in protected if value is not None}:
        raise ValueError('recording output cannot overwrite execution inputs or diagnostics')
    if target.suffix.lower() not in ('.pcapng', '.mcap'):
        raise ValueError('--record-file must end in .pcapng or .mcap')
    for clause in clauses:
        pattern, comma, expression = clause.partition(',')
        if not pattern.strip() or (comma and not expression.strip()):
            raise ValueError('--record expects TOPIC_GLOB[, FILTER]')
    return target


class CaptureWindow:
    """A half-open position window and its source-Unix-time admission intervals."""
    def __init__(self, clauses, start=None, end=None):
        from ose.recording.control import duration_ns
        if not clauses and (start is not None or end is not None):
            raise ValueError('--record-from and --record-until require --record')
        self.start = duration_ns(str(start), positive=False) if start is not None else 0
        self.end = duration_ns(str(end), positive=False) if end is not None else None
        if self.end is not None and self.end <= self.start:
            raise ValueError('--record-until must be greater than --record-from')
        self.enabled = start is not None or end is not None
        self.times, self.states = [], []

    def contains(self, position):
        return position >= self.start and (self.end is None or position < self.end)

    def update(self, position, rate, *, now=None):
        # Keep past transitions, replacing predictions when speed/pause/step changes.
        # Only window crossings are retained, not a sample for every publication.
        now = time.time_ns() if now is None else now
        index = bisect_left(self.times, now)
        del self.times[index:]
        del self.states[index:]
        state = self.contains(position)
        if not self.states or self.states[-1] != state:
            self.times.append(now)
            self.states.append(state)
        if rate > 0:
            for boundary, after in ((self.start, True), (self.end, False)):
                if boundary is not None and position < boundary:
                    self.times.append(now + math.ceil((boundary - position) / rate))
                    self.states.append(after)

    def accepts(self, record):
        if not self.enabled:
            return True
        index = bisect_right(self.times, record['publish_time_ns']) - 1
        return index >= 0 and self.states[index]


def select(clauses, points, *, sdl_root=None):
    """Validate each clause, then union overlapping clauses using SDL logical OR."""
    filters = {}
    for clause in clauses:
        pattern, comma, expression = clause.partition(',')
        pattern, expression = pattern.strip(), expression.strip()
        matches = [key for key, desc in points.items()
                   if fnmatchcase(desc.get('topic', key), pattern) or fnmatchcase(key, pattern)]
        if not matches:
            raise ValueError(f'no recording point matches {pattern!r}')
        for key in matches:
            if comma:
                predicate = prepare_filter(points[key], expression, sdl_root=sdl_root)
                predicate.close()
            filters.setdefault(key, []).append(expression if comma else None)
    return {key: None if None in values else ' || '.join(f'({value})' for value in dict.fromkeys(values))
            for key, values in filters.items()}


class SimulatedCapture:
    def __init__(self, plan, codec, clock, clauses, path, *, protected=(), microsteps=False, record_from=None, record_until=None):
        self.path = destination(clauses, path, protected=protected)
        self.window = CaptureWindow(clauses, record_from, record_until)
        self.plan, self.codec, self.clock = plan, codec, clock
        self.clauses, self.microsteps = clauses, microsteps
        self.points = {topic: dict(desc, topic=topic, kind='external')
                       for topic, desc in plan.get('external_topics', {}).items()}
        for name, node in plan['components'].items():
            self.points.update({f'{name}.{port}': dict(desc, topic=f'{name}.{port}', kind='output')
                                for port, desc in node['outputs'].items()})
            self.points.update(input_points(plan, name, 'simulated', True))
        self.filters, self.channels = {}, {}
        self.writer = None
        self.count = 0

    def declare(self, name, local, desc):
        topic = f'{name}.record.{local}'
        self.points[topic] = dict(desc, topic=topic, kind='internal')

    def active(self, name):
        prefix = f'{name}.record.'
        return [key[len(prefix):] for key in self.channels if key.startswith(prefix)]

    def open(self):
        try:
            selected = select(self.clauses, self.points, sdl_root=self.plan['sdl_root'])
            for key, expression in selected.items():
                if expression:
                    self.filters[key] = prepare_filter(self.points[key], expression, sdl_root=self.plan['sdl_root'])
            recording_reader(self.codec)  # Load the SDL service using this plan's checkout.
            from sdl_recording import RecordingWriter
            self.writer = RecordingWriter(self.path, format=self.path.suffix.lower()[1:], overwrite=True,
                metadata={'producer': 'ose-simulator', 'ose.clock': 'virtual',
                          'timestamps': 'log and publish: virtual deposit time in nanoseconds'})
            for key, expression in selected.items():
                desc = self.points[key]
                self.channels[key] = self.writer.register_channel(topic=desc['topic'], catalogue=desc['catalogue'],
                    root_type=desc['root'], metadata={'ose.kind': desc['kind'], 'ose.generation': 'simulated',
                                                     'ose.filter': expression or '', 'ose.clock': 'virtual'})
        except BaseException:
            self.close()
            raise

    def emit(self, topic, envelope, *, app_id=None):
        if topic not in self.channels or not self.window.contains(self.clock.time_ns):
            return
        _, payload = self.codec.api.unpack_envelope(envelope)
        predicate = self.filters.get(topic)
        if predicate:
            result = predicate.evaluate_wire(payload)
            if result.status == -1:
                raise ValueError(f'{topic}: {result.diagnostic}')
            if not result.matched:
                return
        metadata = {'ose.app_id': str(app_id)} if app_id is not None else {}
        if self.microsteps:
            metadata['ose.microstep'] = str(self.clock.microstep)
        self.writer.write_encoded(self.channels[topic], payload, log_time_ns=self.clock.time_ns,
            publish_time_ns=self.clock.time_ns, sequence=self.count % 2**32, metadata=metadata)
        self.count += 1

    def close(self):
        try:
            if self.writer is not None:
                writer, self.writer = self.writer, None
                writer.close()
        finally:
            for predicate in self.filters.values():
                predicate.close()
            self.filters.clear()


class LiveCapture:
    def __init__(self, clauses, path, *, host, port, context=None, distributed=False, sdl_root=None, protected=(),
                 record_from=None, record_until=None):
        self.path = destination(clauses, path, protected=protected)
        self.window = CaptureWindow(clauses, record_from, record_until)
        self.clauses, self.sdl_root = clauses, sdl_root
        self.recorder = None
        self.options = dict(host=host, port=port, context=context, distributed=distributed, overwrite=True)

    async def __aenter__(self):
        if self.path is None:
            return self
        if self.sdl_root is not None:
            from ose.codec.sdl2 import SDL2
            recording_reader(SDL2(self.sdl_root))
        from ose.recording.recorder import Recorder
        self.recorder = Recorder(**self.options)
        try:
            await self.recorder.connect()
            points = {key: await self.recorder.describe(key) for key in self.recorder.points}
            self.recorder.selected = select(self.clauses, points, sdl_root=self.sdl_root)
            await self.recorder.start(self.path, format=self.path.suffix.lower()[1:], accept=self.window.accepts)
        except BaseException:
            await self.recorder.close()
            raise
        return self

    def follow(self, session):
        def update():
            rate = (session.clock.speed if not session.paused and not session.stopped
                    and not session.counting else 0)
            self.window.update(session.position(), rate)
        if self.window.enabled:
            session.clock_observer = update
            update()

    def begin_run(self):
        if self.window.enabled:
            self.window.update(0, 1)

    async def watch(self, operation):
        """End a replay if its capture stops unexpectedly, including while paused."""
        if self.recorder is None:
            return await operation
        task = asyncio.ensure_future(operation)
        try:
            done, _ = await asyncio.wait([task, self.recorder.capture.task], return_when=asyncio.FIRST_COMPLETED)
            if self.recorder.capture.task in done:
                await self.recorder.capture.task
                raise ValueError(f'batch recording stopped: {self.recorder.capture.error or "unexpected end"}')
            return await task
        finally:
            if not task.done():
                task.cancel()
            await asyncio.gather(task, return_exceptions=True)

    async def __aexit__(self, kind, value, traceback):
        if self.recorder is not None:
            await self.recorder.close()
            capture = self.recorder.capture
            if kind is None and capture is not None:
                if capture.error:
                    raise ValueError(f'batch recording failed: {capture.error}')
                if self.recorder.status().get('capture_counts', {}).get('dropped', 0):
                    raise ValueError('batch recording lost messages: source capture queue overflow')
