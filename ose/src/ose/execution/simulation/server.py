"""Loopback RPC host for a single simulated session; the engine owns logical time.

Only committed engine snapshots are published. Network reads never drive the
scheduler. Commands carry an epoch and serialize against the wave barrier.
"""
import asyncio
from collections import OrderedDict
import copy
import hashlib
import json
from pathlib import Path
import signal
import uuid

from ose.execution.plan import read_plan
from ose.execution.simulation.coordinator import Simulation
from ose.execution.simulation.console import QuietOutput, SimulationConsole
from ose.execution.observation import describe, subscription_schema
from ose.execution.logs import LogStore
from ose.parameters.service import ParameterEndpoint, OPERATIONS
from ose.recording.control import ReplayControl
from ose.transport.endpoint import publish
from ose.transport.tcp import TCP, start_server

MAX_NS = 2**64 - 1


def nanoseconds(value):
    if not isinstance(value, str) or not value.isascii() or not value.isdecimal() or len(value) > 20:
        raise ValueError('time must be an unsigned decimal nanosecond string')
    result = int(value)
    if result > MAX_NS:
        raise ValueError('time exceeds uint64 nanoseconds')
    return result


class SimulationServer:
    def __init__(self, plan, duration, *, keep_alive=False, **options):
        self.plan, self.duration, self.options = plan, duration, dict(options)
        self.launch_control = self.options.pop('controller', None)
        self.console = self.console_task = None
        self.keep_alive = keep_alive
        self.server = self.task = self.engine = self.parameters = None
        self.connections = set()
        self.operations = OrderedDict()
        self.persistent_breakpoints = set()
        self.active = None
        self.lifecycle = asyncio.Lock()
        self.closed = asyncio.Event()
        self.ready = asyncio.Event()
        self.state, self.error = 'preparing', None
        self.cached = {}
        self.sequence = 0

    async def start(self, port=0):
        if type(port) is not int or not 0 <= port <= 65535:
            raise ValueError('port must be between 0 and 65535')
        # Construct/validate the engine before advertising an endpoint.
        self._prepare(initial=True)
        self.server = await start_server(self._connection, '127.0.0.1', port)
        self.port = self.server.sockets[0].getsockname()[1]
        publish(self.plan['build_dir'], self.port, mode='simulated', epoch=self.epoch)
        self.task = asyncio.create_task(self._execute())
        launch = self.launch_control
        if launch is not None and (launch.interactive or launch.commands is not None):
            self.console = SimulationConsole(self, commands=launch.commands, output=launch.output)
            self.console_task = asyncio.create_task(self.console.run())
        return self.port

    def _prepare(self, *, initial=False):
        options = dict(self.options)
        self.control = ReplayControl('simulated', replay=False, output=QuietOutput())
        self.control.breakpoints.update(self.persistent_breakpoints)
        if self.keep_alive or not initial or self.launch_control is not None:
            self.control.pause()
            self.control.pause_reason = 'initial'
        self.epoch = uuid.uuid4().hex
        if not initial:
            for key in ('trace', 'record_file'):
                path = options.get(key)
                if key == 'record_file' and not path and options.get('record'):
                    path = 'capture.pcapng'
                if path:
                    path = Path(path)
                    options[key] = path.with_name(path.stem + '-' + self.epoch[:12] + path.suffix)
        self.logs = LogStore(self.epoch)
        self.state, self.error = 'preparing', None
        self.ready.clear()
        self.cached = {}
        self.engine = Simulation(self.plan, self.duration, controller=self.control,
                                 observer=self._observe, **options)
        from ose.execution.telemetry import ProcessResources
        self.resources = {name: ProcessResources() for name in self.engine.order}
        self.parameters = ParameterEndpoint(self.plan, self.engine.codec, service=self.engine.parameter_service)

    def _observe(self, engine):
        self._drain_logs(engine)
        observation = describe(self.plan)
        rows = {}
        for name in engine.order:
            process = engine.processes.get(name)
            generation = self.epoch + ':' + name
            desc = observation['components'][name]
            present = process is not None or name in engine.disabled
            desc.update(connected=present, ready=present,
                        generation=generation, report_age_ms=0 if present else None)
            measurements = process.measurements.snapshot() if process is not None else {}
            pending = engine.busy.get(name)
            wait_ms = None
            if pending is not None and pending.publication is not None:
                publication = pending.publication
                starts = [deadline - engine.router.by_id[identity].policy.wait_timeout_ms * 1000000
                          for identity, deadline in publication.pending.items()]
                wait_ms = (engine.clock.time_ns - min(starts)) / 1e6 if starts else None
            queues = {port: buffer.snapshot() for port, buffer in engine.router.buffers[name].items()}
            waiting = [port for port, spec in engine.plan['components'][name]['inputs'].items()
                       if spec.get('kind') != 'timer' and spec.get('mode', 'push') != 'pull'
                       and engine.schedules[name].buffers.get(port) is not None
                       and engine.schedules[name].buffers[port].take() is None]
            rows[name] = dict(running=process is not None, inputs=queues,
                completed_ticks=max(0, engine.schedules[name].tick_index - int(pending is not None)),
                publications=engine.router.topic_counts_by_component.get(name, 0),
                measurements=measurements, waiting_inputs=waiting,
                publication_wait={'current_ms': wait_ms})
        self.sequence += 1
        self.cached = dict(epoch=self.epoch, mode='simulated', observation=observation,
            enabled=True, components=rows, subscriptions={},
            component_states={name: dict(active=name not in engine.disabled) for name in engine.order},
            parameters=engine.parameter_service.snapshot(), logs_version=1, measurements_version=1,
            simulation_time_ns=str(engine.clock.time_ns), simulation_microstep=engine.clock.microstep,
            snapshot_sequence=str(self.sequence))
        if self.state == 'preparing' and engine.initialized:
            self.state = 'ready'
            self.ready.set()

    def _drain_logs(self, engine):
        for name, process in engine.processes.items():
            while process.logs.pending:
                entries = process.logs.drain()
                for entry in entries:
                    entry['simulation_time_ns'] = str(engine.clock.time_ns)
                self.logs.append(name, self.epoch + ':' + name, entries)

    def execution(self):
        engine = self.engine
        terminal = self.state in ('finished', 'failed')
        return dict(epoch=self.epoch, mode='simulated',
            state=self.state if self.state != 'ready' else ('paused' if self.control.paused else 'running'),
            position_ns=self.cached.get('simulation_time_ns', '0'), horizon_ns=str(engine.horizon) if engine.horizon is not None else None,
            microstep=self.cached.get('simulation_microstep', 0), microsteps=engine.microstep_limit is not None,
            pause_reason=self.control.pause_reason,
            stop_reason=engine.reason if terminal else None, error=self.error,
            breakpoints_ns=[str(at) for at in sorted(self.control.breakpoints | self.persistent_breakpoints)],
            persistent_breakpoints_ns=[str(at) for at in sorted(self.persistent_breakpoints)],
            armed_breakpoints_ns=[str(at) for at in sorted(self.control.breakpoints)],
            active_operation=self.active,
            operations=[self._public_operation(o) for o in self.operations.values()][-20:],
            snapshot_sequence=str(self.sequence),
            capabilities={'control': True, 'reset': self.keep_alive, 'persistent_breakpoints': True, 'parameters': True,
                          'measurements': True, 'logs': True, 'recordings': False, 'live': False},
            semantics={'time_boundary': 'exclusive', 'rates_clock': 'simulated',
                       'measurements': 'latest values at committed wave boundaries'})

    def snapshot(self):
        result = copy.deepcopy(self.cached) | {'mode': 'simulated', 'epoch': self.epoch, 'execution': self.execution()}
        for name, row in result.get('components', {}).items():
            process = self.engine.processes.get(name)
            if process is not None and process.returncode is None:
                row['resources'] = self.resources[name].snapshot(process.pid)
            timing = self.engine.timings[name].snapshot()
            row.update({key: value for key, value in timing.items() if key != 'waiting_inputs'})
        return result

    async def _execute(self):
        try:
            await self.engine.execute()
            self.state = 'finished'
        except asyncio.CancelledError:
            raise
        except Exception as error:
            self.error, self.state = str(error)[:1000], 'failed'
        finally:
            self._drain_logs(self.engine)
            self.ready.set()

    async def _reset(self):
        self.state = 'resetting'
        if self.task and not self.task.done():
            self.task.cancel()
            await asyncio.gather(self.task, return_exceptions=True)
        self.parameters.close()
        try:
            self._prepare()
        except Exception as error:
            self.state, self.error = 'failed', str(error)[:1000]
            self.ready.set()
            raise
        publish(self.plan['build_dir'], self.port, mode='simulated', epoch=self.epoch)
        self.task = asyncio.create_task(self._execute())
        await self.ready.wait()
        if self.state == 'failed':
            raise ValueError(self.error)

    async def command(self, header):
        async with self.lifecycle:
            if header.get('epoch') != self.epoch:
                raise ValueError('simulation epoch changed; refresh before issuing a command')
            action = header.get('action')
            allowed = ('pause', 'resume', 'step_instants', 'step_duration', 'run_until',
                       'step_microsteps', 'break_add', 'break_remove', 'break_clear',
                       'component_set', 'reset', 'stop')
            if action not in allowed:
                raise ValueError('unknown simulation command')
            identity = header.get('command_id')
            if not isinstance(identity, str) or not 1 <= len(identity) <= 128:
                raise ValueError('command_id is required (at most 128 characters)')
            signature = {k: v for k, v in header.items() if k not in ('op', 'request')}
            existing = self.operations.get(identity)
            if existing:
                if existing['_request'] != signature:
                    raise ValueError('command_id was already used for another command')
                return self._public_operation(existing)
            if len(self.operations) >= 256:
                oldest = next(iter(self.operations))
                if self.operations[oldest]['status'] == 'running':
                    raise ValueError('simulation operation quota reached')
                self.operations.pop(oldest)
            if action == 'reset':
                if not self.keep_alive:
                    raise ValueError('reset requires an interactive simulation server')
                if self.active or self.state == 'preparing' or self.state == 'ready' and not self.control.paused:
                    raise ValueError('pause the simulation and wait for the current operation before resetting')
            else:
                if self.state != 'ready':
                    raise ValueError('simulation is not ready or has finished')
                if self.active and action not in ('pause', 'stop'):
                    raise ValueError('another simulation operation is running')
            line = None
            if action in ('pause', 'resume', 'stop'):
                line = action
            elif action in ('step_instants', 'step_microsteps'):
                count = header.get('count', 1)
                if type(count) is not int or not 1 <= count <= 1000000:
                    raise ValueError('step count must be between 1 and 1000000')
                if action == 'step_microsteps' and not self.control.microsteps:
                    raise ValueError('microsteps are not enabled')
                line = ('step' if action == 'step_instants' else 'microstep') + f' {count}'
            elif action in ('step_duration', 'run_until'):
                value = nanoseconds(header.get('time_ns'))
                if action == 'run_until':
                    value -= self.engine.clock.time_ns
                if value <= 0 or self.engine.clock.time_ns + value > MAX_NS:
                    raise ValueError('target must be strictly after the current time and within uint64')
                if self.engine.horizon is not None and self.engine.clock.time_ns + value > self.engine.horizon:
                    raise ValueError('target exceeds the simulation horizon')
                line = f'step {value}ns'
            elif action in ('break_add', 'break_remove'):
                value = nanoseconds(header.get('time_ns'))
                if action == 'break_add' and type(header.get('persistent', False)) is not bool:
                    raise ValueError('persistent must be a boolean')
                if action == 'break_add' and (value < self.engine.clock.time_ns or
                        value == self.engine.clock.time_ns and self.control.time_entered):
                    raise ValueError('breakpoint time has already passed or its events have started')
                line = ('break-time' if action == 'break_add' else 'clear-break-time') + f' {value}ns'
            elif action == 'break_clear':
                line = 'clear-break-time all'
            elif action == 'component_set':
                names, enabled = header.get('components'), header.get('enabled')
                if (not isinstance(names, list) or not names or type(enabled) is not bool
                        or not all(isinstance(name, str) and name in self.engine.order for name in names)):
                    raise ValueError('select known components and a boolean enabled state')
            if action.startswith('step_') or action == 'run_until':
                if not self.control.paused:
                    raise ValueError('pause the simulation before advancing')
            operation = dict(id=identity, epoch=self.epoch, action=action, status='running',
                             error=None, _request=signature)
            self.operations[identity] = operation
            if action not in ('pause', 'stop'):
                self.active = identity
            async def perform():
                try:
                    if action == 'reset':
                        await self._reset()
                    elif action == 'component_set':
                        self.control.pause()
                        async with self.control.busy:
                            states = await self.engine.component_command('enable' if enabled else 'disable', names)
                            self._observe(self.engine)
                        operation['result'] = {'components': states}
                    else:
                        if action == 'break_remove' and value in self.persistent_breakpoints and value not in self.control.breakpoints:
                            self.persistent_breakpoints.remove(value)
                        else:
                            await self.control.execute(line)
                        if action == 'break_add':
                            if header.get('persistent', False):
                                self.persistent_breakpoints.add(value)
                            else:
                                self.persistent_breakpoints.discard(value)
                        elif action == 'break_remove':
                            self.persistent_breakpoints.discard(value)
                        elif action == 'break_clear':
                            self.persistent_breakpoints.clear()
                        if action == 'pause':
                            self.control.complete('interrupted')
                        if action == 'stop' or (action.startswith('step_') or action == 'run_until') and self.control.finished:
                            await asyncio.shield(self.task)
                        if (action.startswith('step_') or action == 'run_until') and (self.control.last_command or {}).get('stop_reason') == 'failed':
                            raise ValueError('simulation failed before the requested advance completed')
                    interrupted = action.startswith('step_') or action == 'run_until'
                    if interrupted:
                        operation['stop_reason'] = (self.control.last_command or {}).get('stop_reason')
                    interrupted = interrupted and operation['stop_reason'] in ('interrupted', 'stopped')
                    operation.update(status='interrupted' if interrupted else 'complete', position_ns=str(self.engine.clock.time_ns))
                except asyncio.CancelledError:
                    operation.update(status='interrupted')
                    raise
                except Exception as error:
                    operation.update(status='failed', error=str(error)[:1000])
                finally:
                    if self.active == identity:
                        self.active = None
            task = asyncio.create_task(perform())
            operation['_task'] = task
            return self._public_operation(operation)

    async def wait_operation(self, identity):
        operation = self.operations[identity]
        await asyncio.shield(operation['_task'])
        return self._public_operation(operation)

    @staticmethod
    def _public_operation(operation):
        return {k: v for k, v in operation.items() if not k.startswith('_')}

    async def request(self, peer, header, body):
        op = header.get('op')
        if op == 'status':
            return self.snapshot(), None
        if op == 'simulation_command':
            return await self.command(header), None
        if op == 'simulation_operation':
            operation = self.operations.get(header.get('id'))
            if operation is None:
                raise ValueError('unknown simulation operation')
            return self._public_operation(operation), None
        if op == 'subscription_schema':
            return subscription_schema(self.plan, self.engine.router.subscriptions[header['subscription']], header.get('side', 'source'))
        if op == 'logs':
            return self.logs.query(**header.get('filters', {})), None
        if op in OPERATIONS:
            endpoint = self.parameters
            if op == 'parameters_set':
                async with self.control.busy:
                    if endpoint is not self.parameters or self.state != 'ready':
                        raise ValueError('simulation is not ready for parameter changes')
                    result = await endpoint.request(peer, header, body)
                    self.engine.parameter_changes += 1
                    self.engine.trace.emit('parameter', component='external', path=header['path'],
                                           sha256=hashlib.sha256(body).hexdigest())
                    self._observe(self.engine)
                    return result
            return await endpoint.request(peer, header, body)
        if op == 'component_status':
            names = header.get('components', self.engine.order)
            if not isinstance(names, list) or not all(isinstance(name, str) and name in self.engine.order for name in names):
                raise ValueError('select known components')
            return {name: self.cached.get('component_states', {}).get(name, {}) for name in names or self.engine.order}, None
        raise ValueError('operation is unavailable in simulated mode')

    async def _connection(self, reader, writer):
        current = asyncio.current_task()
        self.connections.add(current)
        peer = TCP(reader, writer)
        requests = set()
        async def reply(header, body):
            try:
                result, binary = await self.request(peer, header, body)
                response = dict(op='result', request=header.get('request'))
                if binary is None:
                    await peer.send({**response, 'content': 'json'}, json.dumps(result or {}, separators=(',', ':')).encode())
                else:
                    await peer.send({**result, **response}, binary)
            except (ValueError, KeyError, TypeError) as error:
                await peer.send(dict(op='result', request=header.get('request'), error=str(error)[:1000]))
        try:
            while True:
                header, body = await peer.receive()
                if len(requests) >= 16:
                    raise ValueError('too many concurrent simulation requests')
                task = asyncio.create_task(reply(header, body))
                requests.add(task)
                def completed(done):
                    requests.discard(done)
                    if not done.cancelled() and done.exception() is not None:
                        writer.close()
                task.add_done_callback(completed)
        except (asyncio.IncompleteReadError, ConnectionError, ValueError):
            pass
        finally:
            for task in requests:
                task.cancel()
            await asyncio.gather(*requests, return_exceptions=True)
            self.parameters.disconnect(peer)
            self.connections.discard(current)
            await peer.close()

    async def close(self):
        if self.console_task:
            self.console_task.cancel()
            await asyncio.gather(self.console_task, return_exceptions=True)
        if self.server:
            self.server.close()
        for operation in self.operations.values():
            task = operation.get('_task')
            if task and not task.done():
                task.cancel()
        await asyncio.gather(*(o['_task'] for o in self.operations.values() if '_task' in o), return_exceptions=True)
        if self.task and not self.task.done():
            self.task.cancel()
        if self.task:
            await asyncio.gather(self.task, return_exceptions=True)
        elif self.engine:
            self.engine.router.close()
            self.engine.trace.close()
            if self.engine.capture is not None:
                self.engine.capture.close()
        if self.parameters:
            self.parameters.close()
        for task in self.connections:
            task.cancel()
        await asyncio.gather(*self.connections, return_exceptions=True)
        if self.server:
            await self.server.wait_closed()
        self.closed.set()


