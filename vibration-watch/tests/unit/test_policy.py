import math
from pathlib import Path
import sys
import unittest
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'components/alarm'))
from policy import classify


class PolicyTests(unittest.TestCase):
    def test_threshold_and_recovery(self):
        self.assertEqual([classify(value, 1) for value in (0, .99, 1, 1.1, .5)], [0, 0, 1, 1, 0])

    def test_invalid_observations(self):
        for value in (-1, math.nan, math.inf):
            with self.assertRaises(ValueError):
                classify(value, 1)
        for threshold in (0, -1, math.nan, math.inf):
            with self.assertRaises(ValueError):
                classify(1, threshold)
