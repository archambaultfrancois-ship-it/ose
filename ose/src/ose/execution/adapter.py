"""Per-component process: transport delivery, scheduling and bounded IPC."""
import asyncio
import json
import sys
import time
from ose.codec.sdl2 import SDL2
from ose.delivery.buffers import Buffer, input_buffers
from ose.delivery.stores import MemoryStore
from ose.delivery.policies import SubscriptionPolicy
from ose.parameters.bindings import RemoteBindings
from ose.execution.process import start_component, input_events, stop_component
from ose.execution.plan import require_v2
from ose.modeling.activation import activation_order
from ose.execution.observation import TickTimings, ReportSchedule
from ose.transport.tcp import Client
from ose.transport.udp import UDP
from ose.recording.component import RemoteRecording
from ose.recording.inputs import InputRecording
from ose.execution.logs import ComponentLogs


class Adapter:
    def __init__(self, plan, name, *, working_directory=None):
        require_v2(plan)
        activation_order(plan)
        self.plan, self.name, self.node = plan, name, plan["components"][name]
        self.working_directory = working_directory
        self.reports = ReportSchedule()
        self.component_logs = ComponentLogs(on_change=self.reports.wake)
        self.codec = SDL2(plan["sdl_root"])
        self.component_recording = RemoteRecording(self.codec, self._recording_request, max_frame=plan["max_frame"])
        self.input_recording = InputRecording(self.codec, self.node,
            lambda: self.component_recording.active_inputs, self.component_recording.emit_input)
        self.subscriptions = {s["id"]: s for s in plan["subscriptions"] if s["to"].split(".")[0] == name}
        self.policies = {identity: SubscriptionPolicy.from_dict(sub["policy"])
                         for identity, sub in self.subscriptions.items()}
        self.catalogues, self.metrics = {}, {}
        self.compatibility = set()
        self.network_queues = {identity: asyncio.Queue(maxsize=1024) for identity in self.subscriptions}
        self.data_clients = {}
        self.activity = asyncio.Event()
        max_wait = max((sub["policy"]["wait_timeout_ms"] for sub in plan["subscriptions"]
                        if sub["from"].split(".")[0] == name and sub["policy"]["overflow"] == "wait"), default=0)
        self.request_timeout = 15 + max_wait / 1000
        self.buffers = {target.split(".")[1]: buffer for target, buffer in
                        input_buffers(self.subscriptions.values(), self._changed).items()}
        from ose.execution.activation import InputScheduler
        from ose.execution.input_ipc import InputServices
        for port, desc in self.node["inputs"].items():
            if desc.get("kind") != "timer" and port not in self.buffers:
                buffer = Buffer(SubscriptionPolicy(), MemoryStore())
                buffer.store.on_change = self._changed
                self.buffers[port] = buffer
        self.scheduler = InputScheduler(self.node, self.buffers)
        self.scheduler.start()
        self.input_services = InputServices(self.scheduler, self._input_now, on_change=self._changed)
        self.timings = TickTimings()
        self.started_at = time.monotonic()
        self.tick_index, self.activated_at_ns = 0, 0
        self.closing = False

    def _changed(self):
        self.activity.set()
        self.reports.mark()

    def _input_now(self):
        return time.monotonic_ns() - self.activated_at_ns if self.activated_at_ns else 0

    async def _recording_request(self, *args, **kwargs):
        return await self.client.request(*args, **kwargs)

    async def _tcp_data(self, header, body):
        # This callback runs on the subscription's data connection, never the control reader.
        await self.network_queues[header["subscription"]].put((header, body, None))

    async def _udp_data(self, header, body, address):
        if address != self.broker_udp or header["op"] != "data":
            raise ValueError("unexpected UDP peer/event")
        try:
            self.network_queues[header["subscription"]].put_nowait((header, body, address))
        except asyncio.QueueFull:
            self.metrics["network_refused"] = self.metrics.get("network_refused", 0) + 1
            self.reports.mark()

    async def _receiver(self, subscription):
        queue = self.network_queues[subscription]
        while not self.closing:
            header, body, address = await queue.get()
            try:
                if header["subscription"] != subscription:
                    raise ValueError("wrong subscription")
                sub = self.subscriptions[subscription]
                policy = self.policies[subscription]
                if header["topic"] != sub["from"]:
                    raise ValueError("wrong topic")
                port = sub["to"].split(".")[1]
                buffer = self.buffers[port]
                if (address is not None) != (policy.transport == "udp"):
                    raise ValueError("wrong data transport")
                if buffer.policy.buffer == "latest" and header["epoch"] != self.broker_epoch:
                    raise ValueError("obsolete broker epoch")
                identity = self.codec.api.unpack_envelope(body)[0]
                if identity not in self.catalogues:
                    _, raw_catalogue = await self.client.request("catalogue", catalogue_id=identity)
                    text = raw_catalogue.decode()
                    if self.codec.identity(text) != identity:
                        raise ValueError("catalogue identity mismatch")
                    if len(self.catalogues) >= 64:
                        evicted = next(iter(self.catalogues))
                        self.catalogues.pop(evicted)
                        self.compatibility = {key for key in self.compatibility if key[0] != evicted}
                    self.catalogues[identity] = text
                text = self.catalogues[identity]
                key = (identity, port)
                if key not in self.compatibility:
                    self.codec.compare(text, self.node["inputs"][port]["catalogue"], self.node["inputs"][port]["root"])
                    self.compatibility.add(key)
                self.codec.validate_only(text, self.node["inputs"][port]["root"], body)
                record = {"id": header["id"], "epoch": header["epoch"], "sequence": header["sequence"],
                          "topic": header["topic"],
                          "body": body, "payload": memoryview(body)[self.codec.api.ENVELOPE_HEADER_SIZE:], "catalogue": text}
                if "app_id" in header:
                    record["app_id"] = header["app_id"]
                from ose.execution.input_ipc import check_record_size
                check_record_size(port, record)
                deadline = time.monotonic() + buffer.policy.wait_timeout_ms / 1000
                while True:
                    buffer.store.changed.clear()
                    result = self.scheduler.admit(port, record, self._input_now())
                    self._changed()
                    if result != "full" or buffer.policy.overflow != "wait" or time.monotonic() >= deadline:
                        break
                    try:
                        await asyncio.wait_for(buffer.store.changed.wait(), max(0, deadline - time.monotonic()))
                    except asyncio.TimeoutError:
                        break
                if result in ("accepted", "duplicate", "stale") and policy.ack == "received":
                    ack = {"op": "ack", "subscription": sub["id"], "id": record["id"]}
                    if address is None:
                        await self.client.tcp.send(ack)
                    else:
                        await self.udp.send(ack, address=address)
            except (ValueError, KeyError) as error:
                self.metrics["invalid"] = self.metrics.get("invalid", 0) + 1
                self.reports.mark()
                print(f"{self.name}: rejected data: {error}", file=sys.stderr)
            # Cached catalogue/in-memory admissions may otherwise run without any suspension.
            await asyncio.sleep(0)

    def _snapshot(self):
        from ose.execution.identity import describe
        elapsed = max(time.monotonic() - self.started_at, .001)
        metrics = {"inputs": {p: b.snapshot() for p, b in self.buffers.items()},
            "process": describe("a", self.plan, self.name, self.process),
            "ticks_hz": self.metrics.get("ticks", 0) / elapsed,
            "publications_hz": self.metrics.get("publications", 0) / elapsed,
            "running": bool(self.activated_at_ns), **self.metrics, **self.timings.snapshot()}
        if hasattr(self, "component_recording"):
            metrics["recording"] = dict(self.component_recording.counts)
        metrics['measurements'] = self.process.measurements.snapshot()
        return metrics

    async def _send_report(self, full, timeout=None):
        metrics = self._snapshot() if full else ({'running': bool(self.activated_at_ns)} if self.reports.managed else {})
        metrics['log_records'] = self.process.logs.drain()
        metrics['logs_dropped'] = self.process.logs.dropped
        result, _ = await self.client.request("metrics", json.dumps(metrics).encode(),
            details=full, report_version=self.reports.version, timeout=timeout)
        self.reports.configure(result.get('reporting'))

    async def _report(self):
        while not self.closing:
            full = await self.reports.next(self.process.logs)
            if full is None:
                return
            await self._send_report(full)

    async def _work(self):
        while not self.closing:
            self.activity.clear()
            now = self._input_now()
            reference = self.scheduler.select(now)
            if reference is None:
                deadline = self.scheduler.next_event(now)
                timeout = max(0, (deadline - now) / 1e9) if deadline is not None else None
                try:
                    await asyncio.wait_for(self.activity.wait(), timeout)
                except asyncio.TimeoutError:
                    pass
                continue
            self.tick_index += 1
            self.reports.mark()
            self.input_recording.observe({reference.port: reference.record}, self.tick_index)
            outputs, size, success = [], 0, False
            try:
                async for port, payload, app_id in self.timings.events(input_events(
                        self.process, self.node, reference, self.tick_index, self.input_services,
                        **({"parameters": self.parameters} if self.node.get("parameter_bindings") else {}))):
                    size += len(payload) + len(port.encode()) + 12
                    if size > 16 * 1024 * 1024:
                        raise ValueError("callback outputs exceed 16 MiB")
                    desc = self.node["outputs"][port]
                    envelope = self.codec.wrap(desc["catalogue"], payload)
                    self.codec.validate_only(desc["catalogue"], desc["root"], envelope)
                    outputs.append((port, envelope, app_id))
                success = True
            finally:
                self.scheduler.complete(reference, self._input_now(), success=success)
            # FINISHED releases withdrawn inbox capacity before any outbound
            # admission wait, so feedback cannot hold its own consumed slot.
            for port, envelope, app_id in outputs:
                result, _ = await self.client.request("publish", envelope, timeout=self.request_timeout,
                    topic=f"{self.name}.{port}", **({"app_id": app_id} if app_id is not None else {}))
                self.metrics["publications"] = self.metrics.get("publications", 0) + 1
                refused = {key: value for key, value in result["delivery"].items()
                           if value not in ("accepted", "duplicate", "stale")}
                if refused:
                    self.metrics["delivery_refused"] = self.metrics.get("delivery_refused", 0) + len(refused)
                    print(f"{self.name}: delivery refused {refused}", file=sys.stderr)
            self.metrics["ticks"] = self.metrics.get("ticks", 0) + 1
            self.reports.mark()

    async def run(self, host, port):
        from ose.execution.identity import identify
        identify("a", self.plan, self.name)
        tasks = []
        try:
            self.udp = await UDP.bind(self._udp_data, self.plan["max_datagram"])
            self.client = await Client.connect(host, port, limit=self.plan["max_frame"], request_timeout=self.request_timeout)
            self.client.on_event = self.reports.event
            result, _ = await self.client.request("hello", node=self.name, udp_port=self.udp.address[1], data_channels=True, observe_metrics=True)
            self.reports.configure(result.get('reporting'))
            self.broker_udp = (host, result["udp_port"])
            self.broker_epoch = result["epoch"]
            for identity, sub in self.subscriptions.items():
                if sub["policy"]["transport"] == "tcp":
                    async def on_data(header, body, expected=identity):
                        if header["subscription"] != expected:
                            raise ValueError("wrong TCP data channel")
                        await self._tcp_data(header, body)
                    client = await Client.connect(host, port, on_data, self.plan["max_frame"])
                    self.data_clients[identity] = client
                    await client.request("attach", node=self.name, subscription=identity)
            self.parameters = await RemoteBindings(self.node, self.client.request, self.activity).open()
            self.process = await start_component(self.node, self.parameters, self.component_recording.endpoint, self.component_logs,
                **({"working_directory": self.working_directory} if self.working_directory is not None else {}),
                inputs=self.input_services)
            self.process.measurements.on_change = self.reports.mark
            result, _ = await self.client.request("ready")
            self.reports.configure(result.get('reporting'))
            # Supervisor activates delivery only after every component is ready.
            while True:
                status, _ = await self.client.request("state")
                if status["enabled"]:
                    break
                await asyncio.sleep(.02)
            self.activated_at_ns = time.monotonic_ns()
            tasks = [*(asyncio.create_task(self._receiver(identity)) for identity in self.subscriptions),
                     asyncio.create_task(self._work()), asyncio.create_task(self._report()), self.client.task,
                     *(client.task for client in self.data_clients.values()), asyncio.create_task(self.process.wait())]
            if self.parameters.task:
                tasks.append(self.parameters.task)
            if self.component_recording.endpoint.points or self.node["inputs"]:
                tasks.extend([asyncio.create_task(self.component_recording.poll()),
                              asyncio.create_task(self.component_recording.send())])
            done, _ = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
            for task in done:
                task.result()
            raise RuntimeError(f"component {self.name} stopped unexpectedly")
        finally:
            self.closing = True
            self.reports.close()
            if hasattr(self, 'component_recording'):
                self.component_recording.close()
            if hasattr(self, "parameters"):
                await self.parameters.close()
            reader = getattr(getattr(self, 'client', None), 'task', None)
            for task in tasks:
                if task is not reader:
                    task.cancel()
            await asyncio.gather(*(task for task in tasks if task is not reader), return_exceptions=True)
            if hasattr(self, "process") and self.process.returncode is None:
                await self._stop_component()
            if hasattr(self, 'client'):
                try:
                    if hasattr(self, 'process'):
                        await self._send_report(True, timeout=.5)
                    while self.component_logs.pending:
                        await self.client.request('metrics', json.dumps({'log_records': self.component_logs.drain(),
                            'logs_dropped': self.component_logs.dropped}).encode(), timeout=.5)
                except (OSError, EOFError, ValueError, asyncio.TimeoutError):
                    pass
            for client in self.data_clients.values():
                await client.close()
            if hasattr(self, "client"):
                await self.client.close()
            if hasattr(self, "udp"):
                await self.udp.close()
            for buffer in self.buffers.values():
                buffer.store.close()
            self.catalogues.clear()
            self.compatibility.clear()
            self.network_queues.clear()

    async def _stop_component(self):
        await stop_component(self.process)
