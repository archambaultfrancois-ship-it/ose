"""Strict versioned document loading and graph descriptions."""
import json
from pathlib import Path


def unique_pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate key: {key}")
        result[key] = value
    return result


def load(path, *, version=1):
    versions = (version,) if type(version) is int else version
    if not isinstance(versions, tuple) or not versions or any(type(v) is not int or v < 1 for v in versions):
        raise ValueError("expected document version must be a positive integer")
    path = Path(path).resolve()
    if path.stat().st_size > 1024 * 1024:
        raise ValueError("configuration exceeds 1 MiB")
    value = json.loads(path.read_text(), object_pairs_hook=unique_pairs)
    if not isinstance(value, dict) or type(value.get("version")) is not int or value.get("version") not in versions:
        raise ValueError(f"expected a version {' or '.join(map(str, versions))} document")
    return value


def fields(value, allowed, required=()):
    if not isinstance(value, dict) or set(value) - set(allowed) or set(required) - set(value):
        raise ValueError(f"invalid fields; expected {allowed}, required {required}")
