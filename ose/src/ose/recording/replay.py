"""Preflight recordings into immutable, disk-backed simulation inputs."""
from ose.execution.plan import read_plan
from collections import Counter
from contextlib import contextmanager
import hashlib
import importlib
import json
from pathlib import Path
import sys
import tempfile

from ose.codec.sdl2 import SDL2
from ose.execution.ipc import MAX_SIZE
from ose.execution.simulation.clock import MAX_NS, seconds_ns
from ose.execution.simulation.scenario import Event
from ose.recording.schema_adapter import SchemaAdapters
from ose.delivery.messages import validate_app_id


def recorded_app_id(record):
    value = record.metadata.get("ose.app_id")
    if value is None:
        return None
    try:
        result = validate_app_id(int(value))
        if str(result) != value:
            raise ValueError("noncanonical integer")
        return result
    except (TypeError, ValueError) as error:
        raise ValueError("recorded ose.app_id must be a canonical uint64 integer") from error


def loop_count(value):
    if type(value) is not int or value < 0:
        raise ValueError("loops must be a nonnegative integer (0 means infinite replay)")
    return value


class RepeatedEvents:
    """Reuse the same envelopes, shifting only scheduling times and input IDs."""
    def __init__(self, events, loops=1, *, horizon=None):
        self.events, self.loops = events, loop_count(loops)
        self.period_ns = events[-1].at + 1 if events else 1
        repetitions = loops or None
        if horizon is not None:
            # Include an event at/beyond the exclusive horizon so unbounded
            # replay ends with the simulation duration, rather than quiescence.
            bound = horizon // self.period_ns + 1
            repetitions = min(repetitions, bound) if repetitions is not None else bound
        self.event_count = len(events) * repetitions if repetitions is not None else None
        if not events:
            self.event_count = 0

    @property
    def input_paths(self):
        return {event.path for event in self.events}

    def __getitem__(self, index):
        if index < 0 or (self.event_count is not None and index >= self.event_count):
            raise IndexError(index)
        cycle, position = divmod(index, len(self.events))
        event = self.events[position]
        if cycle == 0:
            return event
        return Event(event.at + cycle * self.period_ns, cycle * len(self.events) + event.index,
                     event.topic, event.path, event.digest, event.app_id)

    def __iter__(self):
        index = 0
        while self.event_count is None or index < self.event_count:
            yield self[index]
            index += 1


def topic_mapping(values, topics):
    """Validate explicit mappings; None selects matching names during preflight."""
    if values is None:
        if not topics:
            raise ValueError("automatic replay requires declared external topics in the plan")
        return None
    result = {}
    for value in values:
        source, separator, target = value.partition("=")
        if not separator or not source or not target:
            raise ValueError("--map expects RECORDED_TOPIC=EXTERNAL_TOPIC")
        if source in result:
            raise ValueError(f"duplicate recorded topic mapping: {source}")
        if target not in topics:
            raise ValueError(f"replay target must be a declared external topic: {target}")
        if target in result.values():
            raise ValueError(f"multiple recorded topics map to {target}")
        result[source] = target
    if not result:
        raise ValueError("replay requires at least one topic mapping")
    return result


def recording_reader(codec):
    dependencies = codec.root / "build/recording-deps"
    if dependencies.is_dir():
        sys.path.insert(0, str(dependencies))
    try:
        return importlib.import_module("sdl_recording").RecordingReader
    except ImportError as error:
        raise ValueError("replay requires SDL recording services; run make -C "
                         f"{codec.root} recording-deps with the same Python interpreter") from error