async def serve(plan_path, duration, *, port=0, keep_alive=False, **options):
    plan_path = Path(plan_path).resolve()
    if options.get('trace') is not None and Path(options['trace']).resolve() == plan_path:
        raise ValueError('trace cannot overwrite the compiled plan')
    from ose.recording.batch import destination
    destination(options.get('record'), options.get('record_file'), protected=[plan_path])
    server = SimulationServer(read_plan(plan_path), duration, keep_alive=keep_alive, **options)
    from ose.execution.identity import identify
    identify('sim', server.plan)
    loop = asyncio.get_running_loop()
    stopped = asyncio.Event()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, stopped.set)
    waiter = None
    try:
        await server.start(port)
        waiter = asyncio.create_task(stopped.wait())
        if keep_alive:
            await waiter
        else:
            await asyncio.wait((server.task, waiter), return_when=asyncio.FIRST_COMPLETED)
        if stopped.is_set() and not server.task.done():
            server.task.cancel()
            await asyncio.gather(server.task, return_exceptions=True)
        if server.console is not None and server.console.commands is not None:
            await server.console_task
            if server.console.error is not None:
                raise ValueError(str(server.console.error))
        if server.state == 'failed':
            raise RuntimeError(server.error)
        return server.engine.summary
    finally:
        if waiter:
            waiter.cancel()
            await asyncio.gather(waiter, return_exceptions=True)
        await server.close()
        for sig in (signal.SIGINT, signal.SIGTERM):
            loop.remove_signal_handler(sig)
