"""Business policy, independent of SDL and OSE."""
import math


def classify(rms, threshold):
    if not all(math.isfinite(value) for value in (rms, threshold)) or rms < 0 or threshold <= 0:
        raise ValueError("RMS must be nonnegative and threshold finite and positive")
    return int(rms >= threshold)
