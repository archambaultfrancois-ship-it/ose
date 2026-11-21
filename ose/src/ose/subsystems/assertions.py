"""Explicit bounded-window assertions over collected SDL JSON values."""
import math
from ose.modeling.model import fields
from ose.timing import seconds_ns


def matches(actual, expected, tolerance=0):
    if isinstance(expected, dict):
        return isinstance(actual, dict) and all(
            key in actual and matches(actual[key], value, tolerance) for key, value in expected.items())
    if isinstance(expected, list):
        return isinstance(actual, list) and len(actual) == len(expected) and all(
            matches(a, b, tolerance) for a, b in zip(actual, expected))
    if type(expected) in (int, float) and type(actual) in (int, float):
        return actual == expected or (tolerance > 0 and abs(actual - expected) <= tolerance)
    return type(actual) is type(expected) and actual == expected


def validate(document, outputs, duration):
    fields(document, ('version', 'assertions'), ('version', 'assertions'))
    if document['version'] != 1 or type(document['version']) is not int:
        raise ValueError('expected version 1 expectations')
    if not isinstance(document['assertions'], list) or not document['assertions']:
        raise ValueError('expectations require a nonempty assertions list')
    horizon = seconds_ns(duration, positive=True)
    for assertion in document['assertions']:
        fields(assertion, ('output', 'from', 'until', 'count', 'min_count', 'max_count',
                           'value', 'sequence', 'tolerance'), ('output',))
        if assertion['output'] not in outputs:
            raise ValueError(f'assertion output is not collected: {assertion["output"]}')
        start = seconds_ns(assertion.get('from', 0))
        stop = seconds_ns(assertion.get('until', duration))
        if not 0 <= start < stop <= horizon:
            raise ValueError('assertion window must satisfy 0 <= from < until <= duration')
        for key in ('count', 'min_count', 'max_count'):
            if key in assertion and (type(assertion[key]) is not int or assertion[key] < 0):
                raise ValueError(f'{key} must be a nonnegative integer')
        if 'count' in assertion and ('min_count' in assertion or 'max_count' in assertion):
            raise ValueError('count cannot be combined with min_count/max_count')
        if assertion.get('min_count', 0) > assertion.get('max_count', math.inf):
            raise ValueError('min_count exceeds max_count')
        tolerance = assertion.get('tolerance', 0)
        if type(tolerance) not in (int, float) or not math.isfinite(tolerance) or tolerance < 0:
            raise ValueError('tolerance must be finite and nonnegative')
        if 'sequence' in assertion and (not isinstance(assertion['sequence'], list) or
                                        'value' in assertion):
            raise ValueError('sequence must be an array and cannot be combined with value')
        if not any(key in assertion for key in ('count', 'min_count', 'max_count', 'value', 'sequence')):
            raise ValueError('assertion requires a count, value or sequence expectation')


def evaluate(document, records, outputs, duration):
    validate(document, outputs, duration)
    results = []
    for index, assertion in enumerate(document['assertions']):
        start, stop = seconds_ns(assertion.get('from', 0)), seconds_ns(assertion.get('until', duration))
        observed = [r for r in records if r['output'] == assertion['output'] and start <= r['time_ns'] < stop]
        tolerance = assertion.get('tolerance', 0)
        selected = [r for r in observed if 'value' not in assertion or matches(r['value'], assertion['value'], tolerance)]
        count = len(selected)
        lower = assertion.get('count', assertion.get('min_count', 1 if 'value' in assertion else 0))
        upper = assertion.get('count', assertion.get('max_count', math.inf))
        passed = lower <= count <= upper
        if 'sequence' in assertion:
            passed = passed and len(observed) == len(assertion['sequence']) and all(
                matches(record['value'], expected, tolerance)
                for record, expected in zip(observed, assertion['sequence']))
        results.append(dict(index=index, passed=passed, observed=len(observed), matched=count,
                            expectation=assertion))
    return results
