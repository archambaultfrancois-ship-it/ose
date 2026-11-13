"""Canonical logical waves, bounded parallel subprocess calculations and virtual time."""
import asyncio
from contextlib import ExitStack
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
from ose.execution.process import start_component, calculate, stop_component, DEFAULT_OUTPUT_LIMIT
from ose.execution.plan import require_v2, read_plan
from ose.recording.component import ComponentRecording
from ose.execution.simulation.clock import MAX_NS, VirtualClock, InputSchedule, seconds_ns
from ose.execution.simulation.router import Router
from ose.execution.simulation.scenario import read_scenario, ParameterEvent
from ose.execution.simulation.trace import Trace
from ose.execution.simulation.options import microstep_limit


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
                 microsteps=False, max_microsteps=None, controller=None, record=None, record_file=None,
                 record_from=None, record_until=None, disabled=()):
        require_v2(plan)
        from ose.execution.components import disabled_components
        self.disabled = disabled_components(plan, disabled)
        self.controller = controller
        self.microstep_limit = microstep_limit(microsteps, max_microsteps)
        if controller is not None:
            if controller.mode != "simulated":
                raise ValueError("simulation controls require simulated mode")
            controller.microsteps = self.microstep_limit is not None
        self.microstep_diagnostic = None
        self.recent_activations = deque(maxlen=64)
        if type(workers) is not int or workers <= 0:
            raise ValueError("workers must be a positive integer")
        if type(max_tick_output_bytes) is not int or max_tick_output_bytes <= 0:
            raise ValueError("max_tick_output_bytes must be a positive integer")
        self.plan, self.workers, self.limit = plan, workers, max_tick_output_bytes
        self.working_directory = working_directory
        if duration is None and events is None:
            raise ValueError('simulated run requires a duration; only replay may omit it')
        self.horizon = seconds_ns(duration, positive=True) if duration is not None else None
        self.clock = VirtualClock()
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
        from ose.recording.batch import destination, SimulatedCapture, CaptureWindow
        CaptureWindow(record, record_from, record_until)
        destination(record, record_file, protected=[*protected, trace])
        self.capture = (SimulatedCapture(plan, self.codec, self.clock, record, record_file,
            protected=[*protected, trace], microsteps=microsteps,
            record_from=record_from, record_until=record_until) if record else None)
        self.diagnostics = {name: [] for name in self.order}
        self.input_recorders = {}
        self.trace = Trace(self.clock, trace, microsteps=self.microstep_limit is not None)
        try:
            self.router = Router(plan, self.codec, self.clock, self.trace)
            self.router.capture = self.capture
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
        for name in self.disabled:
            self.schedules[name].core.enable(False, self.clock.time_ns)
        self.processes, self.busy, self.external = {}, {}, []
        self.external_bytes = 0
        self.cursor, self.waves, self.ticks = 0, 0, 0
        self.reason, self.summary = "failed", None
        self.parameter_changes = 0
        self.parameter_scenario_events = 0

    async def _start(self, name):
        if name in self.disabled:
            return
        capture = self.capture
        async def declare(local, desc):
            capture.declare(name, local, desc)
        def emit(local, desc, payload):
            self.diagnostics[name].append((f'{name}.record.{local}', self.codec.wrap(desc['catalogue'], payload)))
        if capture is not None:
            from ose.recording.inputs import InputRecording, input_key
            self.input_recorders[name] = InputRecording(self.codec, self.plan['components'][name],
                lambda: [port for port in self.plan['components'][name]['inputs']
                         if input_key(self.plan, name, port) in capture.channels],
                lambda port, desc, envelope, index, **extra: capture.emit(
                    input_key(self.plan, name, port), envelope, **extra))
        recording = (ComponentRecording(self.codec, declare=declare, active=lambda: capture.active(name), emit=emit)
                     if capture is not None else ComponentRecording(self.codec))
        process = await start_component(self.plan["components"][name], self.parameter_bindings[name],
            recording,
            **({"working_directory": self.working_directory} if self.working_directory is not None else {}),
            inputs=self.input_services[name])
        self.processes[name] = process

    async def component_command(self, operation, names):
        if operation not in ('status', 'enable', 'disable'):
            raise ValueError('expected component status, enable or disable')
        from ose.execution.components import disabled_components
        disabled_components(self.plan, names)
        if operation != 'status':
            if not names:
                raise ValueError('select at least one component')
            for name in names:
                enabled = operation == 'enable'
                self.schedules[name].core.enable(enabled, self.clock.time_ns)
                if enabled:
                    self.disabled.discard(name)
                    if name not in self.processes:
                        await self._start(name)
                else:
                    self.disabled.add(name)
                self.trace.emit('component_state', component=name, enabled=enabled)
        return {name: dict(active=name not in self.disabled) for name in names or self.order}

    async def _calculate(self, name, selected, semaphore):
        async with semaphore:
            return await self._calculate_input(name, selected)

    async def _calculate_input(self, name, reference):
        schedule = self.schedules[name]
        return await calculate(self.processes[name], schedule.node, reference, schedule.tick_index,
            self.input_services[name], self.codec, lambda: self.clock.time_ns,
            parameters=self.parameter_bindings[name], limit=self.limit)

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
            if self.capture is not None:
                self.input_recorders[name].observe(snapshot, self.schedules[name].tick_index)
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
            if self.capture is not None:
                for topic, body in self.diagnostics[name]:
                    self.capture.emit(topic, body)
                self.diagnostics[name].clear()
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

    def _has_events(self):
        return self.event_count is None or self.cursor < self.event_count

    async def _inject(self):
        while self._has_events() and self.events[self.cursor].at == self.clock.time_ns:
            if self.controller is not None and self.controller.quota == 0:
                break
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
            if self.controller is not None and self.controller.quota is not None:
                self.controller.quota -= 1
                self.controller.units += 1
            if self.cursor % 64 == 0:
                await asyncio.sleep(0)

    def _next_event(self):
        times = []
        if self._has_events():
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
                    component_states={name: dict(active=name not in self.disabled) for name in self.order},
                    time_ns=self.clock.time_ns, ticks=self.ticks, waves=self.waves, publications=self.router.sequence,
                    stop_reason=self.reason, waiting_inputs=waiting, pending_ticks=len(self.busy),
                    pending_external=len(self.external), external_events=self.cursor - self.parameter_scenario_events, parameter_changes=self.parameter_changes,
                    inputs={name: {port: buffer.snapshot() for port, buffer in buffers.items()}
                            for name, buffers in self.router.buffers.items()}, trace_sha256=self.trace.digest.hexdigest())

    def _control_snapshot(self):
        snapshot = self._snapshot()
        return {**snapshot, "published": snapshot["external_events"],
                "delivery_results": dict(self.router.external_deliveries),
                "pending_timestamp_group": (self._has_events()
                    and self.events[self.cursor].at == self.clock.time_ns)}

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
            if self.capture is not None:
                self.capture.open()
            self.trace.emit("start", duration_ns=self.horizon, functional=True)
            for name in self.order:
                self._input_operations(name)
            for path, payload in sorted(self.parameter_service.values.items()):
                self.trace.emit('parameter_initial', path=path, sha256=hashlib.sha256(payload).hexdigest())
            control = self.controller
            if control is not None:
                control.component_command = self.component_command
                control.position = lambda: self.clock.time_ns
                control.snapshot = self._control_snapshot
                control.start()
                await asyncio.sleep(0)
            while self.horizon is None or self.clock.time_ns < self.horizon:
                if control is not None:
                    if not await control.ready():
                        self.reason = "stopped"
                        break
                    if control.breakpoints and min(control.breakpoints) <= self.clock.time_ns:
                        control.hit_breakpoint()
                        continue
                    if control.target is not None and self.clock.time_ns >= control.target:
                        control.complete("time")
                        continue
                if control is None:
                    await self._inject()
                else:
                    async with control.busy:
                        before_injection = self.cursor
                        await self._inject()
                        if self.cursor != before_injection:
                            control.time_entered = True
                # An exact message budget may divide one timestamp's external
                # group. Do not select callbacks until the group is complete.
                if (control is not None and control.quota == 0 and self._has_events()
                        and self.events[self.cursor].at == self.clock.time_ns):
                    control.complete(pending_timestamp_group=True)
                    continue
                while await self._pump():
                    if control is not None:
                        control.time_entered = True
                    await asyncio.sleep(0)
                if control is not None and control.instant_quota == 0:
                    control.complete()
                    continue
                for name, process in self.processes.items():
                    if process.returncode is not None:
                        raise RuntimeError(f"component {name} stopped unexpectedly")
                if control is not None and not await control.ready():
                    self.reason = "stopped"
                    break
                self._check_microstep_limit()
                selected = {name: snapshot for name, schedule in self.schedules.items()
                            if name not in self.busy and (snapshot := schedule.select()) is not None}
                if selected:
                    if control is None:
                        await self._wave(selected)
                        if self.microstep_limit is not None:
                            self.clock.microstep += 1
                    else:
                        async with control.busy:
                            control.time_entered = True
                            await self._wave(selected)
                            if self.microstep_limit is not None:
                                self.clock.microstep += 1
                            if control.micro_quota is not None:
                                while await self._pump():
                                    await asyncio.sleep(0)
                    if control is not None and control.micro_quota is not None:
                        control.micro_quota -= 1
                        control.units += 1
                        if control.micro_quota == 0:
                            control.complete("microstep")
                    continue
                if control is not None and control.quota == 0:
                    control.complete(pending_timestamp_group=False)
                    continue
                if control is not None and control.micro_quota is not None:
                    control.complete("blocked")
                    continue
                logical_timestamp = timestamp = self._next_event()
                if control is not None:
                    boundary = control.boundary()
                    if timestamp is not None and boundary is not None:
                        timestamp = min(timestamp, boundary)
                    elif timestamp is None and control.breakpoints:
                        timestamp = boundary
                if timestamp is None:
                    self.reason = "quiescent"
                    break
                if timestamp <= self.clock.time_ns:
                    raise RuntimeError("simulation made no logical progress")
                next_time = timestamp if self.horizon is None else min(timestamp, self.horizon)
                if next_time > MAX_NS:
                    raise ValueError('simulation time exceeds uint64 nanoseconds')
                self.clock.time_ns = next_time
                if (control is not None and control.instant_quota is not None
                        and self.clock.time_ns == logical_timestamp
                        and (self.horizon is None or self.clock.time_ns < self.horizon)):
                    control.instant_quota -= 1
                    control.units += 1
                self.clock.microstep = 0
                if control is not None:
                    control.time_entered = False
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
            if self.controller is not None:
                await self.controller.finish(self.reason)
                await self.controller.close()
            cleanup = await asyncio.gather(*(stop_component(p) for p in self.processes.values()), return_exceptions=True)
            cleanup_failed = any(isinstance(result, BaseException) for result in cleanup)
            if cleanup_failed:
                self.reason = "failed"
            self.trace.emit("stop", reason=self.reason)
            self.summary = self._snapshot()
            if self.controller is not None:
                self.summary.update(position_ns=self.clock.time_ns, paused=self.controller.paused,
                                    last_command=self.controller.last_command,
                                    breakpoints_ns=sorted(self.controller.breakpoints),
                                    last_breakpoint_ns=self.controller.last_breakpoint)
            try:
                (Path(self.plan["build_dir"]) / "last-status.json").write_text(json.dumps(self.summary, indent=2) + "\n")
            finally:
                with ExitStack() as closing:
                    closing.callback(self.parameter_service.close)
                    closing.callback(self.router.close)
                    closing.callback(self.trace.close)
                    if self.capture is not None:
                        closing.callback(self.capture.close)
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
    from ose.recording.batch import destination
    destination(options.get('record'), options.get('record_file'), protected=[plan_path])
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
