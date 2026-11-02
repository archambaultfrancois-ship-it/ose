"""Resolve the current endpoint on every connection attempt."""
from pathlib import Path
from ose.transport.secure import connect, contexts, loopback
from ose_ws.util import APIError, loads


class ApplicationConnection:
    def __init__(self, config):
        self.config = config["application"]
        self.context = None
        if self.config["distributed"] and not self.config["insecure"]:
            self.context = contexts(self.config["tls_ca"], self.config["tls_cert"], self.config["tls_key"])[1]

    def address(self):
        app = self.config
        endpoint = loads(Path(app["endpoint"]).read_bytes()) if app["endpoint"] else app
        host, port = endpoint.get("host"), endpoint.get("port")
        if not isinstance(host, str) or not host or type(port) is not int or not 1 <= port <= 65535:
            raise ValueError("OSE endpoint requires a host and a port between 1 and 65535.")
        if self.context is None and not loopback(host):
            raise ValueError("OSE connections without TLS require a loopback endpoint.")
        return host, port

    async def connect(self):
        host, port = self.address()
        return await connect(host, port, 1024 * 1024, self.context,
                             "coordinator" if self.config["distributed"] else None, timeout=3)

    def public(self):
        return {"name": self.config["name"], "mode": "distributed" if self.config["distributed"] else "local"}
