"""Migrated examples execute with single-input callbacks and explicit timers."""
import asyncio
import copy
import json
import os
from pathlib import Path
import shutil
import tempfile
import unittest

from ose.execution.simulation import Simulation
from ose.modeling.compiler import build

ROOT = Path(__file__).resolve().parents[2]
SDL_ROOT = os.environ.get("SDL_ROOT", str(ROOT.parent / "sdl"))


class ExampleInputTests(unittest.IsolatedAsyncioTestCase):
    async def test_shared_parameter_timers_keep_deterministic_updates(self):
        source = ROOT / "examples/shared_parameters"
        with tempfile.TemporaryDirectory(prefix="ose parameter example ") as directory:
            root = Path(directory)
            path = build(source / "model.json", source / "deployment.json", SDL_ROOT, root / "build")
            plan = json.loads(path.read_text())
            traces = []
            for workers in (1, 4):
                trace = root / ("trace-" + str(workers) + ".jsonl")
                summary = await Simulation(copy.deepcopy(plan), 2, workers=workers,
                    scenario=source / "scenario.json", trace=trace).execute()
                self.assertEqual(summary["ticks"], 38)
                self.assertEqual(summary["parameter_changes"], 1)
                self.assertEqual(summary["publications"], 0)
                traces.append(trace.read_bytes())
            self.assertEqual(traces[0], traces[1])

    async def test_four_language_subset_preserves_branch_join_results(self):
        # Explicitly exclude Ada: this qualifies the changed Python/C/Rust/Java
        # handlers, not the complete five-language example or the Ada SDK.
        with tempfile.TemporaryDirectory(prefix="ose multilingual subset ") as directory:
            root = Path(directory) / "example"
            shutil.copytree(ROOT / "examples/multilingual", root)
            model = json.loads((root / "model.json").read_text())
            del model["components"]["ada_worker"]
            del model["components"]["join"]["inputs"]["ada_worker"]
            model["subscriptions"] = [sub for sub in model["subscriptions"]
                                      if sub["from"].split(".")[0] != "ada_worker"
                                      and sub["to"] not in ("ada_worker.input", "join.ada_worker")]
            deployment = json.loads((root / "deployment.json").read_text())
            del deployment["components"]["ada_worker"]
            join = root / "components/join.py"
            join.write_text(join.read_text().replace(', "ada_worker"', ''))
            (root / "model.json").write_text(json.dumps(model))
            (root / "deployment.json").write_text(json.dumps(deployment))
            path = await asyncio.to_thread(build, root / "model.json", root / "deployment.json", SDL_ROOT, root / "build")
            plan = json.loads(path.read_text())
            traces = []
            for workers in (1, 4):
                trace = root / ("trace-" + str(workers) + ".jsonl")
                engine = Simulation(copy.deepcopy(plan), .31, workers=workers, trace=trace)
                summary = await engine.execute()
                self.assertEqual(summary["ticks"], 21)
                self.assertEqual(summary["publications"], 15)
                self.assertTrue(all(process.returncode == 0 for process in engine.processes.values()))
                traces.append(trace.read_bytes())
            self.assertEqual(traces[0], traces[1])

