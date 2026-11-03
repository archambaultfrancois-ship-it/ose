"""Bounded, volatile scalar measurements: one current fl64 per declared name."""
import math
import struct
from ose.execution.ipc import unpack_port
from ose.recording.component import NAME, MAX_POINTS


def validate_name(name):
    if not isinstance(name, str) or len(name.encode()) > 200 or not NAME.fullmatch(name):
        raise ValueError('measurement name must contain dot-separated ASCII identifiers (max 200 bytes)')
    return name


def validate_value(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError('measurement requires a finite fl64')
    try:
        value = float(value)
    except OverflowError as error:
        raise ValueError('measurement requires a finite fl64') from error
    if not math.isfinite(value):
        raise ValueError('measurement requires a finite fl64')
    return value


def validate_snapshot(values):
    if not isinstance(values, dict) or len(values) > MAX_POINTS:
        raise ValueError('invalid measurement registry (max 128 names)')
    return {validate_name(name): None if value is None else validate_value(value)
            for name, value in values.items()}


class Measurements:
    def __init__(self, on_change=None):
        self.values = {}
        self.on_change = on_change

    def declare(self, body):
        name, payload = unpack_port(body)
        validate_name(name)
        if payload or name in self.values or len(self.values) >= MAX_POINTS:
            raise ValueError('measurement declarations require unique names (max 128) and no payload')
        self.values[name] = None
        if self.on_change:
            self.on_change()

    def update(self, body):
        name, payload = unpack_port(body)
        if name not in self.values or len(payload) != 8:
            raise ValueError('measurement requires a declared name and an fl64 payload')
        self.values[name] = validate_value(struct.unpack('>d', payload)[0])
        if self.on_change:
            self.on_change()

    def snapshot(self):
        return dict(self.values)
