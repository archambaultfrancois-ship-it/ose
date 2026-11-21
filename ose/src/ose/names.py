"""Canonical runtime endpoints and private generated-code identifiers."""


def split_endpoint(value):
    """Split a qualified endpoint; retain support for legacy flat endpoints."""
    if ':' in value:
        return tuple(value.split(':', 1))
    return tuple(value.split('.', 1))


def endpoint_name(component, port):
    """Hierarchical components use ':'; existing flat topics stay compatible."""
    return component + (':' if '.' in component else '.') + port


def code_identifier(name):
    """Private generated-code symbol; never a runtime component identity."""
    if '.' not in name:
        return name
    import hashlib
    return 'oseg_' + hashlib.sha256(name.encode()).hexdigest()[:24]
