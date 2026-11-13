"""Optional, model-independent SDL recording services."""

DEFAULT_LABEL = "recorder"


def unpack_record(record, body):
    """Separate an optional source catalogue from the recorded SDL envelope."""
    record = dict(record)
    if 'catalogue_size' in record:
        size = record.pop('catalogue_size')
        if type(size) is not int or not 0 < size < len(body):
            raise ValueError('invalid recording catalogue size')
        record['catalogue'] = body[:size].decode('utf-8')
        body = body[size:]
    return record, body


def session_label(value):
    if not isinstance(value, str) or not value.strip() or len(value.encode("utf-8")) > 256:
        raise ValueError("session label must be nonempty text of at most 256 UTF-8 bytes")
    return value.strip()
