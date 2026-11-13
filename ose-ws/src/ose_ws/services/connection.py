"""Resolve the current endpoint on every connection attempt."""
from ose.transport.secure import connect, contexts, loopback
from ose.transport.endpoint import resolve


class ApplicationConnection:
    def __init__(self, config):
        self.config = config["application"]
        self.distributed = self.config["distributed"]
        self.context = None
        if self.config["distributed"] and not self.config["insecure"]:
            self.context = contexts(self.config["tls_ca"], self.config["tls_cert"], self.config["tls_key"])[1]

    def address(self):
        endpoint = resolve(self.config)
        host, port = endpoint.host, endpoint.port
        self.context, self.distributed = endpoint.context, endpoint.distributed
        if self.context is None and not loopback(host):
            raise ValueError("OSE connections without TLS require a loopback endpoint.")
        return host, port

    async def connect(self):
        host, port = self.address()
        return await connect(host, port, 1024 * 1024, self.context,
                             "coordinator" if self.distributed else None, timeout=3)

    def public(self):
        return {"name": self.config["name"], "mode": "distributed" if self.distributed else "local"}
