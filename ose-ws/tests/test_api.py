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
from ose.transport.endpoint import publish
from ose.delivery.policies import SubscriptionPolicy
from ose.parameters.values import Values
from ose.codec.runtime import Runtime
from ose_ws.auth import Session
from ose_ws.cli import initialize
from ose_ws.config import read
from ose_ws.server import create_app
from test_core import CATALOGUE, VALUE


SDL_ROOT = Path(__file__).resolve().parents[2] / "sdl"


class APITests(unittest.IsolatedAsyncioTestCase):
    async def test_input_point_rights_use_the_natural_topic_name(self):
        node = self.broker.plan["components"]["source"]
        node["inputs"]["settings"] = dict(node["outputs"]["settings"])
        await self.app.state.recordings.refresh(force=True)
        signed_in = next(iter(self.app.state.auth.sessions.values()))
        with patch.object(signed_in, "permissions", {"recordings.create": ["source.settings"]}):
            points = (await self.client.get("/api/v1/recording-points")).json()["items"]
            self.assertEqual({p["kind"] for p in points}, {"input", "output"})
            point = next(p for p in points if p["kind"] == "input")
            self.assertEqual((await self.client.get(f"/api/v1/recording-points/{point['id']}/schema")).status_code, 200)
            self.assertEqual((await self.request("POST", "/recording-filters/validate", {
                "point_id": point["id"], "expression": "enabled == true"})).status_code, 200)
            created = await self.wait_operation(await self.request("POST", "/recordings", {"selection": [point["id"]]}))
            self.assertEqual(created["status"], "complete", created)
            self.assertEqual(created["result"]["selection"][0]["kind"], "input")
        with patch.object(signed_in, "permissions", {"recordings.create": ["input:*"]}):
            self.assertEqual((await self.client.get("/api/v1/recording-points")).json()["items"], [])
            self.assertEqual((await self.client.get(f"/api/v1/recording-points/{point['id']}/schema")).status_code, 403)

    async def test_same_named_input_output_capture_keeps_categories_and_filters_separate(self):
        node = self.broker.plan["components"]["source"]
        node["inputs"]["settings"] = dict(node["outputs"]["settings"])
        manager = self.app.state.recordings
        await manager.refresh(force=True)
        points = (await self.client.get("/api/v1/recording-points", params={"search": "source.settings"})).json()["items"]
        self.assertEqual(len(points), 2)
        by_kind = {point["kind"]: point for point in points}
        self.assertNotEqual(by_kind["input"]["id"], by_kind["output"]["id"])
        self.assertEqual({point["topic"] for point in points}, {"source.settings"})
        only_inputs = (await self.client.get("/api/v1/recording-points", params={"kind": "input"})).json()["items"]
        self.assertEqual([p["id"] for p in only_inputs], [by_kind["input"]["id"]])
        for point in points:
            self.assertEqual((await self.client.get(f"/api/v1/recording-points/{point['id']}/schema")).status_code, 200)
        created = await self.wait_operation(await self.request("POST", "/recordings", {
            "selection": [by_kind["input"]["id"], by_kind["output"]["id"]],
            "filters": {by_kind["input"]["id"]: "enabled == true"}}))
        self.assertEqual(created["status"], "complete", created)
        capture = created["result"]
        selection = {point["kind"]: point for point in capture["selection"]}
        self.assertEqual(selection["input"]["topic"], "source.settings")
        self.assertNotEqual(selection["input"]["point_key"], selection["output"]["point_key"])
        self.assertEqual(selection["input"]["filter"], "enabled == true")
        self.assertIsNone(selection["output"]["filter"])
        identity = capture["id"]
        started = await self.wait_operation(await self.request("POST", f"/recordings/{identity}/start"))
        self.assertEqual(started["status"], "complete", started)
        envelope = self.broker.codec.wrap(CATALOGUE, self.broker.parameters.service.values["machine/settings"])
        published_at = time.time_ns()
        self.broker.component_recording("source", "recording_input",
            dict(port="settings", sequence=1, publish_time_ns=published_at), envelope)
        await self.broker.publish("source.settings", envelope)
        await self.until(lambda: manager.recorders[identity].capture.counts["written"] == 2)
        stopped = await self.wait_operation(await self.request("POST", f"/recordings/{identity}/stop"))
        self.assertEqual(stopped["status"], "complete", stopped)
        saved = (await self.client.get(f"/api/v1/recordings/{identity}")).json()
        self.assertEqual(saved["status"]["topics"], {p["point_key"]: 1 for p in capture["selection"]})
        from sdl_recording import RecordingReader
        records = list(RecordingReader(Path(self.config["state_dir"]) / "captures" / identity / "capture.pcapng").iter_records())
        self.assertEqual({r.channel.metadata["ose.kind"] for r in records}, {"input", "output"})
        self.assertEqual({r.channel.topic for r in records}, {"source.settings"})
        observed_input = next(r for r in records if r.channel.metadata["ose.kind"] == "input")
        self.assertEqual(observed_input.publish_time_ns, published_at)
        self.assertGreaterEqual(observed_input.log_time_ns, published_at)
        scoped = await self.wait_operation(await self.request("POST", "/recordings", {"selection": ["all"], "kind": "input"}))
        self.assertEqual([p["kind"] for p in scoped["result"]["selection"]], ["input"])

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
        node = {"execution_model": 2, "inputs": {"clock": {"kind": "timer", "timer": {"mode": "periodic", "delay": .1},
                "activates": {"timeout": ["settings"]}}}, "outputs": {"settings": {"catalogue": CATALOGUE, "root": "Settings"}}}
        self.broker = Broker({"version": 2, "sdl_root": str(SDL_ROOT), "components": {"source": node}, "subscriptions": [],
            "max_frame": 1024**2, "max_datagram": 1200,
            "shared_parameters": {"machine/settings": {"catalogue": CATALOGUE, "root": "Settings", "initial_payload": base64.b64encode(initial).decode()}},
            "parameter_clients": {"operator": {"token": "test-parameter-token", "read": ["machine/settings"], "write": ["machine/settings"]}}})
        await self.broker.start()
        self.broker.peers["source"] = (None, None)
        self.broker.ready.add("source")
        self.broker.components.acknowledge('source', 0, True)
        self.broker.enabled = True
        self.broker.generations["source"] = "generation-one"
        self.broker.metrics["source"] = {"running": True, "measurements": {"signal": 42.0}}
        self.broker.metric_times["source"] = time.monotonic()
        self.addAsyncCleanup(self.broker.close)
        path = self.root / "console.json"
        initialize(path)
        runtime = self.root / "runtime files"
        runtime.mkdir()
        self.config = read(path, {"application": {"endpoint": None, "host": "127.0.0.1", "port": self.broker.port},
                                  "collection": {"interval_ms": 100}, "recording": {"min_free_disk_mib": 0}},
                           working_directory=runtime)
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
        self.assertTrue((self.root / "runtime files/.ose-ws/state.sqlite3").is_file())
        self.assertTrue((self.root / "runtime files/.ose-ws/captures").is_dir())
        self.assertFalse((self.root / ".ose-ws/state.sqlite3").exists())
        self.assertTrue((self.root / ".ose-ws/access.token").is_file())
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

    async def test_log_filters_fatal_cursors_and_scoped_read_permissions(self):
        stamp = str(time.time_ns())
        self.broker.logs.append('source', 'generation-one', [
            dict(level='INFO', logger='lifecycle', message='started', time_ns=stamp),
            dict(level='CRITICAL', logger='detection', message='critical', time_ns=stamp),
            dict(level='FATAL', logger='detection', message='Échec moteur', time_ns=stamp)])
        self.broker.logs.append('private', 'other-generation', [
            dict(level='FATAL', logger='detection', message='private message', time_ns=stamp)])
        response = await self.client.get('/api/v1/logs', params={'components': 'sou*', 'levels': 'FATAL', 'search': 'MOTEUR'})
        self.assertEqual(response.status_code, 200, response.text)
        result = response.json()
        self.assertEqual([row['message'] for row in result['items']], ['Échec moteur'])
        self.assertEqual(result['items'][0]['level'], 'FATAL')
        later = await self.client.get('/api/v1/logs', params={'after': result['cursor'], 'epoch': result['epoch']})
        self.assertEqual(later.json()['items'], [])
        signed_in = self.app.state.auth.sessions[self.client.cookies.get('ose_ws_session')]
        with patch.object(signed_in, 'permissions', {'logs.read': ['sou*']}):
            limited = (await self.client.get('/api/v1/logs')).json()['items']
            self.assertEqual(len(limited), 3)
            self.assertTrue(all(row['component'] == 'source' for row in limited))
            self.assertEqual((await self.client.get('/api/v1/logs?components=private')).json()['items'], [])
        with patch.object(signed_in, 'permissions', {'components.read': ['*']}):
            self.assertEqual((await self.client.get('/api/v1/logs')).status_code, 403)
        for query in ('levels=INVALID', 'after=-1', 'limit=201'):
            self.assertEqual((await self.client.get('/api/v1/logs?' + query)).status_code, 422)

    async def test_twenty_readers_do_not_multiply_status_requests(self):
        before = self.app.state.collector.requests
        started = time.monotonic()
        responses = await asyncio.gather(*(self.client.get("/api/v1/components") for _ in range(20)))
        self.assertTrue(all(r.status_code == 200 for r in responses))
        self.assertLessEqual(self.app.state.collector.requests - before, int((time.monotonic() - started) / .1) + 3)
        self.assertEqual(responses[0].json()["components"][0]["state"], "IDLE")
        topology = responses[0].json()["topology"]
        self.assertEqual(list(topology["components"]), ["source"])
        self.assertEqual(topology["components"]["source"]["outputs"], {"settings": {"root": "Settings"}})
        self.assertNotIn("catalogue", topology["components"]["source"]["outputs"]["settings"])

    async def test_link_contracts_for_local_direct_and_external_subscriptions(self):
        external_catalogue = "SDL2|Child{1:uint16:code;}|Packet{1:Child:child;2*:fl32:samples;3:Mode:mode;}|#Mode{OFF=0;ON=1;}"
        sink = {"inputs": {"settings": {"root": "Settings", "catalogue": CATALOGUE},
                           "offset": {"root": "Packet", "catalogue": external_catalogue}},
                "outputs": {}, "execution_model": 2}
        policy = SubscriptionPolicy.resolve().to_dict()
        collector = self.app.state.collector
        for distributed in (False, True):
            plan = dict(self.broker.plan, execution="distributed" if distributed else "local",
                components={"source": self.broker.plan["components"]["source"], "sink": sink},
                subscriptions=[
                    {"id": "source/settings #1", "from": "source.settings", "to": "sink.settings", "policy": policy,
                     "routing": "direct" if distributed else "broker"},
                    {"id": "external", "from": "source.external", "from_topic": "source.external", "to": "sink.offset", "policy": policy}],
                external_topics={"source.external": {"root": "Packet", "catalogue": external_catalogue}},
                broker={"external": {"enabled": False}})
            runtime = Coordinator(plan) if distributed else Broker(plan)
            await runtime.start()
            self.addAsyncCleanup(runtime.close)
            await collector.close()
            collector.client = None
            self.app.state.connection.config.update(port=runtime.port, distributed=distributed, insecure=True)
            await collector.start()
            await self.until(lambda: collector.snapshot.get("epoch") == runtime.epoch)
            params = {"subscription": "source/settings #1", "epoch": runtime.epoch}
            signed_in = self.app.state.auth.sessions[self.client.cookies.get(self.app.state.auth.cookie)]
            with patch.object(signed_in, 'permissions', {'components.read': ['*']}):
                response = await self.client.get("/api/v1/flow/schema", params=params)
                self.assertEqual(response.status_code, 200, response.text)
                self.assertEqual(response.json()["root"], "Settings")
                self.assertEqual(response.json()["catalogue"], CATALOGUE)
                self.assertEqual(response.json()["types"]["Settings"]["fields"][0]["name"], "enabled")
                response = await self.client.get("/api/v1/flow/schema", params=dict(params, subscription="external"))
                self.assertEqual(response.status_code, 200, response.text)
                self.assertEqual(response.json()["root"], "Packet")
                self.assertEqual(response.json()["types"]["Child"]["fields"][0]["type"], "uint16")
                self.assertEqual(response.json()["types"]["Mode"]["kind"], "enum")
                self.assertEqual((await self.client.get("/api/v1/flow/schema", params=dict(params, subscription="unknown"))).status_code, 404)
                self.assertEqual((await self.client.get("/api/v1/flow/schema", params=dict(params, epoch="obsolete"))).status_code, 409)
            with patch.object(signed_in, 'permissions', {'recordings.create': ['*']}):
                self.assertEqual((await self.client.get("/api/v1/flow/schema", params=params)).status_code, 403)
            # A reconnect to a new runtime cannot return a contract for the old graph.
            with patch.object(collector, "error", "Disconnected"):
                self.assertEqual((await self.client.get("/api/v1/flow/schema", params=params)).status_code, 503)

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

    async def test_parameter_validation_identifies_the_exact_field_or_array_element(self):
        block = await self.block()
        initial = (await self.client.get(f"/api/v1/parameters/{block}")).json()
        for field, value, pointer in (("counter", "-1", "/counter"),
                                      ("label", 42, "/label"),
                                      ("samples", ["1", "invalid"], "/samples/1")):
            response = await self.request("POST", f"/parameters/{block}/validate", {
                "value": dict(initial["value"], **{field: value}), "schema_digest": initial["schema_digest"]})
            self.assertEqual(response.status_code, 422, response.text)
            self.assertEqual(response.json()["error"]["details"]["pointer"], pointer)
        current = (await self.client.get(f"/api/v1/parameters/{block}")).json()
        self.assertEqual(current["value"], initial["value"])

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

    async def create_capture(self, finished=False):
        created = await self.wait_operation(await self.request("POST", "/recordings", {"selection": ["source.settings"]}))
        self.assertEqual(created["status"], "complete", created)
        identity = created["result"]["id"]
        if finished:
            start = await self.wait_operation(await self.request("POST", f"/recordings/{identity}/start"))
            self.assertEqual(start["status"], "complete", start)
            await self.broker.publish("source.settings", self.broker.codec.wrap(CATALOGUE, self.broker.parameters.service.values["machine/settings"]))
            stop = await self.wait_operation(await self.request("POST", f"/recordings/{identity}/stop"))
            self.assertEqual(stop["result"]["state"], "STOPPED", stop)
        return identity

    async def test_delete_finished_capture_keeps_files_and_replays_original_operation(self):
        identity = await self.create_capture(finished=True)
        artifact = (await self.client.get(f"/api/v1/recordings/{identity}/files")).json()["items"][0]
        file = Path(self.config["state_dir"]) / "captures" / identity / artifact["name"]
        original = file.read_bytes()
        key = uuid.uuid4().hex
        deleted = await self.wait_operation(await self.request("DELETE", f"/recordings/{identity}", {"delete_files": False}, key))
        self.assertEqual(deleted["status"], "complete", deleted)
        self.assertFalse(deleted["result"]["files_deleted"])
        self.assertEqual(file.read_bytes(), original)
        self.assertEqual((await self.client.get("/api/v1/recordings")).json()["items"], [])
        self.assertEqual((await self.client.get(f"/api/v1/recordings/{identity}")).status_code, 404)
        self.assertEqual((await self.client.get(f"/api/v1/files/{artifact['id']}/download")).status_code, 404)
        repeated = await self.wait_operation(await self.request("DELETE", f"/recordings/{identity}", {"delete_files": False}, key))
        self.assertEqual(repeated["id"], deleted["id"])
        conflict = await self.request("DELETE", f"/recordings/{identity}", {"delete_files": True}, key)
        self.assertEqual(conflict.status_code, 409)
        unknown = await self.request("DELETE", f"/recordings/{identity}", {"delete_files": False})
        self.assertEqual(unknown.status_code, 404)
        self.assertEqual(await self.app.state.store.captures(), [])
        audit = await self.app.state.store.audit()
        self.assertTrue(any(row["target"] == f"recordings/{identity}/delete" and row["action"] == "result" for row in audit))

    async def test_delete_finished_capture_removes_rotated_files_and_directory(self):
        identity = await self.create_capture(finished=True)
        directory = Path(self.config["state_dir"]) / "captures" / identity
        (directory / "capture_001.pcapng").write_bytes((directory / "capture.pcapng").read_bytes())
        operation = await self.wait_operation(await self.request("DELETE", f"/recordings/{identity}", {"delete_files": True}))
        self.assertEqual(operation["status"], "complete", operation)
        self.assertTrue(operation["result"]["files_deleted"])
        self.assertFalse(directory.exists())

    async def test_delete_rejects_active_captures_and_releases_draft_quota(self):
        self.config["limits"]["max_drafts"] = 1
        identity = await self.create_capture()
        capture = self.app.state.recordings.objects[identity]
        for state in ("STARTING", "RECORDING", "STOPPING"):
            capture["state"] = state
            operation = await self.wait_operation(await self.request("DELETE", f"/recordings/{identity}", {"delete_files": True}))
            self.assertEqual(operation["status"], "failed", operation)
            self.assertEqual(operation["result"]["code"], "CAPTURE_ACTIVE")
            self.assertIn(identity, self.app.state.recordings.objects)
        capture["state"] = "DRAFT"
        operation = await self.wait_operation(await self.request("DELETE", f"/recordings/{identity}", {"delete_files": True}))
        self.assertEqual(operation["status"], "complete", operation)
        await self.create_capture()

    async def test_delete_requires_owner_permission_and_an_explicit_boolean_choice(self):
        identity = await self.create_capture()
        path = f"/recordings/{identity}"
        for data in ({}, {"delete_files": "false"}, {"delete_files": 1}, {"delete_files": False, "extra": True}):
            self.assertEqual((await self.request("DELETE", path, data)).status_code, 422)
        refused = await self.client.request("DELETE", "/api/v1" + path, json={"delete_files": False}, headers={"X-CSRF-Token": "wrong"})
        self.assertEqual(refused.status_code, 403)
        signed_in = next(iter(self.app.state.auth.sessions.values()))
        with patch.object(signed_in, "permissions", {"recordings.read": ["*"]}):
            listed = (await self.client.get("/api/v1/recordings")).json()["items"][0]
            self.assertFalse(listed["permissions"]["delete"])
            self.assertEqual((await self.request("DELETE", path, {"delete_files": False})).status_code, 404)
        with patch.object(signed_in, "actor", "other-user"), patch.object(signed_in, "permissions", {
                "recordings.read": [identity], "recordings.delete": [identity]}):
            listed = (await self.client.get("/api/v1/recordings")).json()["items"][0]
            self.assertFalse(listed["permissions"]["delete"])
            self.assertEqual((await self.request("DELETE", path, {"delete_files": False})).status_code, 404)
        with patch.object(signed_in, "permissions", {"recordings.delete": [identity]}):
            operation = await self.wait_operation(await self.request("DELETE", path, {"delete_files": False}))
            self.assertEqual(operation["status"], "complete", operation)

    async def test_delete_preserves_unexpected_entries_and_symlink_targets(self):
        identity = await self.create_capture()
        directory = Path(self.config["state_dir"]) / "captures" / identity
        directory.mkdir()
        known = directory / "capture.pcapng"
        known.write_bytes(b"capture-content")
        outside = self.root / "outside.pcapng"
        outside.write_bytes(b"outside-content")
        for name in ("notes.txt", "capture_001.pcapng"):
            entry = directory / name
            entry.symlink_to(outside)
            operation = await self.wait_operation(await self.request("DELETE", f"/recordings/{identity}", {"delete_files": True}))
            self.assertEqual(operation["result"]["code"], "CAPTURE_FILES_CHANGED", operation)
            self.assertEqual(known.read_bytes(), b"capture-content")
            self.assertEqual(outside.read_bytes(), b"outside-content")
            self.assertFalse(self.app.state.store.failed)
            entry.unlink()
        (directory / "notes.txt").write_bytes(b"keep-notes")
        operation = await self.wait_operation(await self.request("DELETE", f"/recordings/{identity}", {"delete_files": True}))
        self.assertEqual(operation["result"]["code"], "CAPTURE_FILES_CHANGED", operation)
        self.assertEqual(known.read_bytes(), b"capture-content")
        operation = await self.wait_operation(await self.request("DELETE", f"/recordings/{identity}", {"delete_files": False}))
        self.assertEqual(operation["status"], "complete", operation)
        self.assertEqual((directory / "notes.txt").read_bytes(), b"keep-notes")

    async def test_delete_never_follows_a_replaced_capture_directory(self):
        identity = await self.create_capture()
        directory = Path(self.config["state_dir"]) / "captures" / identity
        outside = self.root / "outside"
        outside.mkdir()
        file = outside / "capture.pcapng"
        file.write_bytes(b"keep-outside")
        directory.symlink_to(outside, target_is_directory=True)
        operation = await self.wait_operation(await self.request("DELETE", f"/recordings/{identity}", {"delete_files": True}))
        self.assertEqual(operation["result"]["code"], "CAPTURE_FILES_CHANGED", operation)
        self.assertEqual(file.read_bytes(), b"keep-outside")
        self.assertFalse(self.app.state.store.failed)

    async def test_delete_failed_and_interrupted_captures_without_files(self):
        for state in ("FAILED", "INTERRUPTED"):
            identity = await self.create_capture()
            capture = self.app.state.recordings.objects[identity]
            capture["state"] = state
            await self.app.state.store.save_capture(capture)
            operation = await self.wait_operation(await self.request("DELETE", f"/recordings/{identity}", {"delete_files": True}))
            self.assertEqual(operation["status"], "complete", operation)
            self.assertNotIn(identity, self.app.state.recordings.objects)

    async def test_delete_does_not_touch_files_without_a_durable_intention(self):
        from ose_ws.util import APIError
        identity = await self.create_capture(finished=True)
        directory = Path(self.config["state_dir"]) / "captures" / identity
        original = (directory / "capture.pcapng").read_bytes()
        async def unavailable(*args):
            raise APIError(503, "STORAGE_UNAVAILABLE", "Injected metadata failure")
        with patch.object(self.app.state.store, "intention", unavailable):
            response = await self.request("DELETE", f"/recordings/{identity}", {"delete_files": True})
        self.assertEqual(response.status_code, 503)
        self.assertEqual((directory / "capture.pcapng").read_bytes(), original)
        self.assertIn(identity, self.app.state.recordings.objects)

    async def test_delete_serializes_with_a_pending_start_without_recreating_the_capture(self):
        identity = await self.create_capture()
        manager = self.app.state.recordings
        lock = manager.locks.setdefault(identity, asyncio.Lock())
        await lock.acquire()
        deletion = asyncio.create_task(self.request("DELETE", f"/recordings/{identity}", {"delete_files": True}))
        starting = None
        try:
            await self.until(lambda: bool(self.app.state.operations.tasks))
            await asyncio.sleep(.02)
            starting = await self.request("POST", f"/recordings/{identity}/start")
        finally:
            lock.release()
        deleted = await self.wait_operation(await deletion)
        self.assertEqual(deleted["status"], "complete", deleted)
        started = await self.wait_operation(starting)
        self.assertEqual(started["status"], "failed", started)
        self.assertNotIn(identity, manager.objects)
        self.assertNotIn(identity, manager.recorders)
        self.assertEqual(self.broker.recording.sessions, {})

    async def test_live_file_size_grows_and_final_size_matches_download(self):
        created = await self.wait_operation(await self.request('POST', '/recordings', {
            'selection': ['source.settings'], 'format': 'pcapng'}))
        identity = created['result']['id']
        await self.wait_operation(await self.request('POST', f'/recordings/{identity}/start'))
        manager = self.app.state.recordings
        payload = self.broker.codec.wrap(CATALOGUE, self.broker.parameters.service.values['machine/settings'])

        async def publish_batch():
            for _ in range(200):
                await self.broker.publish('source.settings', payload)
                await asyncio.sleep(.001)

        await publish_batch()
        await self.until(lambda: (manager.objects[identity].get('status', {}).get('file_size_bytes') or 0) > 0)
        first = (await self.client.get(f'/api/v1/recordings/{identity}')).json()
        self.assertEqual(first['state'], 'RECORDING')
        first_size = first['status']['file_size_bytes']
        point = 'source.settings'
        self.assertGreater(first['status']['topics'][point], 0)
        self.assertGreater(first['status']['topic_bytes'][point], 0)
        self.assertNotIn('file', first['status'])
        self.assertNotIn('base_file', first['status'])
        await publish_batch()
        await self.until(lambda: (manager.objects[identity]['status']['file_size_bytes'] or 0) > first_size)
        await self.wait_operation(await self.request('POST', f'/recordings/{identity}/stop'))
        final = (await self.client.get(f'/api/v1/recordings/{identity}')).json()
        artifact = (await self.client.get(f'/api/v1/recordings/{identity}/files')).json()['items'][0]
        self.assertGreater(final['status']['file_size_bytes'], first_size)
        self.assertEqual(final['status']['file_size_bytes'], artifact['size'])
        self.assertEqual(final['status']['topics'][point], 400)
        _, encoded = self.broker.codec.api.unpack_envelope(payload)
        self.assertEqual(final['status']['topic_bytes'][point], 400 * len(encoded))
        # The per-point statistics survive a console restart through capture metadata.
        stored = next(c for c in await self.app.state.store.captures() if c['id'] == identity)
        self.assertEqual(stored['status']['topic_bytes'][point], final['status']['topic_bytes'][point])

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

    async def test_endpoint_switches_local_distributed_local_for_status_parameters_and_recording(self):
        plan = dict(self.broker.plan, execution="distributed")
        coordinator = Coordinator(plan)
        await coordinator.start()
        self.addAsyncCleanup(coordinator.close)
        collector = self.app.state.collector
        await collector.close()
        collector.client = None
        publish(self.root, coordinator.port, distributed=True, insecure=True)
        self.app.state.connection.config.update(host=None, port=None, endpoint=str(self.root / 'endpoint.json'))
        await self.app.state.parameters.reset()
        await collector.start()
        await self.until(lambda: collector.snapshot.get("epoch") == coordinator.epoch)
        status = (await self.client.get("/api/v1/application")).json()
        self.assertEqual(status["mode"], "distributed")
        self.assertTrue(status["connected"])
        topology = (await self.client.get("/api/v1/components")).json()["topology"]
        self.assertEqual(topology["components"]["source"]["outputs"], {"settings": {"root": "Settings"}})
        block = await self.block()
        value = await self.client.get(f"/api/v1/parameters/{block}")
        self.assertEqual(value.status_code, 200, value.text)
        self.assertEqual(value.json()["value"]["counter"], str(2**64 - 1))
        points = await self.client.get("/api/v1/recording-points")
        self.assertEqual(points.status_code, 200, points.text)
        self.assertEqual(points.json()["items"][0]["root"], "Settings")
        self.assertTrue(self.app.state.recordings.discovery.distributed)
        self.assertFalse(self.app.state.connection.config['distributed'])
        await collector.close()
        collector.client = None
        publish(self.root, self.broker.port)
        await self.app.state.parameters.reset()
        await collector.start()
        await self.until(lambda: collector.snapshot.get("epoch") == self.broker.epoch)
        status = (await self.client.get("/api/v1/application")).json()
        self.assertEqual(status["mode"], "local")
        self.assertTrue(status["connected"])
        self.assertEqual((await self.client.get(f"/api/v1/parameters/{block}")).status_code, 200)
        await self.app.state.recordings.refresh(force=True)
        self.assertFalse(self.app.state.recordings.discovery.distributed)

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
