"""Observer settings control optional labels, machine filters and nested output format."""
from contextlib import redirect_stderr
import importlib.util
import io
import json
from pathlib import Path
from types import SimpleNamespace
import unittest


ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("vibration_observer", ROOT / "components/observer/handler.py")
observer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(observer)


class ObserverSettingsTests(unittest.TestCase):
    def observe(self, label, machine_ids, *, include_time_ns=True, include_sequence=True, output_present=True):
        output_format = SimpleNamespace(include_time_ns=include_time_ns, include_sequence=include_sequence)
        settings = SimpleNamespace(label=label, machine_ids=machine_ids,
                                   output=SimpleNamespace(format=output_format) if output_present else None)
        ctx = SimpleNamespace(parameters_display=lambda: settings, logf=lambda *args, **kwargs: None)
        output = io.StringIO()
        with redirect_stderr(output):
            for machine in (1, 2):
                observer.on_input(ctx, SimpleNamespace(machine_id=machine, sequence=1,
                    time_ns=100, rms=.5, peak=.7, state=0))
        return [json.loads(line) for line in output.getvalue().splitlines()]

    def test_default_settings_preserve_existing_observations(self):
        rows = self.observe(None, [1, 2])
        self.assertEqual([row["machine_id"] for row in rows], [1, 2])
        self.assertTrue(all("label" not in row for row in rows))
        self.assertTrue(all(row["event"] == "vibration" and row["state"] == 0 for row in rows))
        self.assertTrue(all(row["time_ns"] == 100 and row["sequence"] == 1 for row in rows))

    def test_optional_text_and_empty_text_are_distinct_from_absence(self):
        for label in ("Workshop A", ""):
            with self.subTest(label=label):
                self.assertTrue(all(row["label"] == label for row in self.observe(label, [1, 2])))

    def test_absent_output_uses_default_format_and_preserves_other_settings(self):
        self.assertEqual(self.observe(None, [1, 2], output_present=False), self.observe(None, [1, 2]))
        rows = self.observe("Workshop A", [2], output_present=False)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["machine_id"], 2)
        self.assertEqual(rows[0]["label"], "Workshop A")
        self.assertEqual(rows[0]["sequence"], 1)
        self.assertEqual(rows[0]["time_ns"], 100)

    def test_variable_machine_list_filters_and_empty_list_shows_all(self):
        self.assertEqual([row["machine_id"] for row in self.observe(None, [2])], [2])
        self.assertEqual([row["machine_id"] for row in self.observe(None, [])], [1, 2])
        self.assertEqual(self.observe(None, [42]), [])

    def test_third_level_format_controls_fields_independently(self):
        for include_time_ns in (False, True):
            for include_sequence in (False, True):
                with self.subTest(include_time_ns=include_time_ns, include_sequence=include_sequence):
                    rows = self.observe(None, [], include_time_ns=include_time_ns,
                                        include_sequence=include_sequence)
                    self.assertEqual([row["machine_id"] for row in rows], [1, 2])
                    for row in rows:
                        self.assertEqual("time_ns" in row, include_time_ns)
                        self.assertEqual("sequence" in row, include_sequence)
                        self.assertEqual((row["rms"], row["peak"], row["state"]), (.5, .7, 0))
