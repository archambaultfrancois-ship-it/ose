"""Canonical logical waves, bounded parallel subprocess calculations and virtual time."""
import asyncio
from collections import deque
from dataclasses import dataclass, field
import hashlib
import json
from pathlib import Path
from graphlib import CycleError, TopologicalSorter
import signal
from ose.codec.sdl2 import SDL2
from ose.parameters.service import ParameterService
from ose.parameters.bindings import LocalBindings
from ose.execution.process import start_component, input_events, stop_component
from ose.execution.plan import require_v2, read_plan
from ose.recording.component import ComponentRecording
from ose.execution.simulation.clock import MAX_NS, VirtualClock, InputSchedule, seconds_ns
from ose.execution.simulation.router import Router
from ose.execution.simulation.scenario import read_scenario, ParameterEvent
from ose.execution.simulation.trace import Trace
from ose.execution.simulation.options import microstep_limit

DEFAULT_OUTPUT_LIMIT = 16 * 1024 * 1024


class MicrostepLimitError(RuntimeError):
    """Bounded same-time execution failed to reach quiescence."""
    def __init__(self, diagnostic):
        self.diagnostic = diagnostic
        path = diagnostic["activation_cycle"] or diagnostic["recent_activations"]
        super().__init__(f"microstep limit {diagnostic['max_microsteps']} reached at "
                         f"time_ns={diagnostic['time_ns']}, microstep={diagnostic['microstep']}; "
                         f"activation path: {' -> '.join(path)}; "
                         f"pending components: {', '.join(diagnostic['pending_activations'])}")


def topology(plan, *, microsteps=False):
    from ose.modeling.activation import activation_order
    activation_order(plan, microsteps=microsteps)
    return sorted(plan["components"])


@dataclass
class PendingTick:
    selected: dict
    outputs: deque = field(default_factory=deque)
    publication: object = None


