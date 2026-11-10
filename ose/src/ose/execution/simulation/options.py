"""Launch-only simulation options; models and SDK protocols stay unchanged."""
DEFAULT_MAX_MICROSTEPS = 10_000


def microstep_limit(enabled=False, maximum=None):
    if type(enabled) is not bool:
        raise ValueError("microsteps must be boolean")
    if not enabled:
        if maximum is not None:
            raise ValueError("max_microsteps requires microsteps")
        return None
    maximum = DEFAULT_MAX_MICROSTEPS if maximum is None else maximum
    if type(maximum) is not int or maximum <= 0:
        raise ValueError("max_microsteps must be a positive integer")
    return maximum
