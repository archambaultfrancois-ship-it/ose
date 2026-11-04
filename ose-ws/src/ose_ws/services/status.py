"""Exactly one periodic status request serves every browser."""
import asyncio
import random
import time
from ose.diagnostics import Observer
from ose_ws.util import utc


class StatusCollector:
    def __init__(self, config, connection, measurements):
        self.config, self.connection, self.measurements = config, connection, measurements
        self.observer = Observer(config["collection"]["diagnostic_window_s"])
        self.client, self.task = None, None
        self.snapshot = {}
        self.report = {"components": [], "error": "Waiting for OSE."}
        self.error = "Waiting for OSE."
        self.last_success = None
        self.last_mono = None
        self.requests, self.failures, self.latency_ms = 0, 0, None
        self.serial = 0

    async def start(self):
        self.task = asyncio.create_task(self.run())

    async def run(self):
        delay = 1
        while True:
            started = time.monotonic()
            try:
                if self.client is None:
                    self.client = await self.connection.connect()
                self.requests += 1
                snapshot, _ = await self.client.request("status", timeout=3)
                self.snapshot = snapshot
                self.latency_ms = (time.monotonic() - started) * 1000
                self.last_success, self.last_mono = utc(), time.monotonic()
                self.error, delay = None, 1
                try:
                    self.report = self.observer.sample(snapshot)
                except ValueError as error:
                    self.report = {"components": [], "error": str(error)}
                self.measurements.observe(snapshot)
                self.serial += 1
                await asyncio.sleep(max(0, self.config["collection"]["interval_ms"] / 1000 - (time.monotonic() - started)))
            except asyncio.CancelledError:
                raise
            except Exception as error:
                self.error = str(error) or type(error).__name__
                self.failures += 1
                if self.snapshot:
                    try:
                        self.report = self.observer.sample(self.snapshot, error=self.error)
                    except ValueError:
                        pass
                    self.measurements.observe(self.snapshot, self.error)
                self.serial += 1
                if self.client:
                    await self.client.close()
                    self.client = None
                await asyncio.sleep(delay + random.uniform(0, delay / 4))
                delay = min(30, delay * 2)

    def application(self):
        snapshot = self.snapshot
        return self.connection.public() | {"connected": self.error is None, "error": self.error,
            "epoch": snapshot.get("epoch"), "observed_at": self.last_success,
            "age_ms": (time.monotonic() - self.last_mono) * 1000 if self.last_mono else None,
            "capabilities": {"components": snapshot.get("observation", {}).get("version") == 1,
                             "logs": snapshot.get('logs_version') == 1,
                             "measurements": snapshot.get("measurements_version") == 1,
                             "parameters": "parameters" in snapshot},
            "status_requests": self.requests, "status_failures": self.failures, "rpc_latency_ms": self.latency_ms}

    def diagnostics(self):
        observation = self.snapshot.get("observation", {})
        topology = None
        if observation.get("version") == 1:
            topology = {"version": 1, "components": {
                name: {key: desc[key] for key in ("trigger", "inputs", "outputs") if key in desc}
                for name, desc in observation.get("components", {}).items()},
                "subscriptions": observation.get("subscriptions", {}),
                "external_topics": observation.get("external_topics", {})}
        return self.report | {"error": self.error or self.report.get("error"), "observed_at": self.last_success,
                              "age_ms": self.application()["age_ms"], "topology": topology}

    async def close(self):
        if self.task:
            self.task.cancel()
            await asyncio.gather(self.task, return_exceptions=True)
        if self.client:
            await self.client.close()
