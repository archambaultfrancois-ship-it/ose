"""V2 validates activation paths rather than rejecting every data cycle."""
import copy
import json
from pathlib import Path
import tempfile
import unittest

from ose.modeling.activation import activation_graph, activation_order, component_inputs
from ose.modeling.model import load
from ose.modeling.validation import validate_graph


def feedback_model():
    return dict(version=2, schemas="value.sdl", components={
        "a": dict(inputs={
            "clock": dict(kind="timer", timer=dict(mode="periodic", delay=.01),
                          activates=dict(timeout=["out"])),
            "feedback": dict(root="Value", mode="pull")}, outputs={"out": dict(root="Value")}),
        "b": dict(inputs={"input": dict(root="Value", activates=dict(message=["out"]))},
                  outputs={"out": dict(root="Value")})}, subscriptions=[
            {"id": "forward", "from": "a.out", "to": "b.input"},
            {"id": "feedback", "from": "b.out", "to": "a.feedback"}])


class ActivationModelTests(unittest.TestCase):
    def setUp(self):
        self.model = feedback_model()
        self.deployment = dict(version=1, components={
            name: dict(container=dict(language="python", handler=f"{name}.py"))
            for name in self.model["components"]})

    def test_pull_feedback_needs_neither_initial_value_nor_latest_policy(self):
        validate_graph(self.model, self.deployment)
        order = activation_order(self.model)
        self.assertNotIn("a.feedback:message", order)
        self.assertLess(order.index("a.clock:timeout"), order.index("a.out:publish"))
        self.assertLess(order.index("a.out:publish"), order.index("b.input:message"))

    def test_rust_publication_id_method_names_are_checked_before_generation(self):
        self.deployment["components"]["a"]["container"] = dict(language="rust", handler="a.rs")
        self.model["components"]["a"]["outputs"]["out_with_id"] = dict(root="Value")
        with self.assertRaisesRegex(ValueError, "Rust output names conflict"):
            validate_graph(self.model, self.deployment)

    def test_c_publication_id_function_names_are_checked_before_generation(self):
        self.deployment["components"]["a"]["container"] = dict(language="c", handler="a.c")
        self.model["components"]["a"]["outputs"]["out_with_id"] = dict(root="Value")
        with self.assertRaisesRegex(ValueError, "C output names conflict"):
            validate_graph(self.model, self.deployment)

    def test_push_feedback_can_compute_without_authorizing_an_output(self):
        self.model["components"]["a"]["inputs"]["feedback"]["mode"] = "push"
        validate_graph(self.model, self.deployment)
        order = activation_order(self.model)
        self.assertLess(order.index("b.out:publish"), order.index("a.feedback:message"))

    def test_activating_feedback_reports_input_event_and_output_cycle(self):
        feedback = self.model["components"]["a"]["inputs"]["feedback"]
        feedback.update(mode="push", activates=dict(message=["out"]))
        with self.assertRaisesRegex(ValueError, "activation graph contains a cycle") as raised:
            activation_order(self.model)
        validate_graph(self.model, self.deployment)
        for location in ("a.feedback:message", "a.out:publish", "b.input:message", "b.out:publish"):
            self.assertIn(location, str(raised.exception))

    def test_timeout_outputs_do_not_make_receipt_activate_outputs(self):
        self.model["components"]["a"]["inputs"]["feedback"].update(
            mode="push", timeout=.1, activates=dict(timeout=["out"]))
        validate_graph(self.model, self.deployment)

    def test_compiled_external_push_topic_is_not_an_internal_output_dependency(self):
        sub = self.model['subscriptions'][0]
        sub['from_topic'] = sub['from'] = 'replay.signal'
        self.model['external_topics'] = {'replay.signal': {'root': 'Value'}}
        graph = activation_graph(self.model)
        self.assertEqual(graph['b.input:message'], set())
        self.assertEqual(graph['b.out:publish'], {'b.input:message'})
        self.assertIn('b.input:message', activation_order(self.model))

    def test_timer_is_not_a_topic_and_cannot_receive_a_subscription(self):
        self.model["subscriptions"][-1]["to"] = "a.clock"
        with self.assertRaisesRegex(ValueError, "timer input a.clock"):
            validate_graph(self.model, self.deployment)

    def test_pull_cannot_activate_or_wait_for_a_timeout(self):
        for change in (dict(timeout=.1), dict(activates=dict(message=["out"]))):
            model = copy.deepcopy(self.model)
            model["components"]["a"]["inputs"]["feedback"].update(change)
            with self.subTest(change=change), self.assertRaises(ValueError):
                validate_graph(model, self.deployment)

    def test_rejects_legacy_joint_readiness_fields(self):
        for where, change in (("node", dict(trigger=dict(mode="on_change"))),
                              ("input", dict(required=False))):
            model = copy.deepcopy(self.model)
            target = model["components"]["a"]
            (target if where == "node" else target["inputs"]["feedback"]).update(change)
            with self.subTest(where=where), self.assertRaises(ValueError):
                validate_graph(model, self.deployment)

    def test_rejects_invalid_timer_and_event_declarations(self):
        invalid = [dict(kind="timer", root="Value", timer=dict(mode="periodic", delay=.1)),
                   dict(kind="timer", timer=dict(mode="unknown", delay=.1)),
                   dict(kind="timer", timer=dict(mode="periodic", delay=0)),
                   dict(kind="timer", timer=dict(mode="periodic", delay=True)),
                   dict(kind="timer", timer=dict(mode="periodic", delay=.1, start=True)),
                   dict(kind="timer", timer=dict(mode="one_shot", delay=.1), activates=dict(message=["out"])),
                   dict(root="Value", activates=dict(message=["missing"])),
                   dict(root="Value", activates=dict(message=["out", "out"])),
                   dict(root="Value", activates=dict(message="out")),
                   dict(root="Value", activates=dict(timeout=["out"])),
                   dict(root="Value", mode="unknown"), dict(root="Value", kind="unknown"),
                   dict(root="Value", timeout=float("inf")), dict(root="Value", timeout=-1)]
        for desc in invalid:
            model = copy.deepcopy(self.model)
            model["components"]["a"]["inputs"]["clock"] = desc
            with self.subTest(desc=desc), self.assertRaises(ValueError):
                validate_graph(model, self.deployment)

    def test_normalized_declarations_are_immutable_and_do_not_mutate_model(self):
        before = copy.deepcopy(self.model)
        inputs = component_inputs(self.model["components"]["a"])
        self.assertEqual(inputs["clock"].timeout_ns, 10_000_000)
        self.assertTrue(inputs["clock"].automatic)
        self.assertEqual(inputs["feedback"].mode, "pull")
        with self.assertRaises(TypeError):
            inputs["clock"].outputs["timeout"] = frozenset()
        self.assertEqual(self.model, before)

    def test_shared_reception_constraints_still_apply_in_v2(self):
        self.model["subscriptions"].append(dict(id="second", **{
            "from": "a.out", "to": "a.feedback", "qos": dict(capacity=2)}))
        with self.assertRaisesRegex(ValueError, "a.feedback.*reception"):
            validate_graph(self.model, self.deployment)

    def test_application_version_is_explicit_and_does_not_version_deployment(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "model.json"
            path.write_text(json.dumps(self.model))
            self.assertEqual(load(path, version=2), self.model)
            with self.assertRaisesRegex(ValueError, "version 1 document"):
                load(path)
        bad = dict(self.deployment, version=2)
        with self.assertRaisesRegex(ValueError, "version 1 deployment"):
            validate_graph(self.model, bad)
        for version in (True, 2.0, 3):
            with self.subTest(version=version), self.assertRaises(ValueError):
                validate_graph(dict(self.model, version=version), self.deployment)
