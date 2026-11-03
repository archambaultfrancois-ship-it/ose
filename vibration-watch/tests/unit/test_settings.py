"""Business constraints for complete sensor blocks submitted by the application."""
import importlib.util
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("vibration_workflow", ROOT / "tools/vibration.py")
workflow = importlib.util.module_from_spec(spec)
spec.loader.exec_module(workflow)


class SensorSettingsTests(unittest.TestCase):
    def setUp(self):
        self.settings = {"samples": 16384, "sample_rate_hz": 163840, "frequency_hz": 640,
                         "amplitude": .4, "noise": .01, "seed": 42, "ramp_per_second": 0.}

    def test_complete_configuration_and_nyquist_boundary(self):
        self.assertEqual(workflow.sensor_settings(self.settings), self.settings)
        self.settings["frequency_hz"] = self.settings["sample_rate_hz"] / 2
        workflow.sensor_settings(self.settings)

    def test_single_sensor_export_and_bare_block_keep_business_validation(self):
        path = 'machines/1/sensor'
        self.assertEqual(workflow.sensor_settings_document(self.settings, 1), self.settings)
        self.assertEqual(workflow.sensor_settings_document({path: self.settings}, 1), self.settings)
        for document in ({'machines/2/sensor': self.settings},
                         {path: self.settings, 'machines/2/sensor': self.settings},
                         {path: dict(self.settings, amplitude=-1)}, {path: {}}):
            with self.subTest(document=document), self.assertRaises(ValueError):
                workflow.sensor_settings_document(document, 1)

    def test_rejects_invalid_values_and_partial_blocks(self):
        cases = {"samples": (0, 131073, 3.5, True), "seed": (0, 2**32, 1.5),
                 "sample_rate_hz": (0, 1e10, float("inf")),
                 "frequency_hz": (-1, 81921), "amplitude": (-1, 1e7),
                 "noise": (-1, float("nan")), "ramp_per_second": (-1, 1e7)}
        for key, invalid in cases.items():
            for value in invalid:
                with self.subTest(key=key, value=value), self.assertRaises(ValueError):
                    workflow.sensor_settings(dict(self.settings, **{key: value}))
        for settings in ({}, {**self.settings, "machine_id": 2}, None):
            with self.subTest(settings=settings), self.assertRaises(ValueError):
                workflow.sensor_settings(settings)
        with self.assertRaises(ValueError):
            workflow.sensor_settings(dict(self.settings, sample_rate_hz=1000))
