"""Explicit opt-in: cordon a node and replace ONLY analyzer_1, then uncordon."""
import json
import os
import subprocess
import time
import unittest


@unittest.skipUnless(os.environ.get("VIBRATION_CLUSTER_TESTS") == "1", "set VIBRATION_CLUSTER_TESTS=1 on a disposable test cluster")
class ClusterReplacementTests(unittest.TestCase):
    def test_relocates_analyzer_without_replacing_survivors(self):
        namespace = os.environ.get("NAMESPACE", "vibration-watch")
        def kubectl(*args):
            return subprocess.check_output(["kubectl", *args], text=True, timeout=40)
        def pods():
            return {p["metadata"]["labels"]["ose/component"]: p for p in
                    json.loads(kubectl("-n", namespace, "get", "pods", "-l", "app.kubernetes.io/name=ose", "-o", "json"))["items"]
                    if not p["metadata"].get("deletionTimestamp")}
        before = pods()
        old = before["ose-analyzer-1"]
        node = old["spec"]["nodeName"]
        nodes = json.loads(kubectl("get", "nodes", "-o", "json"))["items"]
        self.assertGreaterEqual(sum(not n["spec"].get("unschedulable", False) for n in nodes), 2)
        self.assertFalse(next(n for n in nodes if n["metadata"]["name"] == node)["spec"].get("unschedulable", False))
        try:
            kubectl("cordon", node)
            kubectl("-n", namespace, "delete", "pod", old["metadata"]["name"], "--wait=true")
            deadline = time.monotonic() + 120
            while time.monotonic() < deadline:
                after = pods()
                new = after.get("ose-analyzer-1")
                if new and new["metadata"]["uid"] != old["metadata"]["uid"] and any(
                        c["type"] == "Ready" and c["status"] == "True" for c in new.get("status", {}).get("conditions", [])):
                    break
                time.sleep(1)
            else:
                self.fail("analyzer was not replaced")
            self.assertNotEqual(new["spec"]["nodeName"], node)
            for name, pod in before.items():
                if name != "ose-analyzer-1":
                    self.assertEqual(after[name]["metadata"]["uid"], pod["metadata"]["uid"])
            # Verify application registration and actual new direct traffic, not only pod readiness.
            coordinator = after["ose-coordinator"]["metadata"]["name"]
            deadline = time.monotonic() + 30
            while time.monotonic() < deadline:
                status = json.loads(kubectl("-n", namespace, "exec", coordinator, "--", "ose", "cluster-status",
                    "--tls-ca", "/run/ose-tls/ca.crt", "--tls-cert", "/run/ose-tls/tls.crt", "--tls-key", "/run/ose-tls/tls.key"))
                metrics = status["components"].get("analyzer_1", {}).get("metrics", {})
                if metrics.get("direct_bytes_received", 0) > 0:
                    break
                time.sleep(1)
            else:
                self.fail("replacement analyzer did not receive signal data")
        finally:
            kubectl("uncordon", node)