class Simulation:
    def __init__(self, plan, duration, *, workers=1, scenario=None, events=None, trace=None,
                 max_tick_output_bytes=DEFAULT_OUTPUT_LIMIT, working_directory=None,
                 microsteps=False, max_microsteps=None):
        require_v2(plan)
        self.microstep_limit = microstep_limit(microsteps, max_microsteps)
        self.microstep_diagnostic = None
        self.recent_activations = deque(maxlen=64)
        if type(workers) is not int or workers <= 0:
            raise ValueError("workers must be a positive integer")
        if type(max_tick_output_bytes) is not int or max_tick_output_bytes <= 0:
            raise ValueError("max_tick_output_bytes must be a positive integer")
        self.plan, self.workers, self.limit = plan, workers, max_tick_output_bytes
        self.working_directory = working_directory
        self.horizon, self.clock = seconds_ns(duration, positive=True), VirtualClock()
        self.order = topology(plan, microsteps=microsteps)
        self.codec = SDL2(plan["sdl_root"])
        self.parameter_service = ParameterService(plan, self.codec)
        self.parameter_bindings = {name: LocalBindings(node, self.parameter_service) for name, node in plan["components"].items()}
        if scenario is not None and events is not None:
            raise ValueError("scenario and prevalidated replay events are mutually exclusive")
        # Replay supplies preflighted disk-backed events with exact nanosecond
        # timestamps, using the same injection and fingerprint checks as scenarios.
        self.events = (read_scenario(scenario, plan.get("external_topics", {}), self.codec, self.parameter_service)
                       if events is None else events)
        self.event_count = self.events.event_count if hasattr(self.events, "event_count") else len(self.events)
        if self.event_count is None:
            raise ValueError("simulation events must be bounded by the execution horizon")
        protected = (self.events.input_paths if hasattr(self.events, "input_paths")
                     else {event.path for event in self.events})
        if scenario is not None:
            protected.add(Path(scenario).resolve())
        status_path = (Path(plan["build_dir"]) / "last-status.json").resolve()
        if status_path in protected:
            raise ValueError("scenario inputs cannot use the last-status.json diagnostic path")
        protected.add(status_path)
        if trace is not None and Path(trace).resolve() in protected:
            raise ValueError("trace cannot overwrite scenario inputs")
        # Validate virtual deadlines before opening diagnostics or spawning processes.
        for node in plan["components"].values():
            from ose.modeling.activation import component_inputs
            if node.get("execution_model") != 2:
                raise ValueError("v2 plans require CH/2 components")
            component_inputs(node, compiled=True)
        for sub in plan["subscriptions"]:
            if sub["policy"]["overflow"] == "wait" and sub["policy"]["wait_timeout_ms"] * 1_000_000 > MAX_NS:
                raise ValueError("simulation admission wait exceeds uint64 nanoseconds")
        self.trace = Trace(self.clock, trace, microsteps=self.microstep_limit is not None)
        try:
            self.router = Router(plan, self.codec, self.clock, self.trace)
        except BaseException:
            self.trace.close()
            raise
        self.schedules = {name: InputSchedule(plan["components"][name], self.router.buffers[name], self.clock)
                          for name in self.order}
        self.input_services = {}
        from ose.execution.input_ipc import InputServices
        self.router.schedulers = {name: schedule.core for name, schedule in self.schedules.items()}
        self.input_services = {name: InputServices(schedule.core, lambda: self.clock.time_ns, trace=True)
                               for name, schedule in self.schedules.items()}
        self.processes, self.busy, self.external = {}, {}, []
        self.external_bytes = 0
        self.cursor, self.waves, self.ticks = 0, 0, 0
        self.reason, self.summary = "failed", None
        self.parameter_changes = 0
        self.parameter_scenario_events = 0

    async def _start(self, name):
        process = await start_component(self.plan["components"][name], self.parameter_bindings[name],
            ComponentRecording(self.codec),
            **({"working_directory": self.working_directory} if self.working_directory is not None else {}),
            inputs=self.input_services[name])
        self.processes[name] = process

    async def _calculate(self, name, selected, semaphore):
        async with semaphore:
            return await self._calculate_input(name, selected)

    async def _calculate_input(self, name, reference):
        outputs, size, success = deque(), 0, False
        schedule = self.schedules[name]
        try:
            async for port, payload, app_id in input_events(self.processes[name], schedule.node, reference,
                    schedule.tick_index, self.input_services[name],
                    **({"parameters": self.parameter_bindings[name]} if schedule.node.get("parameter_bindings") else {})):
                size += len(payload) + len(port.encode()) + 12
                if size > self.limit:
                    raise ValueError(f"{name}: callback outputs exceed max_tick_output_bytes={self.limit}")
                desc = schedule.node["outputs"][port]
                body = self.codec.wrap(desc["catalogue"], payload)
                self.codec.validate_only(desc["catalogue"], desc["root"], body)
                outputs.append((port, body, app_id))
            success = True
        finally:
            schedule.complete(reference, success=success)
        return outputs

    def _input_operations(self, name):
        for operation in self.input_services[name].operations:
            self.trace.emit("input_operation", component=name, **operation)
        self.input_services[name].operations.clear()

    async def _wave(self, selected):
        names = [name for name in self.order if name in selected]
        self.waves += 1
        self.trace.emit("wave", components=names)
        for name in names:
            self.schedules[name].tick_index += 1
            if self.microstep_limit is not None:
                self.recent_activations.append(f"{name}.{selected[name].port}:{selected[name].event}")
            snapshot = {selected[name].port: selected[name].record}
            self.trace.emit("tick", component=name, tick_index=self.schedules[name].tick_index,
                            inputs={port: None if record is None else record["id"] for port, record in snapshot.items()},
                            activation_event=selected[name].event, due_ns=selected[name].due_ns,
                            elapsed=selected[name].elapsed)
        for name in names:
            self.parameter_bindings[name].staged = {}
        semaphore = asyncio.Semaphore(self.workers)
        tasks = [asyncio.create_task(self._calculate(name, selected[name], semaphore)) for name in names]
        try:
            results = await asyncio.gather(*tasks)
        finally:
            for task in tasks:
                if not task.done():
                    task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
        for name in names:
            self._input_operations(name)
            bindings = self.parameter_bindings[name]
            for path, payload in bindings.staged.items():
                self.parameter_service.set(path, payload)
                self.parameter_changes += 1
                self.trace.emit('parameter', component=name, path=path, sha256=hashlib.sha256(payload).hexdigest())
            bindings.staged = None
        # No callback output becomes visible until every calculation in the wave succeeded.
        for name, outputs in zip(names, results):
            self.busy[name] = PendingTick(selected[name], deque(enumerate(outputs, 1)))

    async def _pump(self):
        changed = False
        processed = 0
        for publication in list(self.external):
            changed |= self.router.advance(publication)
            if not publication.pending:
                self.external.remove(publication)
                self.external_bytes -= len(publication.record["body"])
        for name in self.order:
            tick = self.busy.get(name)
            if tick is None:
                continue
            while True:
                if tick.publication is None and tick.outputs:
                    index, output = tick.outputs.popleft()
                    port, body = output[:2]
                    identity = f"{name}:{self.schedules[name].tick_index}:{index}"
                    tick.publication = self.router.publish(f"{name}.{port}", body, identity,
                        app_id=output[2])
                    changed = True
                    processed += 1
                    if processed % 64 == 0:
                        await asyncio.sleep(0)
                if tick.publication is not None:
                    changed |= self.router.advance(tick.publication)
                    if tick.publication.pending:
                        break
                    tick.publication = None
                if not tick.outputs:
                    del self.busy[name]
                    self.ticks += 1
                    self.trace.emit("finished", component=name, tick_index=self.schedules[name].tick_index)
                    changed = True
                    break
        return changed

    async def _inject(self):
        while self.cursor < self.event_count and self.events[self.cursor].at == self.clock.time_ns:
            event = self.events[self.cursor]
            if isinstance(event, ParameterEvent):
                self.parameter_scenario_events += 1
                self.parameter_service.set(event.parameter, event.payload)
                self.parameter_changes += 1
                self.trace.emit('parameter', component='scenario', path=event.parameter, sha256=hashlib.sha256(event.payload).hexdigest())
                self.cursor += 1
                if self.cursor % 64 == 0:
                    await asyncio.sleep(0)
                continue
            publication = self.router.publish(event.topic, event.read(), f"scenario:{event.index}", app_id=event.app_id)
            self.router.advance(publication)
            if publication.pending:
                self.external_bytes += len(publication.record["body"])
                if self.external_bytes > self.limit:
                    raise ValueError("pending scenario publications exceed max_tick_output_bytes")
                self.external.append(publication)
            self.cursor += 1
            if self.cursor % 64 == 0:
                await asyncio.sleep(0)

    def _next_event(self):
        times = []
        if self.cursor < self.event_count:
            times.append(self.events[self.cursor].at)
        for name, schedule in self.schedules.items():
            if name not in self.busy and (timestamp := schedule.next_event()) is not None:
                times.append(timestamp)
        publications = self.external + [tick.publication for tick in self.busy.values() if tick.publication is not None]
        for publication in publications:
            times.extend(publication.pending.values())
        return min(times) if times else None

    def _snapshot(self):
        waiting = [f"{name}.{port}" for name, schedule in self.schedules.items()
                   for port, desc in schedule.node["inputs"].items()
                   if desc.get("kind") != "timer" and desc.get("mode", "push") != "pull"
                   and (port not in schedule.buffers or schedule.buffers[port].take() is None)]
        return dict(mode="simulated", functional=True, transport_simulation=False, running=False,
                    **({"microstep": self.clock.microstep, "max_microsteps": self.microstep_limit,
                        **({"microstep_diagnostic": self.microstep_diagnostic} if self.microstep_diagnostic else {})}
                       if self.microstep_limit is not None else {}),
                    time_ns=self.clock.time_ns, ticks=self.ticks, waves=self.waves, publications=self.router.sequence,
                    stop_reason=self.reason, waiting_inputs=waiting, pending_ticks=len(self.busy),
                    pending_external=len(self.external), external_events=self.cursor - self.parameter_scenario_events, parameter_changes=self.parameter_changes,
                    inputs={name: {port: buffer.snapshot() for port, buffer in buffers.items()}
                            for name, buffers in self.router.buffers.items()}, trace_sha256=self.trace.digest.hexdigest())

    def _check_microstep_limit(self):
        if self.microstep_limit is None or self.clock.microstep < self.microstep_limit:
            return
        pending = {}
        for name in self.order:
            if name in self.busy:
                continue
            candidates = sorted(self.schedules[name].core._candidates(self.clock.time_ns), key=lambda item: item[:3])
            if candidates:
                pending[name] = [dict(input=port, event=event, due_ns=due,
                    **({"handle": record["id"], "source": record["topic"]} if record is not None else {}))
                    for due, port, event, record in candidates]
        if not pending:
            return
        from ose.modeling.activation import activation_graph
        graph = activation_graph(self.plan)
        cycle = []
        try:
            tuple(TopologicalSorter({key: sorted(graph[key]) for key in sorted(graph)}).static_order())
        except CycleError as error:
            cycle = error.args[1]
        self.microstep_diagnostic = dict(time_ns=self.clock.time_ns, microstep=self.clock.microstep,
            max_microsteps=self.microstep_limit, activation_cycle=cycle,
            recent_activations=list(self.recent_activations), pending_activations=pending,
            pending_deliveries=[dict(handle=publication.record["id"], subscriptions=sorted(publication.pending))
                for publication in self.external + [tick.publication for tick in self.busy.values() if tick.publication is not None]])
        self.reason = "microstep_limit"
        self.trace.emit("microstep_limit", diagnostic=self.microstep_diagnostic)
        raise MicrostepLimitError(self.microstep_diagnostic)

    async def execute(self):
        starting = []
        try:
            if self.plan.get('shared_parameters'):
                for name in self.order:
                    await asyncio.wait_for(self._start(name), 20)
            else:
                starting = [asyncio.create_task(self._start(name)) for name in self.order]
                await asyncio.wait_for(asyncio.gather(*starting), 20)
            self.trace.emit("start", duration_ns=self.horizon, functional=True)
            for name in self.order:
                self._input_operations(name)
            for path, payload in sorted(self.parameter_service.values.items()):
                self.trace.emit('parameter_initial', path=path, sha256=hashlib.sha256(payload).hexdigest())
            while self.clock.time_ns < self.horizon:
                await self._inject()
                while await self._pump():
                    await asyncio.sleep(0)
                for name, process in self.processes.items():
                    if process.returncode is not None:
                        raise RuntimeError(f"component {name} stopped unexpectedly")
                self._check_microstep_limit()
                selected = {name: snapshot for name, schedule in self.schedules.items()
                            if name not in self.busy and (snapshot := schedule.select()) is not None}
                if selected:
                    await self._wave(selected)
                    if self.microstep_limit is not None:
                        self.clock.microstep += 1
                    continue
                timestamp = self._next_event()
                if timestamp is None:
                    self.reason = "quiescent"
                    break
                if timestamp <= self.clock.time_ns:
                    raise RuntimeError("simulation made no logical progress")
                self.clock.time_ns = min(timestamp, self.horizon)
                self.clock.microstep = 0
                self.recent_activations.clear()
                await asyncio.sleep(0)  # Allow cancellation even in long, callback-free scenarios.
            else:
                self.reason = "duration"
        except asyncio.CancelledError:
            self.reason = "interrupted"
            raise
        finally:
            for task in starting:
                if not task.done():
                    task.cancel()
            await asyncio.gather(*starting, return_exceptions=True)
            cleanup = await asyncio.gather(*(stop_component(p) for p in self.processes.values()), return_exceptions=True)
            cleanup_failed = any(isinstance(result, BaseException) for result in cleanup)
            if cleanup_failed:
                self.reason = "failed"
            self.trace.emit("stop", reason=self.reason)
            self.summary = self._snapshot()
            try:
                (Path(self.plan["build_dir"]) / "last-status.json").write_text(json.dumps(self.summary, indent=2) + "\n")
            finally:
                self.trace.close()
                self.router.close()
                self.parameter_service.close()
                self.busy.clear()
                self.external.clear()
                self.external_bytes = 0
            if cleanup_failed:
                raise RuntimeError("component cleanup failed")
        return self.summary


async def run(plan_path, duration, **options):
    plan_path = Path(plan_path).resolve()
    if options.get("trace") is not None and Path(options["trace"]).resolve() == plan_path:
        raise ValueError("trace cannot overwrite the compiled plan")
    engine = Simulation(read_plan(plan_path), duration, **options)
    from ose.execution.identity import identify
    identify("sim", engine.plan)
    loop, interrupted = asyncio.get_running_loop(), False
    task = asyncio.create_task(engine.execute())
    def stop():
        nonlocal interrupted
        interrupted = True
        task.cancel()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, stop)
    try:
        return await task
    except asyncio.CancelledError:
        if not interrupted:
            raise
        return engine.summary
    finally:
        for sig in (signal.SIGINT, signal.SIGTERM):
            loop.remove_signal_handler(sig)
