"""Bounded JSON, stable opaque resource identifiers and public API errors."""
import hashlib
import json
import math
import time
from datetime import datetime, timezone


class APIError(Exception):
    def __init__(self, status, code, message, **details):
        self.status, self.code, self.message, self.details = status, code, message, details
        super().__init__(message)


def utc():
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def timestamp(seconds):
    return datetime.fromtimestamp(seconds, timezone.utc).isoformat().replace("+00:00", "Z")


def opaque(kind, *parts):
    return kind + "-" + hashlib.sha256(dumps(parts).encode()).hexdigest()[:32]


def dumps(value):
    return json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(",", ":"), sort_keys=True)


def loads(raw, *, max_bytes=2 * 1024 * 1024):
    if len(raw) > max_bytes:
        raise APIError(413, "BODY_TOO_LARGE", "The request body exceeds the configured limit.")
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError("Duplicate JSON keys are not allowed.")
            result[key] = value
        return result
    def reject(value):
        raise ValueError("Non-finite JSON numbers are not allowed.")
    # Check nesting before parsing: json.loads itself must not recurse unboundedly.
    depth, quoted, escaped = 0, False, False
    for char in raw.decode("utf-8") if isinstance(raw, bytes) else raw:
        if quoted:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                quoted = False
        elif char == '"':
            quoted = True
        elif char in "[{":
            depth += 1
            if depth > 64:
                raise ValueError("JSON nesting exceeds 64 levels.")
        elif char in "]}":
            depth -= 1
    result = json.loads(raw, object_pairs_hook=pairs, parse_constant=reject)
    stack, count = [result], 0
    while stack:
        value = stack.pop()
        count += 1
        if count > 250000:
            raise ValueError("JSON contains too many elements.")
        if isinstance(value, float) and not math.isfinite(value):
            raise ValueError("Non-finite JSON numbers are not allowed.")
        if isinstance(value, dict):
            stack.extend(value.values())
        elif isinstance(value, list):
            stack.extend(value)
    return result


def require(condition, message, status=422, code="INVALID_VALUE"):
    if not condition:
        raise APIError(status, code, message)


def public_numbers(value):
    """Never round a counter before it reaches JavaScript."""
    if type(value) is int and abs(value) > 2**53 - 1:
        return str(value)
    if isinstance(value, dict):
        return {key: public_numbers(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [public_numbers(item) for item in value]
    return value


def page(items, cursor=None, limit=200):
    require(1 <= limit <= 200, "Page size must be between 1 and 200.")
    digest = opaque("inventory", items)
    offset = 0
    if cursor:
        try:
            version, position = cursor.rsplit(":", 1)
            require(version == digest, "Inventory changed; restart pagination.", 409, "CURSOR_EXPIRED")
            offset = int(position)
            require(offset >= 0, "Invalid cursor.", 400)
        except (ValueError, AttributeError):
            raise APIError(400, "INVALID_CURSOR", "Invalid pagination cursor.")
    return {"items": items[offset:offset + limit],
            "next": f"{digest}:{offset + limit}" if offset + limit < len(items) else None}
