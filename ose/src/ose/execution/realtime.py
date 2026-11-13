"""One component execution engine, independent of local/distributed transports."""
import asyncio
import time

from ose.delivery.buffers import Buffer, input_buffers
from ose.delivery.policies import SubscriptionPolicy
from ose.delivery.stores import MemoryStore
from ose.execution.activation import InputScheduler
from ose.execution.components import ComponentRuntime
from ose.execution.input_ipc import InputServices
from ose.execution.logs import ComponentLogs
from ose.execution.observation import ReportSchedule, TickTimings
from ose.execution.process import calculate, start_component
from ose.parameters.bindings import updates


class RealtimeRuntime(ComponentRuntime):
    """Transport endpoints supply recording_endpoint and publish(topic, envelope).

    The launcher/endpoint releases its readiness barrier before _input_work.
    Only this engine selects real-time activations and drives component IPC.
    """
    def setup_execution(self, subscriptions, *, working_directory=None):
        self.working_directory = working_directory
        max_wait = max((sub['policy']['wait_timeout_ms'] for sub in self.plan['subscriptions']
                        if sub['from'].split('.')[0] == self.name and sub['policy']['overflow'] == 'wait'), default=0)
        # Control polls wait up to 10 s server-side; keep the RPC budget above
        # that bound and include configured outbound admission waits.
        self.request_timeout = 15 + max_wait / 1000
        self.activity = asyncio.Event()
        self.reports = ReportSchedule()
        self.component_logs = ComponentLogs(on_change=self.reports.wake)
        self.catalogues, self.compatibility = {}, set()
        self.buffers = {target.split('.')[1]: buffer for target, buffer in
                        input_buffers(subscriptions, self._changed).items()}
        for port, desc in self.node['inputs'].items():
            if desc.get('kind') != 'timer' and port not in self.buffers:
                buffer = Buffer(SubscriptionPolicy(), MemoryStore())
                buffer.store.on_change = self._changed
                self.buffers[port] = buffer
        self.scheduler = InputScheduler(self.node, self.buffers)
        self.scheduler.start()
        self.input_services = InputServices(self.scheduler, self._input_now, on_change=self._changed)
        self.timings = TickTimings()
        self.tick_index, self.activated_at_ns = 0, 0
        self.closing = False
        self.setup_component_control()

    def _changed(self):
        self.activity.set()
        self.reports.mark()

    def _input_now(self):
        return time.monotonic_ns() - self.activated_at_ns if self.activated_at_ns else 0

    async def decode_input(self, port, body, request, source=None):
        identity = self.codec.api.unpack_envelope(body)[0]
        catalogue = source['catalogue'] if source is not None else None
        if catalogue is None or identity != self.codec.identity(catalogue):
            if identity not in self.catalogues:
                _, schema = await request('catalogue', catalogue_id=identity)
                catalogue = schema.decode('utf-8')
                if self.codec.identity(catalogue) != identity:
                    raise ValueError('catalogue identity mismatch')
                if len(self.catalogues) >= 64:
                    evicted = next(iter(self.catalogues))
                    self.catalogues.pop(evicted)
                    self.compatibility = {key for key in self.compatibility if key[0] != evicted}
                self.catalogues[identity] = catalogue
            catalogue = self.catalogues[identity]
        desc = self.node['inputs'][port]
        key = (identity, port)
        if key not in self.compatibility:
            self.codec.compare(catalogue, desc['catalogue'], desc['root'])
            self.compatibility.add(key)
        self.codec.validate_only(catalogue, (source or desc)['root'], body)
        return catalogue

    async def admit_input(self, port, record, *, version=0, authorize=None):
        buffer = self.buffers[port]
        deadline = time.monotonic() + buffer.policy.wait_timeout_ms / 1000
        while True:
            buffer.store.changed.clear()
            if authorize is not None:
                await authorize()
            if version != (self.component_version or 0):
                raise ValueError('obsolete target activation')
            result = self.scheduler.admit(port, record, self._input_now())
            self._changed()
            if result != 'full' or buffer.policy.overflow != 'wait' or time.monotonic() >= deadline:
                return result
            try:
                await asyncio.wait_for(buffer.store.changed.wait(), max(0, deadline - time.monotonic()))
            except asyncio.TimeoutError:
                return 'full'

    def close_execution(self):
        for buffer in self.buffers.values():
            buffer.store.close()
        self.catalogues.clear()
        self.compatibility.clear()

    async def initialize_component(self):
        self.process = await start_component(self.node, self.parameters, self.recording_endpoint, self.component_logs,
            **({'working_directory': self.working_directory} if self.working_directory is not None else {}),
            inputs=self.input_services)
        self.process.measurements.on_change = self.reports.mark

    async def _input_work(self):
        while not self.closing:
            self.activity.clear()
            async with self.component_lock:
                if self.component_active and self.node.get('parameter_bindings'):
                    await updates(self.process, self.node, self.parameters.drain())
                now = self._input_now()
                reference = self.scheduler.select(now)
                if self.component_active and reference is not None:
                    await self._callback(reference)
                    continue
                deadline = self.scheduler.next_event(now)
                timeout = max(0, (deadline - now) / 1e9) if deadline is not None else None
            try:
                await asyncio.wait_for(self.activity.wait(), timeout)
            except asyncio.TimeoutError:
                pass

    async def _callback(self, reference):
        self.tick_index += 1
        self.metrics['ticks'] = self.tick_index
        self.reports.mark()
        self.input_recording.observe({reference.port: reference.record}, self.tick_index)
        outputs = await calculate(self.process, self.node, reference, self.tick_index,
            self.input_services, self.codec, self._input_now, parameters=getattr(self, 'parameters', None),
            timings=self.timings)
        # Successful FINISHED commits withdrawals before outbound admissions.
        # The transport can wait without retaining a consumed inbox slot.
        for port, envelope, app_id in outputs:
            await self.publish(f'{self.name}.{port}', envelope, app_id=app_id)
        self.metrics['completed_ticks'] = self.metrics.get('completed_ticks', 0) + 1
        self.reports.mark()
