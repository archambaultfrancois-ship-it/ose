"""Runnable models use v2; joins correlate single-input callback deliveries."""
import importlib.util
import itertools
import json
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from ose.modeling.validation import validate_graph

ROOT = Path(__file__).resolve().parents[3]


def handler(relative):
    spec = importlib.util.spec_from_file_location("example_join", ROOT / relative)
    module = importlib.util.module_from_spec(spec)
    with patch.dict("sys.modules", {"measurement": SimpleNamespace(Measurement=SimpleNamespace)}):
        spec.loader.exec_module(module)
    return module


def reference(port, sequence, value):
    return SimpleNamespace(port=port, message=SimpleNamespace(sequence=sequence, value=value))


class RepositoryModelTests(unittest.TestCase):
    def test_authored_models_and_their_deployments_use_v2(self):
        models = [*ROOT.joinpath("ose/examples").glob("*/model.json"), ROOT / "vibration-watch/model.json"]
        for path in models:
            model = json.loads(path.read_text())
            self.assertEqual(model["version"], 2, str(path))
            for node in model["components"].values():
                self.assertNotIn("trigger", node)
                for port in node["inputs"].values():
                    self.assertNotIn("required", port)
            for deployment in path.parent.glob("*deployment*.json"):
                with self.subTest(model=str(path), deployment=deployment.name):
                    validate_graph(model, json.loads(deployment.read_text()))

    def test_two_branch_join_waits_for_both_inputs_and_consumes_one_offset(self):
        module = handler("ose/examples/simulated/join.py")
        for order in (("left", "right"), ("right", "left")):
            results, withdrawals = [], []
            def pull():
                withdrawals.append(1)
                return SimpleNamespace(message=SimpleNamespace(value=10.0))
            ctx = SimpleNamespace(inbox=lambda port: SimpleNamespace(pull=pull),
                                  publish_result=lambda value, **kwargs: results.append((value, kwargs)))
            module.on_start(ctx)
            getattr(module, "on_" + order[0])(ctx, reference(order[0], 7, 2.0))
            self.assertFalse(results)
            self.assertFalse(withdrawals)
            getattr(module, "on_" + order[1])(ctx, reference(order[1], 7, 3.0))
            self.assertEqual([(v.sequence, v.value, ids["id"]) for v, ids in results], [(7, 15.0, 7)])
            self.assertEqual(withdrawals, [1])
            self.assertFalse(ctx.pending)

    def test_four_branch_join_matches_sequences_despite_interleaving(self):
        module = handler("ose/examples/multilingual/components/join.py")
        for order in itertools.permutations(sorted(module.BRANCHES)):
            results = []
            ctx = SimpleNamespace(publish_value=lambda value, **kwargs: results.append(value))
            module.on_start(ctx)
            for index, port in enumerate(order):
                callback = getattr(module, "on_" + port)
                callback(ctx, reference(port, 2, index + .5))
                callback(ctx, reference(port, 1, index + 1.0))
            self.assertEqual([(v.sequence, v.value) for v in results], [(2, 8.0), (1, 10.0)])
            self.assertFalse(ctx.pending)

    def test_incomplete_join_state_is_bounded_and_duplicates_are_explicit(self):
        for path, port in (("ose/examples/simulated/join.py", "left"),
                           ("ose/examples/multilingual/components/join.py", "c_worker")):
            with self.subTest(handler=path):
                module = handler(path)
                ctx = SimpleNamespace()
                module.on_start(ctx)
                callback = getattr(module, "on_" + port)
                for sequence in range(1024):
                    callback(ctx, reference(port, sequence, 1.0))
                with self.assertRaisesRegex(RuntimeError, "1024 incomplete"):
                    callback(ctx, reference(port, 1024, 1.0))
                with self.assertRaisesRegex(ValueError, "duplicate branch"):
                    callback(ctx, reference(port, 0, 1.0))

