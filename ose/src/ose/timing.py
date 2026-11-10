"""Shared uint64 nanosecond conversion for execution models and clocks."""
from decimal import Decimal, ROUND_HALF_UP
import math

MAX_NS = (1 << 64) - 1


def seconds_ns(value, *, positive=False):
    try:
        valid = type(value) in (int, float) and math.isfinite(value) and value >= 0 and (not positive or value > 0)
    except OverflowError:
        valid = False
    if not valid:
        raise ValueError("simulation time must be a finite nonnegative number (positive for durations)")
    scaled = Decimal(str(value)) * 1_000_000_000
    if scaled > MAX_NS:
        raise ValueError("simulation time exceeds uint64 nanoseconds")
    result = int(scaled.to_integral_value(rounding=ROUND_HALF_UP))
    return max(1, result) if value > 0 else 0
