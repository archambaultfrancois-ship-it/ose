"""Active application endpoint shared by launchers and operational clients."""
from dataclasses import dataclass
import json
from pathlib import Path
import os
import tempfile

from ose.transport.secure import contexts


def publish(build_dir, port, *, distributed=False, insecure=False):
    endpoint = dict(host='127.0.0.1', port=port, distributed=distributed, insecure=insecure)
    path = Path(build_dir) / 'endpoint.json'
    with tempfile.NamedTemporaryFile(mode='w', dir=path.parent, delete=False) as stream:
        temporary = Path(stream.name)
        try:
            json.dump(endpoint, stream)
            stream.close()
            os.replace(temporary, path)
        finally:
            temporary.unlink(missing_ok=True)
    return endpoint


@dataclass(frozen=True)
class Endpoint:
    host: str
    port: int
    distributed: bool
    context: object


def resolve(options):
    """Explicit security options override hints; credentials never come from the file.

    False flags leave discovery enabled, just like omitted CLI switches. Resolve
    again on reconnect so a launcher restart may change both port and mode.
    """
    get = options.get if isinstance(options, dict) else lambda key, default=None: getattr(options, key, default)
    host, port = get('host'), get('port')
    value = dict(host="127.0.0.1", port=7000)
    if host is None and port is None:
        path = get('endpoint')
        if path or Path('build/endpoint.json').is_file():
            value = json.loads(Path(path or 'build/endpoint.json').read_text())
            if not isinstance(value, dict):
                raise ValueError('endpoint must be a JSON object')
    host = host if host is not None else value.get('host')
    port = port if port is not None else value.get('port')
    if not isinstance(host, str) or not host or type(port) is not int or not 1 <= port <= 65535:
        raise ValueError('endpoint requires a host and a port between 1 and 65535')
    for key in ('distributed', 'insecure'):
        if key in value and type(value[key]) is not bool:
            raise ValueError('endpoint ' + key + ' must be boolean')
    credentials = (get('tls_ca'), get('tls_cert'), get('tls_key'))
    explicit = get('distributed') or get('insecure') or any(credentials)
    distributed = bool(explicit or value.get('distributed', False))
    insecure = bool(get('insecure')) if explicit else value.get('insecure', False)
    if insecure and not distributed:
        raise ValueError('insecure requires distributed mode')
    context = contexts(*credentials, insecure=insecure, hosts=[host])[1] if distributed else None
    return Endpoint(host, port, distributed, context)
