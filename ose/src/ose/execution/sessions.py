"""External sessions and contracts, independent of graph component readiness."""
import asyncio
import uuid
from dataclasses import dataclass, field
from ose.config import EXTERNAL_LIMITS
from ose.delivery.engine import DeliveryEngine
from ose.delivery.policies import SubscriptionPolicy
from ose.delivery.stores import MemoryStore


class ClientError(ValueError):
    def __init__(self, code, message):
        self.code = code
        super().__init__(message)


@dataclass
class Session:
    identity: str
    client_id: str
    peer: object
    endpoint: tuple
    subscriptions: set = field(default_factory=set)
    publishers: set = field(default_factory=set)


class Sessions:
    def init_sessions(self):
        self.sessions, self.publisher_owners, self.external_tasks = {}, {}, {}
        from ose.execution.publications import Producers
        self.producers = Producers(self.plan, self.codec)
        self.catalogue_baseline = len(self.catalogues) + len(self.plan.get("external_topics", {}))
        self.limits = {**EXTERNAL_LIMITS, **self.plan.get("broker", {}).get("external", {})}
        self.external_contracts = dict(self.plan.get("external_topics", {}))
        for topic, port in self.external_contracts.items():
            self.register(port["catalogue"])
            self.topics[topic] = dict(port)
        self.internal_topics = set(self.topics) - set(self.external_contracts)

    def open_session(self, peer, header):
        if not self.limits["enabled"]:
            raise ClientError("disabled", "external clients are disabled")
        client_id, udp_port = header.get("client_id"), header.get("udp_port")
        if not isinstance(client_id, str) or not 0 < len(client_id) <= 128:
            raise ClientError("invalid_client", "client_id must contain 1..128 characters")
        if any(s.client_id == client_id for s in self.sessions.values()):
            raise ClientError("duplicate_client", "client_id already connected")
        if len(self.sessions) >= self.limits["max_clients"]:
            raise ClientError("capacity", "too many external clients")
        if type(udp_port) is not int or not 0 < udp_port < 65536:
            raise ClientError("invalid_endpoint", "invalid UDP endpoint")
        address = peer.writer.get_extra_info("peername")[0]
        session = Session(uuid.uuid4().hex, client_id, peer, (address, udp_port))
        self.sessions[session.identity] = session
        return session

    def topic_list(self):
        return [{"topic": topic, "root": port["root"],
                 "catalogue_id": self.codec.identity(port["catalogue"]),
                 "producer_count": len(self.publisher_owners.get(topic, ())) + int(
                     topic in self.internal_topics and topic.split('.')[0] in self.peers
                     and self.components.observed[topic.split('.')[0]] is not False),
                 "publisher_connected": topic in self.publisher_owners or
                 (topic in self.internal_topics and topic.split(".")[0] in self.peers)}
                for topic, port in sorted(self.topics.items())]

    def declare_publisher(self, session, header, body):
        topic, root = header.get("topic"), header.get("root")
        if not isinstance(topic, str) or not 0 < len(topic) <= 200 or not isinstance(root, str):
            raise ClientError("invalid_topic", "invalid topic or root")
        if topic in session.publishers:
            raise ClientError("ownership", "publisher already declared; release it first")
        if topic not in self.topics and len(self.topics) >= self.limits["max_topics"]:
            raise ClientError("capacity", "too many topics")
        text = body.decode("utf-8")
        if root not in self.codec.api.message_names(self.codec.api.prepare(text)):
            raise ClientError("schema", "unknown root")
        consumers = [sub["consumer"] for sub in self.subscriptions_by_topic.get(topic, {}).values()
                     if "consumer" in sub]
        for consumer in consumers:
            if consumer["root"] != root:
                raise ClientError("schema", "topic root differs from its contract")
            self.codec.compare(text, consumer["catalogue"], root)
        try:
            descriptor = self.producers.declare(session.identity, topic, root, text)
        except ValueError as error:
            raise ClientError("schema", str(error)) from error
        try:
            identity = self.register(text)
        except Exception:
            self.producers.release(session.identity, topic)
            if topic not in self.topics:
                self.producers.topics.pop(topic, None)
            raise
        self.topics.setdefault(topic, descriptor)
        self.publisher_owners.setdefault(topic, set()).add(session.identity)
        session.publishers.add(topic)
        return {"topic": topic, "catalogue_id": identity}

    def add_subscription(self, session, header, body):
        topic = header.get("topic")
        if topic not in self.topics:
            raise ClientError("unknown_topic", "unknown topic")
        if sum(len(s.subscriptions) for s in self.sessions.values()) >= self.limits["max_subscriptions"]:
            raise ClientError("capacity", "too many external subscriptions")
        try:
            policy = SubscriptionPolicy.from_dict(header["policy"])
        except (ValueError, TypeError) as error:
            raise ClientError("qos", str(error)) from error
        if policy.capacity > self.limits["max_capacity"] or policy.wait_timeout_ms > self.limits["max_wait_timeout_ms"]:
            raise ClientError("qos", "subscription exceeds broker limits")
        text, root = body.decode("utf-8"), header.get("root")
        port = self.topics[topic]
        if root != port["root"]:
            raise ClientError("schema", "unexpected root")
        for producer in [port, *self.producers.contracts(topic)]:
            self.codec.compare(producer["catalogue"], text, root)
        self.register(text)
        identity = "ext-" + uuid.uuid4().hex
        sub = dict(id=identity, **{"from": topic, "to": "@" + session.identity + ".input"},
                   policy=policy.to_dict(), session=session.identity, active=False,
                   consumer=dict(root=root, catalogue=text))
        engine = DeliveryEngine(policy, MemoryStore(policy.capacity))
        self.subscriptions[identity], self.engines[identity] = sub, engine
        session.subscriptions.add(identity)
        if policy.buffer == "latest" and topic in self.retained:
            record = self.retained[topic]
            if not self._fits(identity, record, record["body"]):
                session.subscriptions.remove(identity)
                del self.subscriptions[identity], self.engines[identity]
                raise ClientError("oversize", "retained state exceeds transport limit")
            engine.enqueue(record)
        # Publish the route only after retained-state validation succeeds.
        self.subscriptions_by_topic.setdefault(topic, {})[identity] = sub
        self.external_tasks[identity] = asyncio.create_task(self._send_loop(identity, engine))
        return {"subscription": identity, "policy": policy.to_dict()}

    async def remove_subscription(self, session, identity):
        if identity not in session.subscriptions:
            raise ClientError("ownership", "subscription does not belong to session")
        session.subscriptions.remove(identity)
        # Explicit state also terminates a loop if wait_for absorbs a cancellation
        # racing an already completed wakeup on Python 3.10/3.11.
        self.subscriptions[identity]["closing"] = True
        self.engines[identity].store.notify()
        task = self.external_tasks.pop(identity)
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        engine = self.engines.pop(identity)
        engine.store.notify()
        engine.store.close()
        sub = self.subscriptions.pop(identity)
        routes = self.subscriptions_by_topic[sub["from"]]
        routes.pop(identity)
        if not routes:
            self.subscriptions_by_topic.pop(sub["from"])
        peer = self.data_peers.pop(identity, None)
        if peer:
            await peer.close()

    async def close_session(self, session):
        for identity in list(session.subscriptions):
            await self.remove_subscription(session, identity)
        for topic in list(session.publishers):
            self.release_publisher(session, topic)
        self.sessions.pop(session.identity, None)

    def release_publisher(self, session, topic):
        owners = self.publisher_owners.get(topic, set())
        owners.discard(session.identity)
        if not owners:
            self.publisher_owners.pop(topic, None)
        self.producers.release(session.identity, topic)
        session.publishers.discard(topic)
