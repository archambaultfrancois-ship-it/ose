"""Capture windows follow replay position while retaining source-time decisions."""
import unittest
from ose.recording.batch import CaptureWindow


class CaptureWindowTests(unittest.TestCase):
    def accepts(self, window, at):
        return window.accepts({'publish_time_ns': at})

    def test_validation_and_optional_bounds(self):
        self.assertTrue(CaptureWindow(['*']).contains(0))
        self.assertTrue(CaptureWindow(['*'], '1s').contains(10**9))
        self.assertFalse(CaptureWindow(['*'], end='1s').contains(10**9))
        self.assertEqual(CaptureWindow(['*'], '.1', '250ms').start, 100_000_000)
        for clauses, start, end in ((None, '1s', None), (None, None, '1s'),
                                    (['*'], '1s', '1s'), (['*'], '2s', '1s'),
                                    (['*'], '-1s', None), (['*'], None, 'nan'),
                                    (['*'], None, '0')):
            with self.subTest(start=start, end=end), self.assertRaises(ValueError):
                CaptureWindow(clauses, start, end)

    def test_source_boundaries_pause_and_speed_changes_preserve_past_decisions(self):
        window = CaptureWindow(['*'], '10ns', '20ns')
        window.update(0, 1, now=100)
        self.assertFalse(self.accepts(window, 109))
        self.assertTrue(self.accepts(window, 110))
        self.assertFalse(self.accepts(window, 120))
        window.update(15, 0, now=115)  # Pause in the window.
        self.assertTrue(self.accepts(window, 124))
        window.update(15, 2, now=125)  # Resume at speed 2.
        self.assertTrue(self.accepts(window, 127))
        self.assertFalse(self.accepts(window, 128))
        # Late delivery is judged at publication, not at recorder receipt.
        self.assertTrue(self.accepts(window, 112))
        self.assertFalse(self.accepts(window, 109))

    def test_steps_jump_in_position_without_advancing_wall_time_and_history_is_bounded(self):
        window = CaptureWindow(['*'], '10ns', '20ns')
        window.update(0, 0, now=100)
        window.update(10, 0, now=101)
        window.update(20, 0, now=102)
        self.assertFalse(self.accepts(window, 100))
        self.assertTrue(self.accepts(window, 101))
        self.assertFalse(self.accepts(window, 102))
        for at in range(103, 1000):
            window.update(at, 0, now=at)
        self.assertEqual(len(window.times), 3)
