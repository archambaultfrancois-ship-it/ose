"""Application IDs are correlation keys, never transport deduplication keys."""

MAX_APP_ID = (1 << 64) - 1


def validate_app_id(value):
    """Accept exact uint64 values, including zero, without coercing bool or float."""
    if type(value) is not int or not 0 <= value <= MAX_APP_ID:
        raise ValueError("application message ID must be a uint64 integer")
    return value
