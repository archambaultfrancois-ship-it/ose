"""Multi-topic broker with independent, bounded subscription delivery."""
import asyncio
from collections import Counter
import json
import time
import uuid
from ose.codec.sdl2 import SDL2
from ose.execution.sessions import Sessions, ClientError
from ose.delivery.engine import DeliveryEngine
from ose.delivery.policies import SubscriptionPolicy
from ose.delivery.stores import MemoryStore
from ose.delivery.messages import validate_app_id
from ose.transport.tcp import TCP, start_server
from ose.transport.udp import UDP
from ose.transport.protocol import encode
from ose.parameters.service import ParameterEndpoint, OPERATIONS as PARAMETER_OPERATIONS
from ose.recording.taps import TapHub, OPERATIONS as RECORDING_OPERATIONS
from ose.execution.observation import describe, ObservationDemand, subscription_schema
from ose.recording.component import validate_name, MAX_POINTS
from ose.recording.inputs import input_points, input_key
from ose.execution.logs import LogStore


class Broker(Sessions):
    def __init__(self, plan):
        self.plan, self.codec = plan, SDL2(plan["sdl_root"])
        self.parameters = ParameterEndpoint(plan, self.codec)
        self.catalogues, self.topics, self.engines = {}, {}, {}
        self.epoch, self.sequence = uuid.uuid4().hex, 0
        self.logs = LogStore(self.epoch)
        self.retained, self.peers, self.ready, self.metrics = {}, {}, set(), Counter()
        self.observation = describe(plan)
        self.demand = ObservationDemand()
        self.metric_times, self.generations = {}, {}
        self.presence_times = {}
        self.data_peers, self.dedicated_data = {}, set()
        self._enabled = False
        self.closing = False
        self.errors = []
        self.connections = set()
        self.started_at = time.time()
        self.topic_counts = Counter()
        for name, node in plan["components"].items():
            for direction in ("inputs", "outputs"):
                for port_name, port in node[direction].items():
                    if port.get("kind") == "timer":
                        continue
                    self.register(port["catalogue"])
                    if direction == "outputs":
                        self.topics[f"{name}.{port_name}"] = port
        self.subscriptions = {sub["id"]: sub for sub in plan["subscriptions"]}
        self.subscriptions_by_topic = {}
        for identity, sub in self.subscriptions.items():
            self.subscriptions_by_topic.setdefault(sub["from"], {})[identity] = sub
        self.init_sessions()
        self.recording = TapHub(self.recording_points, plan["max_frame"], codec=self.codec)
        self.internal_points = {}
        self.recording_sequence = 0
        for identity, sub in self.subscriptions.items():
            policy = SubscriptionPolicy.from_dict(sub["policy"])
            store = MemoryStore(policy.capacity)
            self.engines[identity] = DeliveryEngine(policy, store)

    @property
    def enabled(self):
        return self._enabled

    @enabled.setter
    def enabled(self, value):
        self._enabled = value
        for engine in self.engines.values():
            engine.store.notify()

    def recording_points(self):
        outputs = {topic: dict(topic=topic, root=port["root"], catalogue=port["catalogue"], kind="output",
                    generation=self.publisher_owners.get(topic, self.epoch),
                    available=self.enabled and (topic in self.publisher_owners or topic.split(".")[0] in self.peers), endpoint=None)
                for topic, port in self.topics.items()}
        outputs.update({topic: dict(desc, topic=topic, kind="internal", endpoint=None,
            generation=self.generations.get(desc["component"]),
            available=self.enabled and desc["component"] in self.ready)
            for topic, desc in self.internal_points.items()})
        for name in self.plan["components"]:
            outputs.update(input_points(self.plan, name, self.generations.get(name),
                self.enabled and name in self.ready))
        return outputs

    def recording_activation(self, node, active):
        prefix = f"{node}.record."
        return dict(active=sorted(topic[len(prefix):] for topic in active if topic.startswith(prefix)),
                    inputs=sorted(port for port in self.plan["components"][node]["inputs"]
                                  if input_key(self.plan, node, port) in active))

    def component_recording(self, node, op, header, body):
        if node is None:
            raise ValueError("recording component operations require an identified component")
        prefix = f"{node}.record."
        if op == "recording_component_state":
            return self.recording_activation(node, self.recording.active_points())
        if op == "recording_input":
            port = header["port"]
            desc = self.plan["components"][node]["inputs"][port]
            sequence = header["sequence"]
            extra = {"app_id": validate_app_id(header["app_id"])} if "app_id" in header else {}
            if node not in self.ready or type(sequence) is not int or sequence < 1:
                raise ValueError("input recording requires a ready component and positive tick index")
            key = input_key(self.plan, node, port)
            if key in self.recording.active_points():
                self.codec.validate_only(desc["catalogue"], desc["root"], body)
                self.recording.observe(key, body, generation=self.generations[node], sequence=sequence,
                                       catalogue=desc["catalogue"], root=desc["root"], **extra)
            return {}
        name = validate_name(header["name"])
        topic = prefix + name
        if op == "recording_declare":
            if node in self.ready or topic in self.topics or topic in self.internal_points or sum(
                    desc["component"] == node for desc in self.internal_points.values()) >= MAX_POINTS:
                raise ValueError("recording declarations require initialization and unique names")
            root, description, catalogue = header["root"], header.get("description", ""), body.decode()
            if not isinstance(description, str) or len(description.encode()) > 1024 or "\0" in description:
                raise ValueError("invalid recording description")
            prepared = self.codec.api.prepare(catalogue)
            if root not in self.codec.api.message_names(prepared):
                raise ValueError("unknown recording root")
            self.internal_points[topic] = dict(root=root, description=description, catalogue=catalogue,
                                              component=node, name=name)
            return {}
        if op != "recording_emit" or node not in self.ready:
            raise ValueError("invalid recording component operation")
        desc = self.internal_points[topic]
        if topic in self.recording.active_points():
            envelope = self.codec.wrap(desc["catalogue"], body)
            self.codec.validate_only(desc["catalogue"], desc["root"], envelope)
            self.recording_sequence += 1
            self.recording.observe(topic, envelope, generation=self.generations[node], sequence=self.recording_sequence,
                                   catalogue=desc["catalogue"], root=desc["root"])
        return {}

    def _notify_node(self, node):
        for identity, sub in self.subscriptions.items():
            if sub["to"].split(".")[0] == node:
                self.engines[identity].store.notify()

    def register(self, text):
        self.codec.api.prepare(text)
        identity = self.codec.identity(text)
        if identity in self.catalogues and self.catalogues[identity] != text:
            raise ValueError("CATALOGUE_COLLISION")
        if identity not in self.catalogues and hasattr(self, "limits") and len(self.catalogues) >= self.catalogue_baseline + self.limits["max_catalogues"]:
            raise ClientError("capacity", "too many catalogues")
        self.catalogues[identity] = text
        return identity

    async def start(self, port=0):
        self.readiness = asyncio.Event()
        host = self.plan.get("broker", {}).get("host", "127.0.0.1")
        self.udp = await UDP.bind(self._udp, self.plan["max_datagram"], host=host)
        self.server = await start_server(self._connection, host, port)
        self.port = self.server.sockets[0].getsockname()[1]
        self.send_tasks = [asyncio.create_task(self._send_loop(identity, engine))
                           for identity, engine in self.engines.items()]
        return self.port

    async def _connection(self, reader, writer):
        task = asyncio.current_task()
        self.connections.add(task)
        peer, node = TCP(reader, writer, self.plan["max_frame"]), None
        data_subscription = None
        session = None
        publications = set()
        parameter_polls = set()
        recording_watch = set()
        status_reads = set()
        try:
            while not self.closing:
                header, body = await peer.receive()
                op = header["op"]
                if op in PARAMETER_OPERATIONS:
                    try:
                        if session is not None or data_subscription is not None:
                            raise ValueError("parameter operations require a separate control session")
                        if op == 'parameters_next':
                            if parameter_polls:
                                raise ValueError('one active parameter poll per connection required')
                            poll = asyncio.create_task(self._parameter_reply(peer, header, body, node))
                            parameter_polls.add(poll)
                            def completed(poll):
                                parameter_polls.discard(poll)
                                if not poll.cancelled():
                                    poll.exception()
                            poll.add_done_callback(completed)
                            continue
                        result, binary = await self.parameters.request(peer, header, body,
                            node=self.plan["components"][node] if node is not None else None)
                        await self._reply(peer, header["request"], result, binary=binary)
                    except (ValueError, KeyError, TypeError) as error:
                        await self._reply(peer, header.get("request"), error=str(error), error_code="parameters")
                    continue
                if peer in self.parameters.external:
                    await self._reply(peer, header.get("request"), error="parameter-only client session")
                    continue
                if op in RECORDING_OPERATIONS:
                    try:
                        if not self.limits["enabled"]:
                            raise ValueError("external recording is disabled")
                        result, binary = await self.recording.request(peer, header)
                        await self._reply(peer, header["request"], result, binary=binary)
                    except (ValueError, KeyError, TypeError) as error:
                        await self._reply(peer, header.get("request"), error=str(error))
                    continue
                if op == "ack":
                    self._ack(node, header, session)
                    continue
                if op == "publish":
                    # Admission may wait for seconds; keep reading ACKs and control RPCs.
                    if len(publications) >= 32:
                        await self._reply(peer, header.get("request"), error="too many pending publications")
                    else:
                        publication = asyncio.create_task(self._publish_reply(peer, node, header, body, session))
                        publications.add(publication)
                        publication.add_done_callback(publications.discard)
                    continue
                if op == 'status':
                    if len(status_reads) >= 32:
                        await self._reply(peer, header.get('request'), error='too many pending status reads')
                    else:
                        status = asyncio.create_task(self._status_reply(peer, header))
                        status_reads.add(status)
                        def completed_status(status):
                            status_reads.discard(status)
                            if not status.cancelled():
                                status.exception()
                        status.add_done_callback(completed_status)
                    continue
                if op == 'recording_component_state' and header.get('watch'):
                    if node is None or recording_watch:
                        await self._reply(peer, header.get('request'), error='one recording watch per component connection required')
                    else:
                        watch = asyncio.create_task(self._recording_reply(peer, node, header))
                        recording_watch.add(watch)
                        def completed_watch(watch):
                            recording_watch.discard(watch)
                            if not watch.cancelled():
                                watch.exception()
                        watch.add_done_callback(completed_watch)
                    continue
                try:
                    result = {}
                    response_body = b""
                    if op == 'logs':
                        result = self.logs.query(**header.get('filters', {}))
                        await self._reply(peer, header['request'], result)
                        continue
                    if op == 'subscription_schema':
                        result, response_body = subscription_schema(self.plan, self.subscriptions[header['subscription']], header.get('side', 'source'))
                        await self._reply(peer, header['request'], {**result, 'epoch': self.epoch}, binary=response_body)
                        continue
                    attach_subscription = None
                    if op == "client_hello":
                        if node is not None or session is not None or data_subscription is not None:
                            raise ClientError("session", "connection already identified")
                        session = self.open_session(peer, header)
                        result = dict(session=session.identity,
                                      udp_port=self.udp.address[1], max_frame=self.plan["max_frame"],
                                      max_datagram=self.plan["max_datagram"],
                                      max_wait_timeout_ms=max(self.limits["max_wait_timeout_ms"],
                                          max((e.policy.wait_timeout_ms for e in self.engines.values()
                                               if e.policy.overflow == "wait"), default=0)))
                    elif op == "attach_client":
                        owner = self.sessions.get(header.get("session"))
                        identity = header["subscription"]
                        if node is not None or session is not None or data_subscription is not None or owner is None:
                            raise ClientError("session", "invalid data channel session")
                        if identity not in owner.subscriptions or identity in self.data_peers or self.engines[identity].policy.transport != "tcp":
                            raise ClientError("ownership", "invalid subscription data channel")
                        if owner.endpoint[0] != peer.writer.get_extra_info("peername")[0]:
                            raise ClientError("ownership", "data channel address mismatch")
                        attach_subscription = identity
                    elif op in ("list_topics", "declare_publisher", "release_publisher", "subscribe", "unsubscribe", "subscription_stats", "activate_subscription"):
                        if session is None:
                            raise ClientError("session", "client_hello required")
                        if op == "list_topics":
                            result = {"topics": self.topic_list()}
                        elif op == "declare_publisher":
                            result = self.declare_publisher(session, header, body)
                        elif op == "release_publisher":
                            topic = header["topic"]
                            if topic not in session.publishers:
                                raise ClientError("ownership", "publisher not owned by session")
                            session.publishers.remove(topic)
                            self.publisher_owners.pop(topic, None)
                        elif op == "subscribe":
                            result = self.add_subscription(session, header, body)
                        else:
                            identity = header["subscription"]
                            if identity not in session.subscriptions:
                                raise ClientError("ownership", "subscription not owned by session")
                            if op == "activate_subscription":
                                self.subscriptions[identity]["active"] = True
                                self.engines[identity].store.notify()
                            elif op == "unsubscribe":
                                await self.remove_subscription(session, identity)
                            else:
                                result = self.engines[identity].snapshot()
                    elif op == "hello":
                        if node is not None or session is not None or data_subscription is not None:
                            raise ValueError("connection already identified")
                        node = header["node"]
                        if node not in self.plan["components"] or node in self.peers:
                            raise ValueError("unknown or duplicate component")
                        udp_port = header.get("udp_port")
                        if type(udp_port) is not int or not 0 < udp_port < 65536:
                            raise ValueError("invalid UDP endpoint")
                        self.peers[node] = (peer, ("127.0.0.1", udp_port))
                        self.generations[node] = uuid.uuid4().hex
                        self.metrics.pop(node, None)
                        self.metric_times.pop(node, None)
                        self.presence_times.pop(node, None)
                        if header.get("data_channels", False):
                            self.dedicated_data.add(node)
                        self._notify_node(node)
                        result = {"udp_port": self.udp.address[1], "epoch": self.epoch}
                        if header.get('observe_metrics') is True:
                            result['reporting'] = self.demand.register(peer)
                        for identity, sub in self.subscriptions.items():
                            if sub["to"].split(".")[0] == node and sub["policy"]["buffer"] == "latest" and sub["from"] in self.retained:
                                retained = self.retained[sub["from"]]
                                if self._fits(identity, retained, retained["body"]):
                                    self.engines[identity].store.meta.clear()
                                    self.engines[identity].enqueue(retained)
                    elif op == "attach":
                        target = header["node"]
                        identity = header["subscription"]
                        sub = self.subscriptions[identity]
                        if node is not None or session is not None or data_subscription is not None or target not in self.peers or target not in self.dedicated_data:
                            raise ValueError("invalid data channel attachment")
                        if sub["to"].split(".")[0] != target or sub["policy"]["transport"] != "tcp" or identity in self.data_peers:
                            raise ValueError("invalid subscription data channel")
                        attach_subscription = identity
                    elif op == "ready":
                        if node is None:
                            raise ValueError("hello required")
                        self.ready.add(node)
                        self.readiness.set()
                        if peer in self.demand.members:
                            result['reporting'] = self.demand.state()
                    elif op in ("recording_declare", "recording_component_state", "recording_emit", "recording_input"):
                        result = self.component_recording(node, op, header, body)
                    elif op == "state":
                        result = {"enabled": self.enabled}
                    elif op == "catalogue":
                        response_body = self.catalogues[header["catalogue_id"]].encode()
                    elif op == "metrics":
                        if node is None:
                            raise ValueError("hello required")
                        metrics = json.loads(body) if body else header["metrics"]
                        if not isinstance(metrics, dict):
                            raise ValueError("metrics must be an object")
                        full = header.get('details', True) is not False and not set(metrics) <= {'log_records', 'logs_dropped'}
                        if 'measurements' in metrics:
                            from ose.execution.measurements import validate_snapshot
                            metrics['measurements'] = validate_snapshot(metrics['measurements'])
                        self.logs.append(node, self.generations[node], metrics.pop('log_records', []))
                        if not full:
                            metrics = {**self.metrics.get(node, {}), **metrics}
                        self.metrics[node] = metrics
                        self.presence_times[node] = time.monotonic()
                        if full:
                            self.metric_times[node] = self.presence_times[node]
                            self.demand.received(peer, header.get('report_version'))
                        if peer in self.demand.members:
                            result['reporting'] = self.demand.state()
                    else:
                        raise ValueError("unknown operation")
                    await self._reply(peer, header["request"], result, binary=response_body if op == "catalogue" else None)
                    # Send the handshake reply before permitting DATA on this channel.
                    if attach_subscription is not None:
                        data_subscription = attach_subscription
                        self.data_peers[data_subscription] = peer
                        self.engines[data_subscription].store.notify()
                except (ValueError, KeyError) as error:
                    await self._reply(peer, header.get("request"), error=str(error), error_code=getattr(error, "code", "schema" if op in ("declare_publisher", "subscribe", "catalogue") else "invalid_request"))
        except (asyncio.IncompleteReadError, ConnectionError, ValueError):
            pass
        finally:
            self.demand.forget(peer)
            for status in status_reads:
                status.cancel()
            await asyncio.gather(*status_reads, return_exceptions=True)
            for watch in recording_watch:
                watch.cancel()
            await asyncio.gather(*recording_watch, return_exceptions=True)
            for poll in parameter_polls:
                poll.cancel()
            await asyncio.gather(*parameter_polls, return_exceptions=True)
            self.parameters.disconnect(peer)
            self.recording.disconnected(peer)
            pending = list(publications)
            for publication in pending:
                publication.cancel()
            await asyncio.gather(*pending, return_exceptions=True)
            if session is not None:
                await self.close_session(session)
            if node is not None and self.peers.get(node, (None,))[0] is peer:
                topics = [topic for topic, desc in self.internal_points.items() if desc["component"] == node]
                self.recording.invalidate(topics + [input_key(self.plan, node, port)
                                                    for port in self.plan["components"][node]["inputs"]])
                for topic in topics:
                    del self.internal_points[topic]
                self.peers.pop(node, None)
                self.metrics.get(node, {}).pop('measurements', None)
                self.ready.discard(node)
                self.dedicated_data.discard(node)
                self._notify_node(node)
            if data_subscription is not None and self.data_peers.get(data_subscription) is peer:
                self.data_peers.pop(data_subscription, None)
                if data_subscription in self.engines:
                    self.engines[data_subscription].store.notify()
            self.connections.discard(task)
            await peer.close()

    async def _status_reply(self, peer, header):
        await self.demand.refresh(self.peers[name][0] for name in self.ready if name in self.peers)
        await self._reply(peer, header['request'], self.snapshot())

    async def _recording_reply(self, peer, node, header):
        try:
            if header['watch'] is not True:
                raise ValueError('recording watch must be boolean')
            version, active = await self.recording.activation(header.get('after'))
            await self._reply(peer, header['request'], dict(version=version,
                **self.recording_activation(node, active)))
        except (ValueError, KeyError, TypeError) as error:
            await self._reply(peer, header.get('request'), error=str(error))

    async def _parameter_reply(self, peer, header, body, node):
        try:
            result, binary = await self.parameters.request(peer, header, body,
                node=self.plan['components'][node] if node is not None else None)
            await self._reply(peer, header['request'], result, binary=binary)
        except (ValueError, KeyError, TypeError) as error:
            await self._reply(peer, header.get('request'), error=str(error), error_code='parameters')

    async def _reply(self, peer, request, result=None, *, binary=None, error=None, error_code=None):
        header = {"op": "result", "request": request}
        if error is not None:
            await peer.send({**header, "error": error, "error_code": error_code})
        elif binary is not None:
            await peer.send({**(result or {}), **header}, binary)
        else:
            await peer.send({**header, "content": "json"}, json.dumps(result or {}, separators=(",", ":")).encode())

    async def _publish_reply(self, peer, node, header, body, session=None):
        try:
            try:
                topic = header["topic"]
                if not self.enabled:
                    raise ClientError("not_ready", "broker is not enabled")
                if session is not None:
                    if self.publisher_owners.get(topic) != session.identity:
                        raise ClientError("ownership", "declare publisher before publication")
                elif node is None or topic not in {f"{node}.{p}" for p in self.plan["components"][node]["outputs"]}:
                    raise ClientError("ownership", "publication is not enabled for this component")
                if "app_id" in header:
                    validate_app_id(header["app_id"])
                result = await self.publish(header["topic"], body,
                    **({"app_id": header["app_id"]} if "app_id" in header else {}))
                await self._reply(peer, header["request"], result)
            except (ValueError, KeyError) as error:
                await self._reply(peer, header.get("request"), error=str(error), error_code=getattr(error, "code", "invalid_request"))
        except ConnectionError:
            pass  # The connection's owner cancels any remaining admission waits.

    def _ack(self, node, header, session=None):
        sub = self.subscriptions.get(header["subscription"])
        if sub is None:
            return  # An ACK may race an unsubscribe.
        if (sub.get("session") != session.identity if session else sub.get("session") is not None or sub["to"].split(".")[0] != node):
            raise ValueError("acknowledgement from wrong consumer")
        self.engines[sub["id"]].acknowledge(header["id"])

    async def _udp(self, header, body, address):
        if header["op"] != "ack" or body:
            raise ValueError("expected UDP ACK")
        for node, (_, endpoint) in self.peers.items():
            if endpoint == address:
                self._ack(node, header)
                return
        for session in self.sessions.values():
            if session.endpoint == address:
                self._ack(None, header, session)
                return
        raise ValueError("unknown UDP peer")

    async def publish(self, topic, envelope, *, app_id=None):
        if app_id is not None:
            validate_app_id(app_id)
        port = self.topics[topic]
        try:
            self.codec.validate_only(port["catalogue"], port["root"], envelope)
        except ValueError as error:
            raise ClientError("schema", str(error)) from error
        self.sequence += 1
        self.recording.observe(topic, envelope,
            generation=self.publisher_owners.get(topic, self.epoch), sequence=self.sequence,
            catalogue=port["catalogue"], root=port["root"], app_id=app_id)
        record = {"id": f"{self.epoch}:{self.sequence}", "epoch": self.epoch, "sequence": self.sequence,
                  "topic": topic, "body": bytes(envelope), "published_at": time.time()}
        if app_id is not None:
            record["app_id"] = app_id
        self.retained[topic] = record
        self.topic_counts[topic] += 1
        results = {}
        # Each admission has its own bounded wait; concurrent fan-out avoids coupling subscribers.
        async def admit(identity, sub):
            engine = self.engines.get(identity)
            if engine is None:
                return identity, "closed"
            if not self._fits(identity, record, envelope):
                return identity, "oversize"
            deadline = time.monotonic() + engine.policy.wait_timeout_ms / 1000
            while True:
                engine.store.changed.clear()
                if self.engines.get(identity) is not engine:
                    return identity, "closed"
                result = engine.enqueue(record)
                if result != "full" or engine.policy.overflow != "wait" or time.monotonic() >= deadline:
                    return identity, result
                try:
                    await asyncio.wait_for(engine.store.changed.wait(), max(0, deadline - time.monotonic()))
                except asyncio.TimeoutError:
                    return identity, "full"
        # Snapshot only this topic's routes before admission can suspend.
        matching = list(self.subscriptions_by_topic.get(topic, {}).items())
        if len(matching) == 1:
            identity, result = await admit(*matching[0])
            results[identity] = result
            # Keep delivery and ACK tasks running when admission completes inline.
            await asyncio.sleep(0)
        else:
            for identity, result in await asyncio.gather(*(admit(*pair) for pair in matching)):
                results[identity] = result
        return {"delivery": results, "sequence": record["sequence"], "publication_id": record["id"], "epoch": self.epoch}

    def _header(self, identity, record):
        return {"op": "data", "subscription": identity, "id": record["id"], "epoch": record["epoch"],
                "sequence": record["sequence"], "topic": record["topic"],
                **({"app_id": record["app_id"]} if "app_id" in record else {})}

    def _fits(self, identity, record, envelope):
        engine = self.engines[identity]
        limit = self.plan["max_datagram"] if engine.policy.transport == "udp" else self.plan["max_frame"]
        try:
            encode(self._header(identity, record), envelope, limit)
        except ValueError:
            engine.metrics["failed"] += 1
            return False
        return True

    async def _send_loop(self, identity, engine):
        sub = self.subscriptions[identity]
        node = sub["to"].split(".")[0]
        while not self.closing and not sub.get("closing", False):
            engine.store.changed.clear()
            available, failed = False, False
            external = self.sessions.get(sub.get("session"))
            if self.enabled and sub.get("active", True) and (external is not None or node in self.peers):
                control, udp_address = (external.peer, external.endpoint) if external else self.peers[node]
                tcp = self.data_peers.get(identity) if external or node in self.dedicated_data else control
                if engine.policy.transport == "udp" or tcp is not None:
                    available = True
                    for record in list(engine.due(limit=32)):
                        if not engine.store.has_pending(record["id"]):
                            continue
                        try:
                            header, body = self._header(identity, record), record["body"]
                            if engine.policy.transport == "tcp":
                                # Preserve the one pending write while drain is backpressured.
                                # Cancelling and rewriting would append duplicates to the transport buffer.
                                await tcp.send(header, body)
                            else:
                                await self.udp.send(header, body, udp_address)
                            engine.sent(record["id"])
                            await asyncio.sleep(0)
                        except (ConnectionError, asyncio.TimeoutError):
                            failed = True
                            break
                        except ValueError as error:
                            engine.fail(record["id"], str(error))
            deadline = engine.store.next_deadline() if available and not failed else None
            timeout = max(0, deadline - engine.clock()) if deadline is not None else None
            try:
                await asyncio.wait_for(engine.store.changed.wait(), timeout)
            except asyncio.TimeoutError:
                pass

    def snapshot(self):
        from ose.execution.identity import describe
        observation = {**self.observation, "components": {name: {
            **desc, "connected": name in self.peers, "ready": name in self.ready,
            "generation": self.generations.get(name),
            "presence_age_ms": (time.monotonic() - self.presence_times[name]) * 1000 if name in self.presence_times else None,
            "report_age_ms": (time.monotonic() - self.metric_times[name]) * 1000 if name in self.metric_times else None}
            for name, desc in self.observation["components"].items()}}
        return {"epoch": self.epoch, "process": describe("broker", self.plan), "logs_version": 1, "measurements_version": 1, "observation": observation, "parameters": self.parameters.service.snapshot(),
                "enabled": self.enabled, "ready": sorted(self.ready), "subscriptions": {key: engine.snapshot() for key, engine in self.engines.items()},
                "components": dict(self.metrics), "udp_errors": self.udp.errors, "errors": self.errors,
                "topics": {topic: {"root": port["root"], "publications": self.topic_counts[topic],
                    "frequency_hz": self.topic_counts[topic] / max(time.time() - self.started_at, .001),
                    "age_ms": (time.time() - self.retained[topic]["published_at"]) * 1000 if topic in self.retained else None}
                    for topic, port in self.topics.items()}}

    async def close(self):
        self.closing = True
        await self.demand.close()
        self.parameters.close()
        self.recording.close()
        self.enabled = False
        for task in self.send_tasks:
            task.cancel()
        await asyncio.gather(*self.send_tasks, return_exceptions=True)
        self.server.close()
        for task in list(self.connections):
            task.cancel()
        await asyncio.gather(*self.connections, return_exceptions=True)
        # Python 3.13 also waits for accepted clients here, so close them first.
        await self.server.wait_closed()
        await self.udp.close()
        for engine in self.engines.values():
            engine.store.close()
        self.retained.clear()
        self.catalogues.clear()
