import json
import os
from pathlib import Path
import subprocess
import sys
import unittest

ROOT = Path(__file__).resolve().parents[2]


def invoke(*args):
    return subprocess.run([sys.executable, "tools/vibration.py", *args], cwd=ROOT, env=os.environ,
                          check=True, text=True, capture_output=True, timeout=180)


def observations(text):
    return [json.loads(line) for line in text.splitlines() if line.startswith('{"event": "vibration"')]


class ApplicationTests(unittest.TestCase):
    def test_normal_and_increasing_profiles_and_deterministic_simulation(self):
        expected = json.loads((ROOT / "tests/fixtures/expected.json").read_text())
        normal = observations(invoke("simulate", "--scenario", "normal", "--duration", "2", "--workers", "1").stderr)
        self.assertEqual(len(normal), 40)
        self.assertTrue(all(event["state"] == 0 for event in normal))
        first = invoke("simulate", "--duration", "2", "--workers", "1")
        trace = (ROOT / "build/simulation.jsonl").read_bytes()
        parallel = invoke("simulate", "--duration", "2", "--workers", "4")
        self.assertEqual(trace, (ROOT / "build/simulation.jsonl").read_bytes())
        rows = observations(parallel.stderr)
        self.assertEqual(len(rows), 40)
        motor_1 = [row for row in rows if row["machine_id"] == 1]
        self.assertEqual(motor_1[0]["state"], 0)
        self.assertEqual(motor_1[-1]["state"], 1)
        alarms = [row for row in motor_1 if row["state"]]
        self.assertEqual(alarms[0]["sequence"], expected["increasing_vibration_first_alarm_sequence"])
        self.assertEqual(len(alarms), expected["increasing_vibration_alarm_count"])
        self.assertTrue(all(row["state"] == 0 for row in rows if row["machine_id"] == 2))
        self.assertEqual(sorted(observations(first.stderr), key=lambda row: (row["machine_id"], row["sequence"])),
                         sorted(rows, key=lambda row: (row["machine_id"], row["sequence"])))

    def test_realtime_and_distributed_replacement(self):
        real = observations(invoke("run", "--duration", "2.5").stderr)
        self.assertEqual({row["machine_id"] for row in real}, {1, 2})
        self.assertTrue(any(row["state"] for row in real))
        demo = invoke("distributed-local", "--duration", "6", "--fail-node", "analyzer_1")
        rows = observations(demo.stderr)
        self.assertEqual({row["machine_id"] for row in rows}, {1, 2})
        self.assertTrue(any(row["state"] for row in rows if row["machine_id"] == 1))
        state = json.loads((ROOT / "build/distributed-status.json").read_text())
        self.assertEqual(set(state["components"]), {"sensor_1", "sensor_2", "analyzer_1", "analyzer_2", "alarm", "observer"})
        self.assertGreaterEqual(state["metrics"]["registrations"], 7)
        self.assertGreater(state["components"]["analyzer_1"]["metrics"].get("direct_bytes_received", 0), 0)
        self.assertNotIn("signal_1", state["subscriptions"])
        pids = json.loads((ROOT / "build/distributed-processes.json").read_text())
        self.assertNotEqual(pids["initial"]["analyzer_1"], pids["final"]["analyzer_1"])
        for name in pids["initial"]:
            if name != "analyzer_1":
                self.assertEqual(pids["initial"][name], pids["final"][name])
        broker = invoke("distributed-local", "--duration", "3", "--routing", "broker")
        self.assertTrue(observations(broker.stderr))
        state = json.loads((ROOT / "build/distributed-status.json").read_text())
        self.assertIn("signal_1", state["subscriptions"])
        self.assertEqual(state["components"]["analyzer_1"]["metrics"].get("direct_bytes_received", 0), 0)
