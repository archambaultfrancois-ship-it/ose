"""Directional SDL explanations and contract reads through real OSE runtimes."""
import copy
import unittest
from unittest.mock import patch

import test_api
from ose.delivery.policies import SubscriptionPolicy
from ose.execution.broker import Broker
from ose.execution.distributed import Coordinator
from ose.execution.observation import subscription_schema
from ose_ws.schema.compatibility import diagnose


class DiagnosticTests(unittest.TestCase):
    def compare(self, source, destination, **options):
        return diagnose("SDL2|Root{" + source + "}", "Root",
                        "SDL2|Root{" + destination + "}", options.get("root", "Root"))

    def test_identical_contracts(self):
        result = self.compare("1:uint64:counter;", "1:uint64:counter;")
        self.assertTrue(result["compatible"])
        self.assertTrue(result["identical"])
        self.assertEqual(result["diagnostic"]["code"], "COMPATIBLE")
        self.assertEqual(result["fields"][0]["status"], "matching")
        self.assertEqual(result["source"]["schema_digest"], result["destination"]["schema_digest"])

    def test_matching_by_id_renames_ignored_fields_and_defaults(self):
        result = self.compare("1:uint16:old;2:string:extra;", "1:uint16:renamed;3?:string:label;4+:fl32:samples;")
        self.assertTrue(result["compatible"])
        self.assertFalse(result["identical"])
        fields = {row["field_id"]: row for row in result["fields"]}
        self.assertEqual(fields[1]["destination_path"], "Root.renamed")
        self.assertEqual(fields[1]["findings"][0]["code"], "FIELD_RENAMED")
        self.assertEqual(fields[2]["findings"][0]["code"], "FIELD_IGNORED")
        self.assertEqual(fields[3]["findings"][0]["code"], "FIELD_DEFAULTED")
        self.assertEqual(fields[4]["findings"][0]["code"], "FIELD_DEFAULTED")

    def test_missing_required_fields_are_directional(self):
        forward = self.compare("1:uint8:a;", "1:uint8:a;2:uint8:b;")
        reverse = self.compare("1:uint8:a;2:uint8:b;", "1:uint8:a;")
        self.assertFalse(forward["compatible"])
        self.assertEqual(forward["diagnostic"]["code"], "REQUIRED_FIELD_MISSING")
        self.assertEqual(forward["fields"][1]["status"], "incompatible")
        self.assertTrue(reverse["compatible"])

    def test_reports_all_type_dimension_and_cardinality_errors(self):
        result = self.compare("1:uint8:a;2:fl32[2]:b;3?:string:c;",
                              "1:uint16:a;2:fl32[3]:b;3:string:c;")
        self.assertFalse(result["compatible"])
        self.assertEqual(result["diagnostic"]["code"], "FIELD_TYPE_MISMATCH")
        self.assertEqual([f["findings"][0]["code"] for f in result["fields"]],
            ["FIELD_TYPE_MISMATCH", "FIELD_DIMENSIONS_MISMATCH", "FIELD_CARDINALITY_MISMATCH"])

    def test_packed_repeated_and_wire_settings_are_compatible_differences(self):
        result = self.compare("1+:fl32:samples@b12,q0.1;", "1*:fl32:samples;")
        self.assertTrue(result["compatible"])
        self.assertEqual(result["fields"][0]["status"], "different")
        self.assertEqual([f["code"] for f in result["fields"][0]["findings"]], ["SEQUENCE_ENCODING", "WIRE_DIFFERENCE"])

    def test_nested_fields_use_publisher_paths_and_only_reachable_types(self):
        result = diagnose("SDL2|Child{1:uint8:old;}|Root{1:Child:child;}|Unused{1:uint8:x;}", "Root",
                          "SDL2|Child{1:uint16:new;}|Root{1:Child:renamed;}|Unused{1:string:x;}", "Root")
        self.assertFalse(result["compatible"])
        row = result["fields"][1]
        self.assertEqual(row["path"], "Root.child.old")
        self.assertEqual(row["destination_path"], "Root.renamed.new")
        self.assertEqual(result["diagnostic"]["path"], row["path"])
        self.assertEqual(len(result["fields"]), 2)

    def test_enums_compare_numeric_values_not_labels(self):
        source = "SDL2|Root{1:Mode:mode;}|#Mode{OFF=0;ON=1;}"
        renamed = "SDL2|Root{1:Mode:mode;}|#Mode{DISABLED=0;ENABLED=1;OTHER=2;}"
        result = diagnose(source, "Root", renamed, "Root")
        self.assertTrue(result["compatible"])
        self.assertEqual(result["fields"][0]["findings"][0]["code"], "ENUM_DIFFERENCE")
        reverse = diagnose(renamed, "Root", source, "Root")
        self.assertFalse(reverse["compatible"])
        self.assertEqual(reverse["fields"][0]["findings"][0]["code"], "ENUM_VALUE_UNKNOWN")
        self.assertIn("2", reverse["fields"][0]["findings"][0]["message"])

    def test_type_kind_mismatch(self):
        result = diagnose("SDL2|Item{1:uint8:x;}|Root{1:Item:item;}", "Root",
                          "SDL2|Root{1:Item:item;}|#Item{OFF=0;}", "Root")
        self.assertFalse(result["compatible"])
        self.assertEqual(result["fields"][0]["findings"][0]["code"], "TYPE_KIND_MISMATCH")

    def test_different_roots_are_incompatible_even_if_both_catalogues_contain_them(self):
        catalogue = "SDL2|Other{1:uint8:x;}|Root{1:uint8:x;}"
        result = diagnose(catalogue, "Root", catalogue, "Other")
        self.assertFalse(result["compatible"])
        self.assertEqual(result["diagnostic"]["code"], "ROOT_MISMATCH")
        self.assertEqual(result["fields"], [])

    def test_field_detail_limit_does_not_hide_the_complete_verdict(self):
        fields = "".join(f"{i}:uint8:f{i};" for i in range(1, 260))
        result = self.compare(fields, fields + "260:uint8:missing;")
        self.assertTrue(result["truncated"])
        self.assertEqual(len(result["fields"]), 256)
        self.assertFalse(result["compatible"])
        self.assertEqual(result["diagnostic"]["path"], "Root.missing")

    def test_depth_detail_limit_keeps_the_complete_verdict(self):
        def catalogue(leaf):
            declarations = [f"T{i}{{1:T{i+1}:child;}}" for i in range(18)] + ["T18{1:" + leaf + ":value;}"]
            return "SDL2|" + "|".join(sorted(declarations, key=lambda item: item.split("{")[0]))
        result = diagnose(catalogue("uint8"), "T0", catalogue("uint16"), "T0")
        self.assertTrue(result["truncated"])
        self.assertFalse(result["compatible"])
        self.assertEqual(len(result["fields"]), 16)


