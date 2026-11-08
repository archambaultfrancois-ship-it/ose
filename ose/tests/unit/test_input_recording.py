import struct
from pathlib import Path
import unittest
from unittest.mock import Mock

from ose.codec.sdl2 import SDL2
from ose.codec.catalogue import CatalogueTypes
from ose.delivery.policies import SubscriptionPolicy
from ose.recording.inputs import InputRecording, input_points


CAT = "SDL2|Value{1:uint64:sequence;2:fl64:value;}"


class InputRecordingTests(unittest.TestCase):
    def setUp(self):
        self.codec = SDL2(Path(__file__).resolve().parents[3] / "sdl")
        self.node = dict(inputs={"value": dict(root="Value", catalogue=CAT)}, outputs={})
        self.active, self.emit = set(), Mock()
        self.recording = InputRecording(self.codec, self.node, lambda: self.active, self.emit)

    def record(self, catalogue=CAT, payload=None):
        payload = b"\x01" + struct.pack(">Qd", 1, 2.) if payload is None else payload
        return dict(catalogue=catalogue, payload=payload, body=self.codec.wrap(catalogue, payload))

    def test_inactive_inputs_never_decode_or_emit(self):
        self.recording.observe({"value": dict(catalogue="invalid")}, 1)
        self.emit.assert_not_called()
        self.assertEqual(len(self.recording.adapters.cache), 0)

    def test_latest_repeated_ticks_and_nulls_follow_selected_values(self):
        self.active.add("value")
        record = self.record()
        self.recording.observe({"value": record}, 1)
        self.recording.observe({"value": record}, 2)
        self.recording.observe({"value": None}, 3)
        self.assertEqual([call.args[3] for call in self.emit.call_args_list], [1, 2])
        self.assertIs(self.emit.call_args.args[2], record["body"])

    def test_compatible_source_is_projected_to_input_catalogue(self):
        self.active.add("value")
        source = "SDL2|Value{1:uint64:old_sequence;2:fl64:old_value;3:string:extra;}"
        types = CatalogueTypes(self.codec.api, source)
        payload = self.codec.api.encode(types.context,
            types.types["Value"](old_sequence=3, old_value=4., extra="abc"))
        self.recording.observe({"value": self.record(source, payload)}, 7)
        port, desc, envelope, tick = self.emit.call_args.args
        self.assertEqual((port, desc["catalogue"], tick), ("value", CAT, 7))
        self.assertEqual(self.codec.validate(CAT, "Value", envelope).fields, dict(sequence=3, value=4.))

    def test_input_discovery_retains_natural_name_and_separates_same_named_output(self):
        self.node["outputs"]["value"] = dict(root="Value", catalogue=CAT)
        plan = dict(components={"node": self.node}, subscriptions=[
            dict(id=name, **{"from": name + ".out", "to": "node.value"}, policy=SubscriptionPolicy().to_dict())
            for name in ("a", "b")])
        points = input_points(plan, "node", "receiver-generation", True)
        self.assertEqual(list(points), ["input:node.value"])
        self.assertEqual(points["input:node.value"]["topic"], "node.value")
        self.assertEqual(points["input:node.value"]["kind"], "input")
        self.assertEqual(points["input:node.value"]["sources"], ["a.out", "b.out"])
