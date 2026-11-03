"""Bounded history, durable dispatch and exact SDL conversion regression tests."""
import asyncio
import copy
import os
from pathlib import Path
import tempfile
import time
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from ose_ws.auth import Session
from ose_ws.cli import initialize
from ose_ws.config import read
from ose_ws.schema.adapter import SchemaAdapter
from ose_ws.services.measurements import MeasurementStore
from ose_ws.services.operations import Operations
from ose_ws.services.recordings import RecordingManager
from ose_ws.storage.state import StateStore
from ose_ws.util import APIError, loads


CATALOGUE = "SDL2|Settings{1:bool:enabled;2:uint64:counter;3:fl32:threshold;4?:string:label;5*:int64:samples;6:fl32:quantized@b12,q0.1;}"
VALUE = {"enabled": True, "counter": "18446744073709551615", "threshold": 1.23456789,
         "label": None, "samples": ["-9223372036854775808", "9223372036854775807"], "quantized": 1.23}


class RecordingSizeTests(unittest.IsolatedAsyncioTestCase):
    async def test_current_rotated_missing_and_replaced_files(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            directory = root / 'captures' / 'capture'
            directory.mkdir(parents=True)
            manager = RecordingManager({}, None, SimpleNamespace(root=root))
            first = directory / 'capture_001.pcapng'
            second = directory / 'capture_002.mcap'
            first.write_bytes(b'a' * 1234)
            second.write_bytes(b'b' * 5678)
            self.assertEqual(manager.file_size('capture', first.name), 1234)
            self.assertEqual(manager.file_size('capture', second.name), 5678)
            self.assertIsNone(manager.file_size('capture', 'capture_003.pcapng'))
            self.assertIsNone(manager.file_size('capture', '../capture_001.pcapng'))
            first.unlink()
            first.symlink_to(second)
            self.assertIsNone(manager.file_size('capture', first.name))
            first.unlink()
            first.mkdir()
            self.assertIsNone(manager.file_size('capture', first.name))
            first.rmdir()
            second.unlink()
            directory.rmdir()
            directory.symlink_to(root)
            self.assertIsNone(manager.file_size('capture', second.name))


class SchemaTests(unittest.TestCase):
    def test_exact_integer_normalization_and_optional_presence(self):
        adapter = SchemaAdapter(CATALOGUE, "Settings")
        preview, payload = adapter.validate(VALUE)
        self.assertEqual(preview["value"]["counter"], VALUE["counter"])
        self.assertEqual(preview["value"]["samples"], VALUE["samples"])
        self.assertIsNone(preview["value"]["label"])
        self.assertTrue(preview["normalized"])
        self.assertNotEqual(preview["value"]["threshold"], VALUE["threshold"])
        self.assertAlmostEqual(preview["value"]["quantized"], 1.2, places=5)
        again, _ = adapter.validate(preview["value"])
        self.assertEqual(preview["preview_hash"], again["preview_hash"])
        self.assertEqual(adapter.read(payload), preview["value"])
        plain = adapter.convert(preview["value"], to_web=False)
        self.assertIs(type(plain["counter"]), int)
        self.assertEqual(plain["counter"], 2**64 - 1)

    def test_numeric_64_bit_json_is_rejected_in_web_format(self):
        value = dict(VALUE, counter=2**64 - 1)
        with self.assertRaises(APIError) as raised:
            SchemaAdapter(CATALOGUE, "Settings").validate(value)
        self.assertEqual(raised.exception.details["pointer"], "/counter")

    def test_contract_and_business_constraints_are_authoritative(self):
        adapter = SchemaAdapter(CATALOGUE, "Settings", {"/threshold": {"required": True, "max": 1}})
        with self.assertRaises(APIError):
            adapter.validate(VALUE)
        adapter = SchemaAdapter(CATALOGUE, "Settings")
        with self.assertRaises(APIError):
            adapter.validate(dict(VALUE, counter="18446744073709551616"))
        self.assertEqual(adapter.description()["types"]["Settings"]["fields"][-1]["wire"]["q"], "0.1")

    def test_strict_json_and_depth_limit(self):
        for value in ('{"a":1,"a":2}', '{"a":NaN}', '{"a":1e400}', '[' * 65 + '0' + ']' * 65):
            with self.assertRaises(ValueError):
                loads(value)

    def test_nested_arrays_sequences_enums_complex_and_absent_values(self):
        catalogue = "SDL2|Child{1:int16:code;}|Root{1:Child:child;2:fl64[2][2]:matrix;3*:uint8:samples;4?:Child:optional_child;5:c32:phasor;6:State:state;}|#State{OFF=0;ON=1;}"
        adapter = SchemaAdapter(catalogue, "Root")
        value = {"child": {"code": -32768}, "matrix": [[1., 2.], [3., 4.]], "samples": [0, 255],
                 "optional_child": None, "phasor": {"real": 1.25, "imag": -2.5}, "state": "ON"}
        preview, payload = adapter.validate(value)
        self.assertEqual(adapter.read(payload), value)
        self.assertEqual(preview["value"], value)
        with self.assertRaises(APIError) as invalid:
            adapter.validate(dict(value, matrix=[[1.], [2.]]))
        self.assertEqual(invalid.exception.details["pointer"], "/matrix/0")


class HistoryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        path = Path(self.temp.name) / "console.json"
        initialize(path)
        self.config = read(path)
        self.config["measurements"].update(max_series=1, max_total_samples=12)
        self.store = MeasurementStore(self.config)

    @staticmethod
    def snapshot(generation="one", value=1, age=1):
        return {"epoch": "run", "observation": {"components": {"source": {
            "connected": True, "report_age_ms": age, "generation": generation}}},
            "components": {"source": {"measurements": {"signal": value}}}}

    def test_watch_survives_generations_without_consuming_active_quota(self):
        self.store.observe(self.snapshot())
        identity = next(iter(self.store.catalogue))
        watch = self.store.watch("reader", [identity])
        self.store.observe(self.snapshot())
        self.store.observe(self.snapshot("two", 2))
        self.store.watch("reader", [identity], watch["id"])
        history = self.store.query([identity])
        self.assertEqual(len(history["segments"]), 2)
        self.assertEqual(len(self.store.active()), 1)
        self.assertEqual({s["generation"] for s in history["segments"]}, {"one", "two"})

    def test_stale_values_make_gaps_and_budget_evicts_old_points(self):
        self.store.observe(self.snapshot())
        identity = next(iter(self.store.catalogue))
        self.store.watch("reader", [identity])
        self.store.observe(self.snapshot(age=3001))
        points = self.store.query([identity])["segments"][0]["points"]
        self.assertEqual(points[0]["quality"], "stale")
        self.assertIsNone(points[0]["value"])
        for value in range(40):
            self.store.observe(self.snapshot(value=value))
        report = self.store.query([identity])
        self.assertLessEqual(report["buffer_samples"], 12)
        self.assertLessEqual(report["buffer_bytes"], 32 * 1024**2)
        self.assertTrue(report["truncated"])

    def test_reduction_preserves_extrema_and_a_gap(self):
        points = [(i, i, i, 1000 if i == 23 else -1000 if i == 24 else None if i == 25 else 0, "good") for i in range(100)]
        reduced = self.store.reduce(points, 20)
        self.assertLessEqual(len(reduced), 20)
        self.assertIn(points[0], reduced)
        self.assertIn(points[-1], reduced)
        self.assertIn(points[23], reduced)
        self.assertIn(points[24], reduced)
        self.assertIn(points[25], reduced)

    def test_reduction_does_not_bridge_omitted_gaps(self):
        points = [(i, i, i, float(i) if i % 2 == 0 else None, "good" if i % 2 == 0 else "missing") for i in range(200)]
        reduced = self.store.reduce(points, 16)
        self.assertLessEqual(len(reduced), 16)
        for left, right in zip(reduced, reduced[1:]):
            if left[3] is not None and right[3] is not None:
                self.assertFalse(any(p[3] is None for p in points[left[0] + 1:right[0]]))

    def test_sustained_virtual_hour_respects_buffers_across_generations(self):
        config = copy.deepcopy(self.config)
        config["measurements"].update(max_series=128, max_total_samples=250000, preconfigured=["source.*"])
        store = MeasurementStore(config)
        snapshot = self.snapshot()
        snapshot["components"]["source"]["measurements"] = {f"signal_{i}": float(i) for i in range(128)}
        clock = [0.0]
        with patch("ose_ws.services.measurements.time.monotonic", side_effect=lambda: clock[0]), patch(
                "ose_ws.services.measurements.time.time", side_effect=lambda: 1700000000 + clock[0]):
            for step in range(7201):
                clock[0] = step * .5
                snapshot["observation"]["components"]["source"]["generation"] = str(step // 600)
                store.observe(snapshot)
                if step % 100 == 0:
                    self.assertLessEqual(store.allocated_bytes, 32 * 1024**2)
                    self.assertLessEqual(len(store.order), 250000)
            self.assertEqual(len(store.active()), 128)
            self.assertLessEqual(store.allocated_bytes, 32 * 1024**2)
            self.assertTrue(store.truncated)


class StorageTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        path = Path(self.temp.name) / "console.json"
        initialize(path)
        self.config = read(path)
        self.store = StateStore(self.config)
        await self.store.open()
        self.addAsyncCleanup(self.store.close)
        self.session = Session("session", "operator", "csrf", time.time(), time.time(), {"admin": ["*"]})

    async def test_uncertain_write_is_never_replayed_and_can_be_closed(self):
        runner = Operations(self.store)
        calls = []
        async def effect():
            calls.append(1)
            raise ConnectionError("Acknowledgement lost")
        first = await runner.run(self.session, "parameters/block", "same-key", {}, effect)
        second = await runner.run(self.session, "parameters/block", "same-key", {}, effect)
        self.assertEqual(first["id"], second["id"])
        self.assertEqual(second["status"], "uncertain")
        self.assertEqual(len(calls), 1)
        closed = await self.store.close_unknown(first["id"], "administrator", "Inspected current state; historical outcome remains unknown.")
        self.assertEqual(closed["status"], "closed_unknown")
        self.assertEqual(closed["result"]["result"], "undetermined")
        self.assertIsNotNone(closed["idempotency_expires_at"])
        third = await runner.run(self.session, "parameters/block", "same-key", {}, effect)
        self.assertEqual(third["status"], "closed_unknown")
        self.assertEqual(len(calls), 1)

    async def test_idempotency_conflict_and_unresolved_quota(self):
        self.config["limits"]["max_operations"] = 1
        await self.store.intention("operator", "target", "request-one", {"a": 1})
        with self.assertRaises(APIError) as conflict:
            await self.store.intention("operator", "target", "request-one", {"a": 2})
        self.assertEqual(conflict.exception.status, 409)
        with self.assertRaises(APIError) as quota:
            await self.store.intention("operator", "target", "request-two", {})
        self.assertEqual(quota.exception.code, "OPERATION_QUOTA")

    async def test_second_owner_is_refused(self):
        second = StateStore(self.config)
        try:
            with self.assertRaises(APIError) as refused:
                await second.open()
            self.assertEqual(refused.exception.code, "STATE_IN_USE")
            self.assertIn(f"PID {os.getpid()}", refused.exception.message)
            self.assertFalse(second.failed)
        finally:
            await second.close()

    async def test_legacy_empty_lock_reports_another_console_and_stale_pid_does_not_block(self):
        # Existing versions left an empty lock file. OS ownership, not its text,
        # determines whether the next server may start.
        await self.store.call(lambda: (self.store.lock.seek(0), self.store.lock.truncate(), self.store.lock.flush()))
        second = StateStore(self.config)
        try:
            with self.assertRaises(APIError) as refused:
                await second.open()
            self.assertEqual(refused.exception.code, "STATE_IN_USE")
            self.assertIn("Another ose-ws process", refused.exception.message)
        finally:
            await second.close()
        config = copy.deepcopy(self.config)
        root = Path(self.temp.name) / "unused-state"
        root.mkdir()
        (root / "server.lock").write_text("99999999\n")
        config["state_dir"] = str(root)
        replacement = StateStore(config)
        try:
            await replacement.open()
            self.assertEqual((root / "server.lock").read_text(), f"{os.getpid()}\n")
        finally:
            await replacement.close()

    async def test_emergency_stop_survives_storage_failure(self):
        async def unavailable(*args):
            raise APIError(503, "STORAGE_UNAVAILABLE", "Storage unavailable")
        self.store.intention = unavailable
        runner = Operations(self.store)
        calls = []
        async def stop():
            calls.append(1)
            return {"state": "STOPPED"}
        result = await runner.run(self.session, "recordings/one/stop", "stop-key", {}, stop, emergency_stop=True)
        self.assertEqual(calls, [1])
        self.assertFalse(result["durable"])
        self.assertEqual(result["result"]["state"], "STOPPED")
        with self.assertRaises(APIError):
            await runner.run(self.session, "parameters/block", "write-key", {}, stop)

    async def test_restart_marks_active_captures_interrupted_and_pending_writes_uncertain(self):
        config = copy.deepcopy(self.config)
        config["state_dir"] = str(Path(self.temp.name) / "recovery-state")
        first = StateStore(config)
        await first.open()
        intent, _ = await first.intention("operator", "parameters/block", "recovery-key", {"value": 1})
        await first.save_capture({"id": "capture", "owner": "operator", "revision": 1, "state": "RECORDING", "settings": {}})
        await first.close()
        recovered = StateStore(config)
        try:
            await recovered.open()
            self.assertEqual((await recovered.captures())[0]["state"], "INTERRUPTED")
            self.assertEqual((await recovered.operation(intent["id"]))["status"], "uncertain")
            repeated, created = await recovered.intention("operator", "parameters/block", "recovery-key", {"value": 1})
            self.assertFalse(created)
            self.assertEqual(repeated["id"], intent["id"])
        finally:
            await recovered.close()

    async def test_deleted_capture_stays_deleted_and_operation_replays_after_restart(self):
        config = copy.deepcopy(self.config)
        config["state_dir"] = str(Path(self.temp.name) / "deleted-state")
        first = StateStore(config)
        await first.open()
        try:
            capture = {"id": "discarded", "owner": self.session.actor, "revision": 1, "state": "DRAFT", "settings": {}}
            await first.save_capture(capture)
            recordings = RecordingManager(config, None, first)
            recordings.objects[capture["id"]] = capture
            target, body = "recordings/discarded/delete", {"delete_files": False}
            operation = await Operations(first).run(self.session, target, "delete-once", body,
                                                    lambda: recordings.delete(self.session, "discarded", False))
            self.assertEqual(operation["status"], "complete")
        finally:
            await first.close()
        recovered = StateStore(config)
        await recovered.open()
        try:
            self.assertEqual(await recovered.captures(), [])
            async def forbidden():
                self.fail("A completed deletion must never execute again.")
            repeated = await Operations(recovered).run(self.session, target, "delete-once", body, forbidden, replay_only=True)
            self.assertEqual(repeated["id"], operation["id"])
            self.assertEqual(repeated["status"], "complete")
        finally:
            await recovered.close()

    async def test_closure_releases_unresolved_quota_without_forgetting_the_key(self):
        self.config["limits"]["max_unresolved_operations"] = 1
        operation, _ = await self.store.intention("operator", "target", "unknown-key", {})
        await self.store.finish(operation["id"], "uncertain", {})
        with self.assertRaises(APIError) as raised:
            await self.store.intention("operator", "target", "new-key-value", {})
        self.assertEqual(raised.exception.code, "UNRESOLVED_OPERATION_QUOTA")
        await self.store.close_unknown(operation["id"], "admin", "Outcome reviewed and left undetermined.")
        _, created = await self.store.intention("operator", "target", "new-key-value", {})
        self.assertTrue(created)
        original, created = await self.store.intention("operator", "target", "unknown-key", {})
        self.assertFalse(created)
        self.assertEqual(original["status"], "closed_unknown")
