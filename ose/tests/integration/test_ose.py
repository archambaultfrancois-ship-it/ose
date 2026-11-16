"""Build and run the real C -> Rust -> Python OSE example from an extracted project."""
import asyncio
import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
import sys
import tempfile
import unittest

SDL_ROOT = os.environ.get("SDL_ROOT")


@unittest.skipUnless(SDL_ROOT, "set SDL_ROOT to an independent SDL checkout")
class OseTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.directory = tempfile.TemporaryDirectory()
        cls.addClassCleanup(cls.directory.cleanup)
        original = Path(__file__).resolve().parents[2]
        cls.project = Path(cls.directory.name) / "standalone"
        shutil.copytree(original, cls.project, ignore=shutil.ignore_patterns("build", "__pycache__", "target", "*.egg-info"))
        cls.env = dict(os.environ, SDL_ROOT=str(Path(SDL_ROOT).resolve()), PYTHONPATH=str(cls.project / "src"))
        cls.build = cls.project / "build"
        cls.cli = [sys.executable, "-m", "ose.cli"]
        args = ["--model", "examples/acquisition/model.json", "--deployment", "examples/acquisition/deployment.json"]
        subprocess.run(cls.cli + ["build", *args], cwd=cls.project, env=cls.env, check=True, capture_output=True, timeout=120)

    async def observe_until(self, predicate):
        process = await asyncio.create_subprocess_exec(*self.cli, "run", cwd=self.project, env=self.env,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        observations, output = [], []

        def collect(line):
            if line.startswith(b'{"observer"'):
                observations.append(json.loads(line))

        async def observe():
            while line := await process.stderr.readline():
                output.append(line)
                collect(line)
                if predicate(observations):
                    return
            self.fail("OSE stopped before the expected observations:\n" + b"".join(output).decode())

        try:
            await asyncio.wait_for(observe(), 30)
        finally:
            if process.returncode is None:
                process.send_signal(signal.SIGINT)
            try:
                _, remaining = await asyncio.wait_for(process.communicate(), 10)
            except asyncio.TimeoutError:
                process.kill()
                await process.communicate()
                raise
        self.assertEqual(process.returncode, 0, (b"".join(output) + remaining).decode())
        for line in remaining.splitlines():
            collect(line)
        return observations

    def test_multilanguage_ose_and_restart(self):
        def enough_observations(rows):
            return (sum(row["observer"] == "observation" for row in rows) >= 20
                    and sum(row["observer"] == "affichage" for row in rows) >= 3)

        observations = asyncio.run(self.observe_until(enough_observations))
        values = [row for row in observations if row["observer"] == "observation"]
        display = [row for row in observations if row["observer"] == "affichage"]
        self.assertGreater(len(values), 10)
        self.assertGreater(len(display), 2)
        self.assertLess(len(display), len(values))
        self.assertTrue(all(row["value"] == 2 * row["sequence"] for row in values + display))
        self.assertEqual([r["sequence"] for r in values], sorted(set(r["sequence"] for r in values)))
        status = json.loads((self.build / "last-status.json").read_text())
        self.assertGreater(status["subscriptions"]["analyse-observation"]["acknowledged"], 10)
        self.assertGreater(status["components"]["affichage"]["inputs"]["input"]["replaced"], 0)
        restarted = asyncio.run(self.observe_until(lambda rows: len(rows) > 2))
        self.assertGreater(len(restarted), 2)
        self.assertEqual(min(row["sequence"] for row in restarted), 1)
        self.assertFalse((self.build / "state").exists())
        self.assertFalse(list(self.build.rglob("*.sqlite*")))
        self.assertFalse(list(self.build.glob("*.jsonl")))

    def test_failure_stops_ose(self):
        plan = json.loads((self.build / "plan.json").read_text())
        plan["components"]["acquisition"]["command"] = [sys.executable, "-c", "import sys;sys.exit(23)"]
        bad = self.build / "bad-plan.json"
        bad.write_text(json.dumps(plan))
        result = subprocess.run(self.cli + ["run", "--plan", str(bad), "--duration", "1"], cwd=self.project, env=self.env, capture_output=True, timeout=15)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn(b"startup", result.stderr)

    def test_reject_incompatible_schema_and_accept_nested(self):
        from ose.modeling.compiler import validate
        temporary = self.build / "validation"
        temporary.mkdir(exist_ok=True)
        (temporary / "publisher.sdl").write_text("message Measurement {1: uint64 sequence; 2: fl64 value;}")
        (temporary / "bad.sdl").write_text("message Measurement {1: uint64 sequence; 2: string value;}")
        model = {"version": 2, "schemas": "publisher.sdl", "components": {
            "source": {"inputs": {"clock": {"kind": "timer", "timer": {"mode": "periodic", "delay": .01},
                                          "activates": {"timeout": ["out"]}}}, "outputs": {"out": {"root": "Measurement"}}},
            "sink": {"inputs": {"in": {"root": "Measurement", "schema": "bad.sdl"}}, "outputs": {}}},
            "subscriptions": [{"id": "link", "from": "source.out", "to": "sink.in"}]}
        deployment = {"version": 1, "components": {name: {"command": [sys.executable]} for name in model["components"]}}
        m, d = temporary / "model.json", temporary / "deployment.json"
        m.write_text(json.dumps(model)); d.write_text(json.dumps(deployment))
        with self.assertRaisesRegex(ValueError, "FIELD_TYPE_MISMATCH"):
            validate(m, d, SDL_ROOT)
        nested = "message Measurement {1: Point value;} message Point {1: int32 x;}"
        (temporary / "publisher.sdl").write_text(nested)
        (temporary / "bad.sdl").write_text(nested)
        validate(m, d, SDL_ROOT)
