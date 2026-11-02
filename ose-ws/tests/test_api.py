"""HTTP/WebSocket integration with real OSE control, parameter and tap services."""
import asyncio
import base64
import copy
import os
from pathlib import Path
import socket
import tempfile
import time
import unittest
from unittest.mock import patch
import uuid

import httpx
from ose.execution.broker import Broker
from ose.execution.distributed import Coordinator
from ose.parameters.values import Values
from ose.codec.runtime import Runtime
from ose_ws.auth import Session
from ose_ws.cli import initialize
from ose_ws.config import read
from ose_ws.server import create_app
from test_core import CATALOGUE, VALUE


SDL_ROOT = Path(__file__).resolve().parents[2] / "sdl"


class APITests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.environment = patch.dict(os.environ, {"OSE_PARAMETER_TOKEN": "test-parameter-token"})
        self.environment.start()
        self.addCleanup(self.environment.stop)
        codec = Values(Runtime().api, CATALOGUE, "Settings")
        plain = copy.deepcopy(VALUE)
        plain["counter"] = int(plain["counter"])
        plain["samples"] = [int(v) for v in plain["samples"]]
        initial = codec.encode(plain)
        node = {"inputs": {}, "outputs": {"settings": {"catalogue": CATALOGUE, "root": "Settings"}},
                "trigger": {"mode": "periodic", "period_ms": 100}}
        self.broker = Broker({"sdl_root": str(SDL_ROOT), "components": {"source": node}, "subscriptions": [],
            "max_frame": 1024**2, "max_datagram": 1200,
            "shared_parameters": {"machine/settings": {"catalogue": CATALOGUE, "root": "Settings", "initial_payload": base64.b64encode(initial).decode()}},
            "parameter_clients": {"operator": {"token": "test-parameter-token", "read": ["machine/settings"], "write": ["machine/settings"]}}})
        await self.broker.start()
        self.broker.peers["source"] = (None, None)
        self.broker.ready.add("source")
        self.broker.enabled = True
        self.broker.generations["source"] = "generation-one"
        self.broker.metrics["source"] = {"running": True, "measurements": {"signal": 42.0}}
        self.broker.metric_times["source"] = time.monotonic()
        self.addAsyncCleanup(self.broker.close)
        path = self.root / "console.json"
        initialize(path)
        self.config = read(path, {"application": {"endpoint": None, "host": "127.0.0.1", "port": self.broker.port},
                                  "collection": {"interval_ms": 100}, "recording": {"min_free_disk_mib": 0}})
        self.app = create_app(self.config)
        self.lifespan = self.app.router.lifespan_context(self.app)
        await self.lifespan.__aenter__()
        self.addAsyncCleanup(self.lifespan.__aexit__, None, None, None)
        self.client = httpx.AsyncClient(transport=httpx.ASGITransport(self.app), base_url="http://127.0.0.1")
        self.addAsyncCleanup(self.client.aclose)
        token = Path(self.config["auth"]["token_file"]).read_text().strip()
        response = await self.client.post("/api/v1/auth/login", json={"token": token})
        self.assertEqual(response.status_code, 200, response.text)
        self.client.headers["X-CSRF-Token"] = response.json()["csrf"]
        await self.until(lambda: self.app.state.collector.error is None)

    async def until(self, predicate, timeout=6):
        deadline = time.monotonic() + timeout
        while not predicate():
            if time.monotonic() > deadline:
                self.fail("Condition timed out")
            await asyncio.sleep(.02)

    async def request(self, method, path, data=None, identity=None):
        return await self.client.request(method, "/api/v1" + path, json=data,
                                         headers={"Idempotency-Key": identity or uuid.uuid4().hex})

    async def wait_operation(self, response):
        self.assertIn(response.status_code, (200, 201, 202), response.text)
        operation = response.json()
        for _ in range(300):
            if operation["status"] != "pending":
                return operation
            await asyncio.sleep(.02)
            operation = (await self.client.get("/api/v1/operations/" + operation["id"])).json()
        self.fail("Operation did not finish")

    async def block(self):
        response = await self.client.get("/api/v1/parameters")
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()["items"][0]["id"]

    async def test_authentication_csrf_origin_and_no_cdn(self):
        anonymous = await httpx.AsyncClient(transport=httpx.ASGITransport(self.app), base_url="http://127.0.0.1").get("/api/v1/components")
        self.assertEqual(anonymous.status_code, 401)
        response = await self.client.post("/api/v1/measurement-watches", json={"measurement_ids": []}, headers={"X-CSRF-Token": "wrong"})
        self.assertEqual(response.status_code, 403)
        response = await self.client.post("/api/v1/session/renew", headers={"Origin": "https://untrusted.example"})
        self.assertEqual(response.status_code, 403)
        response = await self.client.get("/")
        self.assertEqual(response.status_code, 200)
        self.assertIn('<html lang="en">', response.text)
        self.assertNotIn("https://", response.text)
        self.assertIn("frame-ancestors 'none'", response.headers["Content-Security-Policy"])

    async def test_twenty_readers_do_not_multiply_status_requests(self):
        before = self.app.state.collector.requests
        started = time.monotonic()
        responses = await asyncio.gather(*(self.client.get("/api/v1/components") for _ in range(20)))
        self.assertTrue(all(r.status_code == 200 for r in responses))
        self.assertLessEqual(self.app.state.collector.requests - before, int((time.monotonic() - started) / .1) + 3)
        self.assertEqual(responses[0].json()["components"][0]["state"], "IDLE")

    async def test_exact_parameter_roundtrip_complete_replacement_and_retry(self):
        block = await self.block()
        initial = (await self.client.get(f"/api/v1/parameters/{block}")).json()
        self.assertEqual(initial["value"]["counter"], str(2**64 - 1))
        value = dict(initial["value"], counter="9223372036854775808", threshold=2.3456789)
        preview = await self.request("POST", f"/parameters/{block}/validate", {"value": value, "schema_digest": initial["schema_digest"]})
        self.assertEqual(preview.status_code, 200, preview.text)
        key = "parameter-write-key"
        first = await self.request("PUT", f"/parameters/{block}", preview.json(), key)
        self.assertEqual(first.json()["status"], "complete", first.text)
        self.assertEqual(first.json()["result"]["result"], "stored")
        second = await self.request("PUT", f"/parameters/{block}", preview.json(), key)
        self.assertEqual(first.json()["id"], second.json()["id"])
        current = (await self.client.get(f"/api/v1/parameters/{block}")).json()
        self.assertEqual(current["value"], preview.json()["value"])
        other = dict(initial["value"], enabled=False)
        preview2 = (await self.request("POST", f"/parameters/{block}/validate", {"value": other})).json()
        await self.request("PUT", f"/parameters/{block}", preview2)
        current = (await self.client.get(f"/api/v1/parameters/{block}")).json()
        self.assertEqual(current["value"]["counter"], str(2**64 - 1))
        self.assertFalse(current["value"]["enabled"])

    async def test_write_only_policy_cannot_read_or_export_but_can_validate_and_write(self):
        block = await self.block()
        session = self.app.state.auth.sessions[self.client.cookies.get("ose_ws_session")]
        session.permissions = {"parameters.write": ["machine/settings"]}
        self.assertEqual((await self.client.get(f"/api/v1/parameters/{block}")).status_code, 403)
        self.assertEqual((await self.request("POST", "/parameter-exports", {"block_ids": [block]})).status_code, 403)
        schema = await self.client.get(f"/api/v1/parameters/{block}/schema")
        self.assertEqual(schema.status_code, 200, schema.text)
        preview = await self.request("POST", f"/parameters/{block}/validate", {"value": VALUE})
        self.assertEqual(preview.status_code, 200, preview.text)
        result = await self.request("PUT", f"/parameters/{block}", preview.json())
        self.assertEqual(result.json()["status"], "complete", result.text)

    async def test_import_export_preserves_numeric_json_and_is_sequential(self):
        block = await self.block()
        response = await self.request("POST", "/parameter-exports", {"block_ids": [block]})
        self.assertIn('18446744073709551615', response.text)
        self.assertNotIn('"18446744073709551615"', response.text)
        preview = await self.client.post("/api/v1/parameter-imports/preview", content=response.content, headers={"Content-Type": "application/json"})
        self.assertEqual(preview.status_code, 200, preview.text)
        operation = await self.wait_operation(await self.request("POST", f"/parameter-imports/{preview.json()['id']}/apply"))
        self.assertEqual(operation["result"]["blocks"][0]["result"], "stored")

    async def test_measurement_watch_history_and_csv(self):
        catalogue = (await self.client.get("/api/v1/measurements")).json()["items"]
        identity = catalogue[0]["id"]
        response = await self.request("POST", "/measurement-watches", {"measurement_ids": [identity]})
        self.assertEqual(response.status_code, 200)
        await self.until(lambda: len(self.app.state.measurements.order) >= 2)
        history = (await self.client.get("/api/v1/series", params={"ids": identity})).json()
        self.assertEqual(history["segments"][0]["points"][0]["value"], 42)
        csv = await self.client.get("/api/v1/series/export", params={"ids": identity})
        self.assertIn('observed_at,sequence,segment,measurement,value,quality', csv.text)
        self.assertIn('source.signal', csv.text)

    async def test_capture_original_messages_finalize_validate_and_protect_files(self):
        response = await self.request("POST", "/recordings", {"selection": ["source.settings"], "label": "Integration capture", "format": "pcapng"})
        created = await self.wait_operation(response)
        self.assertEqual(created["status"], "complete", created)
        identity = created["result"]["id"]
        start = await self.wait_operation(await self.request("POST", f"/recordings/{identity}/start"))
        self.assertEqual(start["status"], "complete", start)
        for _ in range(4):
            await self.broker.publish("source.settings", self.broker.codec.wrap(CATALOGUE, self.broker.parameters.service.values["machine/settings"]))
        stop = await self.wait_operation(await self.request("POST", f"/recordings/{identity}/stop"))
        self.assertEqual(stop["result"]["state"], "STOPPED", stop)
        response = await self.client.get(f"/api/v1/recordings/{identity}/files")
        self.assertEqual(response.status_code, 200, response.text)
        artifact = response.json()["items"][0]
        validated = await self.wait_operation(await self.request("POST", f"/files/{artifact['id']}/validate"))
        self.assertEqual(validated["result"]["message_count"], 4, validated)
        download = await self.client.get(f"/api/v1/files/{artifact['id']}/download")
        self.assertEqual(download.content[:4], b'\x0a\x0d\x0d\x0a')
        path = Path(self.config["state_dir"]) / "captures" / identity / artifact["name"]
        path.rename(path.with_suffix('.original'))
        path.symlink_to(self.root / "console.json")
        refused = await self.client.get(f"/api/v1/files/{artifact['id']}/download")
        self.assertNotEqual(refused.status_code, 200)
        self.assertNotIn('OSE_PARAMETER_TOKEN', refused.text)

    async def test_websocket_authorization_and_parameter_invalidation(self):
        import uvicorn
        from websockets.asyncio.client import connect
        listener = socket.socket()
        listener.bind(("127.0.0.1", 0))
        listener.listen()
        port = listener.getsockname()[1]
        server = uvicorn.Server(uvicorn.Config(self.app, log_level="error", lifespan="off", proxy_headers=False))
        task = asyncio.create_task(server.serve(sockets=[listener]))
        try:
            await self.until(lambda: server.started)
            block = await self.block()
            async with connect(f"ws://127.0.0.1:{port}/api/v1/live", origin=f"http://127.0.0.1:{port}",
                               additional_headers={"Cookie": f"ose_ws_session={self.client.cookies.get('ose_ws_session')}"}) as ws:
                await ws.send(__import__('json').dumps({"action": "subscribe", "subscription_id": "parameters", "stream": "parameters", "selection": [block], "interval_ms": 250}))
                types = set()
                for _ in range(10):
                    event = __import__('json').loads(await asyncio.wait_for(ws.recv(), 3))
                    types.add(event['type'])
                    self.assertNotIn('threshold', str(event))
                    if 'parameters.changed' in types:
                        break
                self.assertIn('snapshot_required', types)
                self.assertIn('parameters.changed', types)
        finally:
            server.should_exit = True
            await asyncio.wait_for(task, 5)
            listener.close()

    async def test_distributed_coordinator_status_and_existing_parameter_rpc(self):
        plan = dict(self.broker.plan, execution="distributed")
        coordinator = Coordinator(plan)
        await coordinator.start()
        self.addAsyncCleanup(coordinator.close)
        collector = self.app.state.collector
        await collector.close()
        collector.client = None
        self.app.state.connection.config.update(port=coordinator.port, distributed=True, insecure=True)
        await self.app.state.parameters.reset()
        await collector.start()
        await self.until(lambda: collector.snapshot.get("epoch") == coordinator.epoch)
        status = (await self.client.get("/api/v1/application")).json()
        self.assertEqual(status["mode"], "distributed")
        self.assertTrue(status["connected"])
        block = await self.block()
        value = await self.client.get(f"/api/v1/parameters/{block}")
        self.assertEqual(value.status_code, 200, value.text)
        self.assertEqual(value.json()["value"]["counter"], str(2**64 - 1))
        points = await self.client.get("/api/v1/recording-points")
        self.assertEqual(points.status_code, 200, points.text)
        self.assertEqual(points.json()["items"][0]["root"], "Settings")

    async def test_real_capture_stop_works_when_intention_persistence_fails(self):
        from ose_ws.util import APIError
        created = await self.wait_operation(await self.request("POST", "/recordings", {"selection": ["source.settings"]}))
        identity = created["result"]["id"]
        start = await self.wait_operation(await self.request("POST", f"/recordings/{identity}/start"))
        self.assertEqual(start["status"], "complete", start)
        async def unavailable(*args):
            raise APIError(503, "STORAGE_UNAVAILABLE", "Injected metadata failure")
        with patch.object(self.app.state.store, "intention", unavailable):
            stopped = await self.wait_operation(await self.request("POST", f"/recordings/{identity}/stop"))
        self.assertFalse(stopped["durable"])
        self.assertEqual(stopped["result"]["state"], "STOPPED")
        self.assertEqual(self.broker.recording.sessions, {})

    async def test_unavailable_ose_does_not_stop_the_console_and_reconnects(self):
        collector = self.app.state.collector
        await collector.close()
        collector.client = None
        actual = self.app.state.connection.config["port"]
        reservation = socket.socket()
        reservation.bind(("127.0.0.1", 0))
        unused = reservation.getsockname()[1]
        reservation.close()
        self.app.state.connection.config["port"] = unused
        await collector.start()
        await self.until(lambda: collector.error is not None)
        health = await self.client.get("/health/ready")
        self.assertEqual(health.status_code, 200)
        diagnostic = await self.client.get("/api/v1/components")
        self.assertIsNotNone(diagnostic.json()["error"])
        self.app.state.connection.config["port"] = actual
        await self.until(lambda: collector.error is None)

    async def test_proxy_policy_requires_actual_trusted_peer_https_and_declared_identity(self):
        from ose_ws.auth import Auth
        from ose_ws.util import APIError
        from starlette.requests import Request
        config = copy.deepcopy(self.config)
        config["auth"].update(mode="proxy", trusted_proxies=["127.0.0.1"], users={"reader": {"permissions": {"components.read": ["*"]}}})
        auth = Auth(config)
        def request(peer="127.0.0.1", proto="https", actor="reader"):
            return Request({"type": "http", "scheme": "http", "method": "POST", "path": "/api/v1/auth/login",
                            "query_string": b"", "client": (peer, 8080), "server": ("127.0.0.1", 8080),
                            "headers": [(b"host", b"console.example"), (b"x-forwarded-proto", proto.encode()), (b"x-ose-user", actor.encode())]})
        signed_in = auth.login(request(), {})
        self.assertTrue(signed_in.allows("components.read"))
        self.assertFalse(signed_in.allows("parameters.write", "machine/settings"))
        for invalid in (request(peer="192.0.2.1"), request(proto="http"), request(actor="unknown")):
            with self.assertRaises(APIError):
                auth.login(invalid, {})