@contextmanager
def recording_events(path, topics, mapping, codec, *, start_ns=None, end_ns=None, schema_policy='exact'):
    automatic = mapping is None
    if automatic:
        mapping = {topic: topic for topic in topics}
        if not mapping:
            raise ValueError("automatic replay requires declared external topics in the plan")
    adapters = SchemaAdapters(codec, schema_policy)
    for name, value in (("start-ns", start_ns), ("end-ns", end_ns)):
        if value is not None and (type(value) is not int or not 0 <= value <= MAX_NS):
            raise ValueError(f"{name} must be an unsigned 64-bit nanosecond timestamp")
    if start_ns is not None and end_ns is not None and end_ns < start_ns:
        raise ValueError("end-ns must be at or after start-ns")
    reader = recording_reader(codec)(path)
    with tempfile.TemporaryDirectory(prefix="ose-replay-") as temporary:
        events, counts, schemas = [], Counter(), {}
        unchanged, adapted = 0, 0
        # Exhaust the reader before starting components: this checks the entire
        # container, including CRCs and EOF, even for a narrow selection.
        for record in reader.iter_records(topics=mapping, start_time_ns=start_ns, end_time_ns=end_ns):
            source = record.channel.topic
            target = mapping[source]
            desc = topics[target]
            try:
                app_id = recorded_app_id(record)
                if record.channel.root_type != desc["root"]:
                    raise ValueError('recorded root does not match the external topic')
                if len(record.payload) + codec.api.ENVELOPE_HEADER_SIZE > MAX_SIZE:
                    raise ValueError('replay envelope exceeds the component frame limit')
                adapter = adapters.get(record.channel.catalogue, desc['catalogue'], desc['root'])
                body = adapter.adapt(record.payload)
                if len(body) > MAX_SIZE:
                    raise ValueError('adapted replay envelope exceeds the component frame limit')
            except ValueError as error:
                raise ValueError(f"{source} -> {target}, root {desc['root']}, selected message {len(events) + 1}, "
                                 f"log_time_ns {record.log_time_ns}: {error}") from error
            source_hash, target_hash = adapter.source_sha256, adapter.target_sha256
            key = source, target, source_hash, target_hash
            if key not in schemas:
                schemas[key] = dict(source_topic=source, target_topic=target, root=desc['root'],
                    source_sha256=source_hash, target_sha256=target_hash,
                    adaptation='exact' if adapter.exact else 'adapted', message_count=0)
            schemas[key]['message_count'] += 1
            unchanged += int(adapter.exact)
            adapted += int(not adapter.exact)
            index = len(events)
            envelope = Path(temporary) / f"{index}.s2ps"
            envelope.write_bytes(body)
            events.append(Event(record.log_time_ns, index, target, envelope, hashlib.sha256(body).hexdigest(), app_id))
            counts[source] += 1
        if automatic:
            mapping = {source: target for source, target in mapping.items() if source in counts}
            if not mapping:
                raise ValueError("no recorded messages match declared external topic names in the selected range; "
                                 "use --map RECORDED_TOPIC=EXTERNAL_TOPIC to select or rename topics")
        missing = sorted(set(mapping) - counts.keys())
        if missing:
            raise ValueError("no selected messages for mapped topics: " + ", ".join(missing))
        origin = min(event.at for event in events)
        events = sorted((Event(event.at - origin, event.index, event.topic, event.path, event.digest, event.app_id)
                         for event in events), key=lambda event: (event.at, event.index))
        yield events, {"source": str(Path(path).resolve()), "mapping": mapping,
                       "origin_log_time_ns": origin, "message_count": len(events),
                       "message_count_by_topic": dict(sorted(counts.items())),
                       "schema_policy": schema_policy,
                       "schema_adaptation": dict(unchanged_messages=unchanged, adapted_messages=adapted,
                                                 catalogues=list(schemas.values()))}


async def run(path, plan_path, duration, *, mappings=None, start_ns=None, end_ns=None,
              workers=1, trace=None, loops=1, schema_policy='exact', microsteps=False, max_microsteps=None,
              interactive=False, commands=None, output=None):
    from ose.execution.simulation import run as simulate
    from ose.execution.simulation.options import microstep_limit
    microstep_limit(microsteps, max_microsteps)
    horizon = seconds_ns(duration, positive=True)
    loop_count(loops)
    if type(workers) is not int or workers <= 0:
        raise ValueError("workers must be a positive integer")
    plan_path = Path(plan_path).resolve()
    path = Path(path).resolve()
    plan = read_plan(plan_path)
    diagnostic = (Path(plan["build_dir"]) / "last-status.json").resolve()
    if path in (plan_path, diagnostic):
        raise ValueError("recording cannot use the compiled plan or last-status.json path")
    if trace is not None and Path(trace).resolve() in (path, plan_path, diagnostic):
        raise ValueError("trace cannot overwrite the recording, compiled plan or last-status.json")
    topics = plan.get("external_topics", {})
    mapping = topic_mapping(mappings, topics)
    codec = SDL2(plan["sdl_root"])
    with recording_events(path, topics, mapping, codec, start_ns=start_ns, end_ns=end_ns,
                          schema_policy=schema_policy) as (events, selection):
        repeated = RepeatedEvents(events, loops, horizon=horizon)
        from ose.recording.control import ReplayControl
        control = (ReplayControl("simulated", interactive=interactive, commands=commands, output=output,
                                 microsteps=microsteps)
                   if interactive or commands is not None else None)
        summary = await simulate(plan_path, duration, workers=workers, events=repeated, trace=trace,
            **({"controller": control} if control is not None else {}),
            **({"microsteps": microsteps, "max_microsteps": max_microsteps}
               if microsteps or max_microsteps is not None else {}))
        summary.update(loops=loops, completed_loops=summary["external_events"] // len(events))
        selection.update(loops=loops, loop_period_ns=repeated.period_ns)
    summary["replay"] = selection
    return summary
