"""Per-component process: transport delivery, scheduling and bounded IPC."""
import asyncio
import json
import sys
import time
from ose.codec.sdl2 import SDL2
from ose.delivery.policies import SubscriptionPolicy
from ose.parameters.bindings import RemoteBindings
from ose.execution.process import stop_component
from ose.execution.plan import require_v2
from ose.modeling.activation import activation_order
from ose.transport.tcp import Client
from ose.transport.udp import UDP
from ose.recording.component import RemoteRecording
from ose.recording.inputs import InputRecording


from ose.execution.realtime import RealtimeRuntime


class Adapter(RealtimeRuntime):
    def __init__(self, plan, name, *, working_directory=None):
        require_v2(plan)
        activation_order(plan)
        self.plan, self.name, self.node = plan, name, plan["components"][name]
        self.codec = SDL2(plan["sdl_root"])
        self.component_recording = RemoteRecording(self.codec, self._recording_request, max_frame=plan["max_frame"])
        self.input_recording = InputRecording(self.codec, self.node,
            lambda: self.component_recording.active_inputs, self.component_recording.emit_input)
        self.subscriptions = {s["id"]: s for s in plan["subscriptions"] if s["to"].split(".")[0] == name}
        self.policies = {identity: SubscriptionPolicy.from_dict(sub["policy"])
                         for identity, sub in self.subscriptions.items()}
        self.metrics = {}
        self.network_queues = {identity: asyncio.Queue(maxsize=1024) for identity in self.subscriptions}
        self.data_clients = {}
        self.setup_execution(self.subscriptions.values(), working_directory=working_directory)
        self.recording_endpoint = self.component_recording.endpoint
        self.started_at = time.monotonic()

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
                text = await self.decode_input(port, body, self.client.request)
                if header.get('target_version', 0) != (self.component_version or 0):
                    raise ValueError('obsolete target activation')
                record = {"id": header["id"], "epoch": header["epoch"], "sequence": header["sequence"],
                          "topic": header["topic"],
                          "body": body, "payload": memoryview(body)[self.codec.api.ENVELOPE_HEADER_SIZE:], "catalogue": text}
                if "app_id" in header:
                    record["app_id"] = header["app_id"]
                from ose.execution.input_ipc import check_record_size
                check_record_size(port, record)
                result = await self.admit_input(port, record, version=header.get('target_version', 0))
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
            "process": describe("a", self.plan, self.name, getattr(self, 'process', None)),
            "ticks_hz": self.metrics.get("ticks", 0) / elapsed,
            "publications_hz": self.metrics.get("publications", 0) / elapsed,
            "running": bool(self.activated_at_ns) and self.component_active, **self.metrics, **self.execution_snapshot()}
        if hasattr(self, "component_recording"):
            metrics["recording"] = dict(self.component_recording.counts)
        metrics['measurements'] = self.process.measurements.snapshot() if hasattr(self, 'process') else {}
        return metrics

    async def _send_report(self, full, timeout=None):
        metrics = self._snapshot() if full else ({'running': bool(self.activated_at_ns) and self.component_active} if self.reports.managed else {})
        metrics['log_records'] = self.component_logs.drain()
        metrics['logs_dropped'] = self.component_logs.dropped
        result, _ = await self.client.request("metrics", json.dumps(metrics).encode(),
            details=full, report_version=self.reports.version, timeout=timeout)
        self.reports.configure(result.get('reporting'))

    async def _report(self):
        while not self.closing:
            full = await self.reports.next(self.component_logs)
            if full is None:
                return
            await self._send_report(full)

    async def _work(self):
        await self._input_work()

    async def publish(self, topic, envelope, *, app_id=None):
        result, _ = await self.client.request("publish", envelope, timeout=self.request_timeout,
            topic=topic, **({"app_id": app_id} if app_id is not None else {}))
        self.metrics["publications"] = self.metrics.get("publications", 0) + 1
        refused = {key: value for key, value in result["delivery"].items()
                   if value not in ("accepted", "duplicate", "stale")}
        if refused:
            self.metrics["delivery_refused"] = self.metrics.get("delivery_refused", 0) + len(refused)
            print(f"{self.name}: delivery refused {refused}", file=sys.stderr)

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
            self.supervision_request = self.client.request
            await self.initialize_component_state()
            result, _ = await self.client.request("ready")
            self.reports.configure(result.get('reporting'))
            # Supervisor activates delivery only after every component is ready.
            while True:
                status, _ = await self.client.request("state")
                if status["enabled"]:
                    break
                await asyncio.sleep(.02)
            # Batch capture can be armed while the supervisor holds the READY
            # barrier. Apply it before the first input observation or callback.
            if self.component_recording.endpoint.points or self.node['inputs']:
                await self.component_recording.refresh()
            self.activated_at_ns = time.monotonic_ns()
            tasks = [*(asyncio.create_task(self._receiver(identity)) for identity in self.subscriptions),
                     asyncio.create_task(self._work()), asyncio.create_task(self._report()), self.client.task,
                     *(client.task for client in self.data_clients.values()), asyncio.create_task(self.component_exit()), asyncio.create_task(self.component_control())]
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
            self.close_execution()
            self.network_queues.clear()

    async def _stop_component(self):
        await stop_component(self.process)
