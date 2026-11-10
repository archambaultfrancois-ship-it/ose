"""Flow metadata comes from the shared status snapshot, including offline views."""
import json
from types import SimpleNamespace
import unittest

from ose.execution.observation import describe
from ose_ws.services.status import StatusCollector


class FlowStatusTests(unittest.TestCase):
    def test_model_metadata_preserves_external_and_direct_links_without_secrets(self):
        plan = {"components": {
            "source": {"inputs": {"clock": {"kind": "timer", "timer": {"mode": "periodic", "delay": .1},
                        "activates": {"timeout": ["value"]}}},
                       "outputs": {"value": {"root": "Value", "catalogue": "secret"}}, "env": {"TOKEN": "secret"}},
            "sink": {"inputs": {"input": {"root": "Value", "mode": "push", "timeout": 1},
                                "offset": {"root": "Value", "mode": "pull"}}, "outputs": {}},
            "isolated": {"inputs": {}, "outputs": {}}},
            "external_topics": {"source.offset": {"root": "Value", "catalogue": "secret"}},
            "subscriptions": [
                {"id": "direct", "from": "source.value", "to": "sink.input", "routing": "direct", "policy": {"buffer": "latest"}},
                {"id": "offset", "from": "source.offset", "from_topic": "source.offset", "to": "sink.offset", "policy": {}}]}
        observation = describe(plan)
        self.assertNotIn("secret", json.dumps(observation))
        self.assertEqual(observation["external_topics"], {"source.offset": {"root": "Value"}})
        self.assertEqual(observation["components"]["source"]["outputs"], {"value": {"root": "Value"}})
        self.assertEqual(observation["components"]["sink"]["inputs"]["input"]["root"], "Value")
        collector = StatusCollector({"collection": {"diagnostic_window_s": 5}},
                                    SimpleNamespace(public=lambda: {}), None)
        self.assertIsNone(collector.diagnostics()["topology"])
        collector.snapshot = {"observation": observation}
        collector.error = None
        collector.report = {"components": []}
        topology = collector.diagnostics()["topology"]
        self.assertEqual(len(topology["components"]), 3)
        self.assertEqual(topology["components"]["source"]["inputs"]["clock"], plan["components"]["source"]["inputs"]["clock"])
        self.assertEqual(topology["components"]["sink"]["inputs"]["offset"]["mode"], "pull")
        self.assertEqual(topology["subscriptions"]["offset"]["from_topic"], "source.offset")
        self.assertEqual(topology["subscriptions"]["direct"]["routing"], "direct")
        # A connection failure retains the topology and explicitly marks the report.
        collector.error = "Disconnected"
        offline = collector.diagnostics()
        self.assertEqual(offline["topology"], topology)
        self.assertEqual(offline["error"], "Disconnected")
        collector.snapshot = {"observation": {"version": 1, "components": {}, "subscriptions": {}}}
        self.assertEqual(collector.diagnostics()["topology"]["components"], {})

    def test_older_observation_metadata_needs_no_local_model_file(self):
        collector = StatusCollector({"collection": {"diagnostic_window_s": 5}},
                                    SimpleNamespace(public=lambda: {}), None)
        collector.snapshot = {"observation": {"version": 1, "components": {
            "sink": {"trigger": {"mode": "on_change"}, "inputs": {}, "connected": False}},
            "subscriptions": {"external": {"from": "offset", "to": "sink.offset", "policy": {}}}}}
        topology = collector.diagnostics()["topology"]
        self.assertNotIn("connected", topology["components"]["sink"])
        self.assertNotIn("trigger", topology["components"]["sink"])
        self.assertEqual(topology["external_topics"], {})
        self.assertEqual(topology["subscriptions"]["external"]["from"], "offset")
