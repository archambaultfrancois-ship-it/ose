import unittest
from ose.subsystems.assertions import evaluate, validate


class SubsystemAssertionTests(unittest.TestCase):
    def check(self, assertion, records):
        return evaluate(dict(version=1, assertions=[dict(output='out', **assertion)]),
                        records, ['out'], 1)[0]['passed']

    def test_silence_windows_and_boundaries(self):
        records = [dict(output='out', time_ns=200000000, value={'value': 90})]
        self.assertTrue(self.check({'until': .2, 'count': 0}, records))
        self.assertFalse(self.check({'until': .3, 'count': 0}, records))
        self.assertTrue(self.check({'from': .2, 'count': 1}, records))

    def test_order_partial_values_and_numeric_tolerance(self):
        records = [dict(output='out', time_ns=i, value={'value': v, 'id': i})
                   for i, v in enumerate([1.001, 2])]
        self.assertTrue(self.check({'sequence': [{'value': 1}, {'value': 2}], 'tolerance': .01}, records))
        self.assertFalse(self.check({'sequence': [{'value': 2}, {'value': 1}]}, records))
        self.assertTrue(self.check({'value': {'id': 1}, 'count': 1}, records))
        self.assertFalse(self.check({'value': {'value': 99}}, records))

    def test_invalid_or_vacuous_assertions_fail_preflight(self):
        for assertion in ({}, {'count': True}, {'from': .8, 'until': .2, 'count': 0},
                          {'count': 1, 'min_count': 0}, {'sequence': {}, 'count': 1},
                          {'tolerance': float('nan'), 'count': 1},
                          {'min_count': 2, 'max_count': 1}):
            with self.subTest(assertion=assertion), self.assertRaises(ValueError):
                validate(dict(version=1, assertions=[dict(output='out', **assertion)]), ['out'], 1)
        with self.assertRaisesRegex(ValueError, 'nonempty'):
            validate(dict(version=1, assertions=[]), ['out'], 1)