class DiagnosticAPITests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp = test_api.APITests.asyncSetUp
    until = test_api.APITests.until

    async def runtime(self, distributed=False):
        node = {"execution_model": 2, "inputs": {"clock": {"kind": "timer", "timer": {"mode": "periodic", "delay": .1},
                "activates": {"timeout": ["value"]}}}, "outputs": {"value": {"root": "Root", "catalogue": "SDL2|Root{1:uint8:old;2:string:extra;}"}}}
        sink = {"inputs": {"input": {"root": "Root", "catalogue": "SDL2|Root{1:uint8:renamed;3?:string:label;}"}},
                "outputs": {}, "execution_model": 2}
        policy = SubscriptionPolicy.resolve().to_dict()
        plan = dict(self.broker.plan, execution="distributed" if distributed else "local", components={"source": node, "sink": sink},
            external_topics={"source.external": copy.deepcopy(node["outputs"]["value"])},
            subscriptions=[{"id": "value/#1", "from": "source.value", "to": "sink.input", "policy": policy,
                            "routing": "direct" if distributed else "broker"},
                           {"id": "external", "from": "source.external", "from_topic": "source.external", "to": "sink.input", "policy": policy}],
            broker={"external": {"enabled": False}})
        runtime = Coordinator(plan) if distributed else Broker(plan)
        await runtime.start()
        self.addAsyncCleanup(runtime.close)
        collector = self.app.state.collector
        await collector.close(); collector.client = None
        self.app.state.connection.config.update(port=runtime.port, distributed=distributed, insecure=distributed)
        await collector.start()
        await self.until(lambda: collector.snapshot.get("epoch") == runtime.epoch)
        return runtime

    async def test_local_distributed_direct_and_external_contracts_without_taps_or_workers(self):
        for distributed in (False, True):
            runtime = await self.runtime(distributed)
            for subscription in ("value/#1", "external"):
                response = await self.client.get("/api/v1/flow/diagnostic", params={"subscription": subscription, "epoch": runtime.epoch})
                self.assertEqual(response.status_code, 200, response.text)
                result = response.json()
                self.assertEqual(result["epoch"], runtime.epoch)
                self.assertEqual(result["subscription"], subscription)
                self.assertTrue(result["compatible"])
                self.assertEqual(result["source"]["endpoint"], "source.external" if subscription == "external" else "source.value")
                self.assertEqual(result["destination"]["endpoint"], "sink.input")
                self.assertEqual(result["fields"][0]["destination"]["name"], "renamed")
                self.assertFalse(runtime.recording.sessions)
                self.assertFalse(self.app.state.flows.sources)

    async def test_permissions_unknown_epoch_and_unavailable_runtime(self):
        runtime = await self.runtime()
        params = {"subscription": "value/#1", "epoch": runtime.epoch}
        signed_in = self.app.state.auth.sessions[self.client.cookies.get("ose_ws_session")]
        with patch.object(signed_in, "permissions", {"components.read": ["*"]}):
            self.assertEqual((await self.client.get("/api/v1/flow/diagnostic", params=params)).status_code, 200)
            self.assertEqual((await self.client.get("/api/v1/flow/diagnostic", params=dict(params, subscription="unknown"))).status_code, 404)
            self.assertEqual((await self.client.get("/api/v1/flow/diagnostic", params=dict(params, epoch="old"))).status_code, 409)
        with patch.object(signed_in, "permissions", {"flows.read": ["*"]}):
            self.assertEqual((await self.client.get("/api/v1/flow/diagnostic", params=params)).status_code, 403)
        with patch.object(self.app.state.collector, "error", "Disconnected"):
            self.assertEqual((await self.client.get("/api/v1/flow/diagnostic", params=params)).status_code, 503)

    async def test_old_runtime_cannot_silently_compare_source_against_itself(self):
        runtime = await self.runtime()
        def legacy(plan, sub, side="source"):
            desc, catalogue = subscription_schema(plan, sub)
            desc.pop("side")
            return desc, catalogue
        with patch("ose.execution.broker.subscription_schema", side_effect=legacy):
            response = await self.client.get("/api/v1/flow/diagnostic", params={"subscription": "value/#1", "epoch": runtime.epoch})
        self.assertEqual(response.status_code, 503, response.text)
        self.assertEqual(response.json()["error"]["code"], "CONTRACT_DIAGNOSTIC_UNSUPPORTED")

    async def test_epoch_changes_during_the_contract_read_are_rejected(self):
        runtime = await self.runtime()
        epoch = runtime.epoch
        def changed(plan, sub, side="source"):
            result = subscription_schema(plan, sub, side)
            if side == "destination":
                # Change the runtime generation itself: a concurrent status poll
                # must not be able to restore the original epoch in this fixture.
                runtime.epoch = "replacement"
            return result
        with patch("ose.execution.broker.subscription_schema", side_effect=changed):
            response = await self.client.get("/api/v1/flow/diagnostic", params={"subscription": "value/#1", "epoch": epoch})
        self.assertEqual(response.status_code, 409, response.text)
        self.assertEqual(response.json()["error"]["code"], "APPLICATION_CHANGED")
