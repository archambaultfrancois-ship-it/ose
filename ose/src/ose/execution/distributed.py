"""Distributed real-time execution with cold replacement and direct TCP routes.

The coordinator authorizes every admission. Leases alone do not fence a partitioned
sender; direct receivers check the current generation with the coordinator too.
"""
import asyncio
from collections import Counter, OrderedDict
import hashlib
import json
import math
from pathlib import Path
import signal
import time
import uuid

from ose.codec.sdl2 import SDL2
from ose.parameters.service import ParameterEndpoint, OPERATIONS as PARAMETER_OPERATIONS
from ose.parameters.bindings import RemoteBindings
from ose.config import EXTERNAL_LIMITS
from ose.delivery.buffers import Buffer
from ose.delivery.engine import DeliveryEngine
from ose.delivery.policies import SubscriptionPolicy
from ose.delivery.stores import MemoryStore
from ose.delivery.messages import validate_app_id
from ose.execution.leadership import lease_clock
from ose.execution.process import stop_component
from ose.execution.plan import require_v2, read_plan
from ose.execution.observation import describe, ObservationDemand, subscription_schema
from ose.transport.protocol import encode
from ose.transport.secure import RPCServer, connect, finish_cleanup, identity, loopback
from ose.recording.taps import TapHub, OPERATIONS as RECORDING_OPERATIONS
from ose.recording.component import ComponentRecording, validate_name, MAX_POINTS
from ose.recording.inputs import InputRecording, input_points, input_key
from ose.execution.logs import LogStore


def fingerprint(plan):
    logical = {"version": plan["version"],
               "components": {name: {key: node.get(key) for key in
                ("inputs", "outputs", "parameters", "env", "execution_model")} for name, node in plan["components"].items()},
               "subscriptions": plan["subscriptions"], "max_frame": plan["max_frame"],
               "external_topics": plan.get("external_topics", {}),
               "external_limits": plan.get("broker", {}).get("external", {}),
               "shared_parameters": plan.get("shared_parameters", {}),
               "parameter_clients": plan.get("parameter_clients", {}),
               "parameter_bindings": {name: node.get("parameter_bindings", {}) for name, node in plan["components"].items()}}
    return hashlib.sha256(json.dumps(logical, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def check_plan(plan):
    require_v2(plan)
    from ose.modeling.activation import activation_order
    activation_order(plan)
    if plan.get("execution") != "distributed":
        raise ValueError("use a deployment with execution=distributed")
    if set(plan["components"]) & {"coordinator", "replay"}:
        raise ValueError("coordinator and replay are reserved distributed identities")
    for sub in plan["subscriptions"]:
        if sub["policy"]["transport"] != "tcp":
            raise ValueError("distributed execution currently requires TCP")
        if sub.get("routing", "broker") not in ("broker", "direct"):
            raise ValueError("routing must be broker or direct")
        if "from_topic" in sub and sub.get("routing", "broker") != "broker":
            raise ValueError("external inputs require broker routing")


def application_id(metadata):
    """Preserve absence separately from zero, without changing transport identities."""
    return {"app_id": validate_app_id(metadata["app_id"])} if "app_id" in metadata else {}


async def admit(buffer, record, valid=lambda: None):
    deadline = time.monotonic() + buffer.policy.wait_timeout_ms / 1000
    while True:
        valid()
        buffer.store.changed.clear()
        result = buffer.admit(record) if isinstance(buffer, Buffer) else buffer.enqueue(record)
        if result != "full" or buffer.policy.overflow != "wait" or time.monotonic() >= deadline:
            return result
        try:
            await asyncio.wait_for(buffer.store.changed.wait(), max(0, deadline - time.monotonic()))
        except asyncio.TimeoutError:
            return "full"


def engine(sub):
    policy = SubscriptionPolicy.from_dict(sub["policy"])
    return DeliveryEngine(policy, MemoryStore(policy.capacity))


class Coordinator:
    def __init__(self, plan, context=None, lease_seconds=6, authority=lambda: None, *, disabled=()):
        check_plan(plan)
        if not math.isfinite(lease_seconds) or lease_seconds < .3:
            raise ValueError("lease must be finite and at least 0.3 seconds")
        self.plan, self.codec = plan, SDL2(plan["sdl_root"])
        from ose.execution.components import ComponentStates
        self.components = ComponentStates(plan, disabled)
        self.parameters = ParameterEndpoint(plan, self.codec)
        self.lease_seconds = lease_seconds
        self.authority = authority
        self.epoch, self.plan_id = uuid.uuid4().hex, fingerprint(plan)
        self.logs = LogStore(self.epoch)
        self.sessions, self.metrics = {}, Counter()
        self.observation = describe(plan)
        self.demand = ObservationDemand()
        self.external_peers, self.external_owners, self.external_generations = {}, {}, OrderedDict()
        from ose.execution.publications import Producers
        self.producers = Producers(plan, self.codec)
        self.catalogues = {self.codec.identity(desc['catalogue']): desc['catalogue']
                           for desc in self.producers.topics.values()}
        self.catalogue_baseline = len(self.catalogues)
        self.external_limits = {**EXTERNAL_LIMITS, **plan.get("broker", {}).get("external", {})}
        self.subscriptions = {s["id"]: s for s in plan["subscriptions"]}
        self.engines = {key: engine(sub) for key, sub in self.subscriptions.items()
                        if sub.get("routing", "broker") == "broker"}
        self.activated = False
        self.recording = TapHub(self.recording_points, plan["max_frame"], codec=self.codec)
        self.rpc = RPCServer(plan["max_frame"], self.request, context, self.disconnected)

    async def start(self, host="127.0.0.1", port=0):
        self.port = await self.rpc.start(host, port)
        self.monitor = asyncio.create_task(self._monitor())
        return self.port

    def current(self, name, token=None, *, ready=False):
        self.authority()
        session = self.sessions.get(name)
        if session is None or lease_clock() - session["seen"] >= self.lease_seconds:
            raise ValueError("component lease expired")
        if token is not None and session["token"] != token:
            raise ValueError("obsolete component generation")
        if ready and not session["ready"]:
            raise ValueError("component is not ready")
        return session

    def retire(self, name):
        session = self.sessions.pop(name, None)
        if not session:
            return
        self.components.disconnected(name)
        self.demand.forget(session['peer'])
        self.metrics["retired"] += 1
        # No input replay or sender state survives a component generation change.
        for key, delivery in self.engines.items():
            sub = self.subscriptions[key]
            if name == sub["to"].split(".")[0] or ("from_topic" not in sub and name == sub["from"].split(".")[0]):
                if name == sub['to'].split('.')[0]:
                    delivery.store.close()
                else:
                    for record in delivery.store.records():
                        if record.get('source_token') == session['token']:
                            delivery.store.remove(record['id'])
                delivery.store.notify()

    def disconnected(self, peer):
        self.parameters.disconnect(peer)
        self.recording.disconnected(peer)
        self.release_external(peer)
        for name, session in list(self.sessions.items()):
            if session["peer"] is peer:
                self.retire(name)

    async def _monitor(self):
        while True:
            await asyncio.sleep(self.lease_seconds / 3)
            for name, session in list(self.sessions.items()):
                if lease_clock() - session["seen"] >= self.lease_seconds:
                    self.retire(name)
                    session["peer"].writer.close()

    def directory(self):
        nodes = {}
        for name in list(self.sessions):
            try:
                session = self.current(name, ready=True)
            except ValueError:
                continue
            if self.components.observed[name] is False:
                continue
            nodes[name] = {key: session[key] for key in ("host", "port", "token")}
            nodes[name]['version'] = self.components.desired[name]['version']
        return {"epoch": self.epoch, "enabled": self.activated, "nodes": nodes,
                "lease_seconds": self.lease_seconds}

    def recording_points(self):
        nodes = self.directory()["nodes"]
        points = {}
        for name in self.plan["components"]:
            endpoint = ({"host": nodes[name]["host"], "port": nodes[name]["port"], "identity": name}
                        if name in nodes else None)
            points.update(input_points(self.plan, name, nodes.get(name, {}).get("token"),
                                       self.activated and name in nodes, endpoint))
        for name, session in self.sessions.items():
            for local, desc in session.get("recording_points", {}).items():
                topic = f"{name}.record.{local}"
                points[topic] = dict(desc, topic=topic, kind="internal", component=name, name=local,
                    available=self.activated and name in nodes, generation=session["token"],
                    endpoint={"host": session["host"], "port": session["port"], "identity": name})
        for topic, desc in self.producers.topics.items():
            points[topic] = dict(topic=topic, root=desc['root'], catalogue=desc['catalogue'],
                kind='output' if topic not in self.plan.get('external_topics', {}) else 'external',
                generation=self.epoch, available=self.activated, endpoint=None, multipublisher=True)
        return points

    def authorize(self, sub, source_token, target_token):
        if not self.activated:
            raise ValueError("graph is not activated")
        if source_token in self.external_generations:
            if sub['from'] not in self.external_generations[source_token]:
                raise ValueError('publisher did not declare this topic')
        elif 'from_topic' in sub:
            raise ValueError('obsolete external publisher generation')
        else:
            self.current(sub['from'].split('.')[0], source_token, ready=True)
        self.current(sub["to"].split(".")[0], target_token, ready=True)

    def release_external(self, peer):
        session = self.external_peers.pop(peer, None)
        if session:
            self.producers.release(session['token'])
            for topic in session['topics']:
                owners = self.external_owners.get(topic, set())
                owners.discard(session['token'])
                if not owners:
                    self.external_owners.pop(topic, None)
        # Already admitted publications drain; release never closes a shared link.

    def _publisher(self, peer, client_id=None):
        if not self.external_limits['enabled']:
            raise ValueError('external publishers are disabled')
        if peer in self.external_peers or any(s['peer'] is peer for s in self.sessions.values()):
            raise ValueError('connection already identified')
        if len(self.external_peers) >= self.external_limits['max_clients']:
            raise ValueError('too many external publishers')
        if client_id is not None and (not isinstance(client_id, str) or not 0 < len(client_id) <= 128):
            raise ValueError('invalid client identity')
        if client_id is not None and any(s.get('client_id') == client_id for s in self.external_peers.values()):
            raise ValueError('duplicate client identity')
        token = uuid.uuid4().hex
        session = dict(token=token, topics=set(), sequence=0, client_id=client_id)
        self.external_peers[peer] = session
        self.external_generations[token] = set()
        # Bound retired generations independently of the lifetime of the application.
        while len(self.external_generations) > 4096:
            oldest = next((key for key in self.external_generations
                           if all(s['token'] != key for s in self.external_peers.values())), None)
            if oldest is None:
                break
            self.external_generations.pop(oldest)
        return session

    def _declare(self, session, topic, root, catalogue):
        if topic not in self.producers.topics and len(self.producers.topics) >= self.external_limits['max_topics']:
            raise ValueError('too many topics')
        identity = self.codec.identity(catalogue)
        if identity in self.catalogues and self.catalogues[identity] != catalogue:
            raise ValueError('CATALOGUE_COLLISION')
        if identity not in self.catalogues and len(self.catalogues) - self.catalogue_baseline >= self.external_limits['max_catalogues']:
            raise ValueError('too many catalogues')
        try:
            descriptor = self.producers.declare(session['token'], topic, root, catalogue)
        except ValueError as error:
            from ose.execution.sessions import ClientError
            raise ClientError('schema', str(error)) from error
        self.catalogues[identity] = catalogue
        for key, sub in self.subscriptions.items():
            if sub['from'] == topic and key not in self.engines:
                self.engines[key] = engine(sub)
        session['topics'].add(topic)
        self.external_generations[session['token']].add(topic)
        self.external_owners.setdefault(topic, set()).add(session['token'])
        return descriptor

    async def external_request(self, peer, header, body, caller):
        op = header['op']
        if op == 'client_hello':
            session = self._publisher(peer, header.get('client_id'))
            return dict(session=session['token'], udp_port=self.port, max_frame=self.plan['max_frame'],
                max_datagram=self.plan.get('max_datagram', 1200), max_wait_timeout_ms=max(
                    self.external_limits['max_wait_timeout_ms'],
                    max((sub['policy']['wait_timeout_ms'] for sub in self.subscriptions.values()), default=0))), None
        if op == 'external_open':
            if header.get('plan_id') != self.plan_id:
                raise ValueError('application plan mismatch')
            topics = header.get('topics')
            if not isinstance(topics, list) or not topics or len(set(topics)) != len(topics) or any(t not in self.producers.topics for t in topics):
                raise ValueError('select known application topics')
            if not self.activated:
                raise ValueError('graph is not activated')
            session = self._publisher(peer)
            try:
                for topic in topics:
                    desc = self.producers.topics[topic]
                    self._declare(session, topic, desc['root'], desc['catalogue'])
            except BaseException:
                self.release_external(peer)
                raise
            return dict(token=session['token'], epoch=self.epoch), None
        session = self.external_peers.get(peer)
        if session is None:
            raise ValueError('publisher session required')
        if op == 'external_close':
            if header.get('token') != session['token']:
                raise ValueError('unknown external publisher session')
            self.release_external(peer)
            return {}, None
        if op == 'list_topics':
            return {'topics': [dict(topic=t, root=d['root'], catalogue_id=self.codec.identity(d['catalogue']),
                publisher_connected=t in self.external_owners or t.split('.')[0] in self.sessions,
                producer_count=len(self.external_owners.get(t, ())) + int(t.split('.')[0] in self.sessions
                    and self.components.observed[t.split('.')[0]] is not False))
                for t, d in self.producers.topics.items()]}, None
        if op == 'catalogue':
            text = self.catalogues.get(header['catalogue_id'])
            if text is None:
                raise ValueError('unknown catalogue')
            return {}, text.encode()
        topic = header['topic']
        if op == 'declare_publisher':
            desc = self._declare(session, topic, header['root'], body.decode())
            return dict(topic=topic, catalogue_id=desc['catalogue_id']), None
        if topic not in session['topics']:
            raise ValueError('declare publisher before publication')
        if op == 'release_publisher':
            self.producers.release(session['token'], topic)
            self.external_owners[topic].discard(session['token'])
            if not self.external_owners[topic]:
                self.external_owners.pop(topic)
            session['topics'].remove(topic)
            return {}, None
        if not self.external_limits['enabled']:
            raise ValueError('external publishers are disabled')
        if op not in ('publish', 'external_publish'):
            raise ValueError('publisher-only session')
        if op == 'external_publish' and header.get('token') != session['token']:
            raise ValueError('unknown external publisher session')
        if not self.activated:
            raise ValueError('graph is not activated')
        desc = self.producers.members[session['token'], topic]
        extra = application_id(header)
        self.codec.validate_only(desc['catalogue'], desc['root'], body)
        session['sequence'] += 1
        sequence, token = session['sequence'], session['token']
        self.recording.observe(topic, body, generation=self.epoch, sequence=sequence,
                               catalogue=desc['catalogue'], root=desc['root'], producer=token, **extra)
        record = dict(id=f'{token}:{sequence}', epoch=self.epoch, sequence=sequence,
            source_token=token, source=topic, body=body, topic=topic, catalogue=desc['catalogue'], **extra)
        result = await self.publish_record(record, external=True)
        self.metrics['external_publications'] += 1
        return dict(result, publication_id=record['id'], epoch=self.epoch, sequence=sequence), None

    async def publish_record(self, record, *, external=False):
        async def enqueue(key, delivery):
            sub = self.subscriptions[key]
            if sub["from"] != record["topic"] or (not external and sub.get('routing') == 'direct'):
                return None
            try:
                target_name = sub['to'].split('.')[0]
                if self.components.observed[target_name] is False:
                    return key, 'disabled'
                target = self.current(target_name, ready=True)
            except ValueError:
                return key, "unavailable"
            version = self.components.desired[target_name]['version']
            value = dict(record, target_version=version)
            metadata = {k: value[k] for k in ("id", "epoch", "sequence", "source_token", "source", "topic", "app_id", "target_version") if k in value}
            try:
                encode({"op": "result", "request": 2**63 - 1, "record": metadata}, record["body"], self.plan["max_frame"])
            except ValueError:
                return key, "oversize"
            def valid():
                self.authorize(sub, record['source_token'], target['token'])
                if self.components.observed[target_name] is False or self.components.desired[target_name]['version'] != version:
                    raise ValueError('obsolete target activation')
            try:
                return key, await admit(delivery, value, valid)
            except ValueError:
                return key, "unavailable"
        results = dict(pair for pair in await asyncio.gather(*(enqueue(key, delivery)
                       for key, delivery in self.engines.items())) if pair is not None)
        if results:
            self.metrics["broker_publications"] += 1
            self.metrics["broker_bytes"] += len(record["body"])
        return {"delivery": results}

    async def request(self, peer, header, body):
        self.authority()
        op = header["op"]
        caller = identity(peer.writer)
        if op in PARAMETER_OPERATIONS:
            node = None
            if "node" in header:
                name = header["node"]
                session = self.current(name, header.get("token"))
                if session["peer"] is not peer or (caller is not None and caller != name):
                    raise ValueError("invalid parameter worker session")
                node = self.plan["components"][name]
            return await self.parameters.request(peer, header, body, node=node, certificate=caller)
        if peer in self.parameters.external:
            raise ValueError("parameter-only client session")
        if op in ('component_status', 'component_set', 'component_poll', 'component_ack'):
            if op == 'component_status':
                return self.components.snapshot(), None
            if op == 'component_set':
                return await self.components.set(header['components'], header['enabled']), None
            name = header['node']
            session = self.current(name, header['token'])
            if session['peer'] is not peer or (caller is not None and caller != name):
                raise ValueError('component connection required')
            if op == 'component_poll':
                return await self.components.poll(name, header.get('after')), None
            accepted = self.components.acknowledge(name, header['version'], header['enabled'])
            if accepted and header['enabled'] is False:
                for key, sub in self.subscriptions.items():
                    if sub['to'].split('.')[0] == name and key in self.engines:
                        for record in self.engines[key].store.records():
                            self.engines[key].store.remove(record['id'])
            return {}, None
        if op in RECORDING_OPERATIONS:
            if op == "recording_open":
                selected = header.get("points")
                if not isinstance(selected, dict) or set(selected) - self.producers.topics.keys():
                    raise ValueError("coordinator taps require application topics")
            result = await self.recording.request(peer, header)
            if op == 'recording_open':
                selected = set(header['points'])
                version = self.recording.activation_version
                deadline = time.monotonic() + 5
                while any(s.get('output_recording_version', -1) < version for name, s in self.sessions.items()
                          if any(t.startswith(name + '.') for t in selected)):
                    if time.monotonic() >= deadline:
                        await self.recording.request(peer, dict(op='recording_close', session=result[0]['session']))
                        raise ValueError('workers did not acknowledge output recording activation')
                    await asyncio.sleep(.01)
            return result
        if op == "status":
            await self.demand.refresh(s['peer'] for s in self.sessions.values() if s['ready'])
            return self.snapshot(), None
        if op == 'logs':
            return self.logs.query(**header.get('filters', {})), None
        if op == 'subscription_schema':
            result, catalogue = subscription_schema(self.plan, self.subscriptions[header['subscription']], header.get('side', 'source'))
            return {**result, 'epoch': self.epoch}, catalogue
        if op in ('client_hello', 'declare_publisher', 'release_publisher', 'list_topics', 'external_open', 'external_publish', 'external_close') or (peer in self.external_peers and op in ('publish', 'catalogue')):
            return await self.external_request(peer, header, body, caller)
        if peer in self.external_peers:
            raise ValueError("external publisher cannot act as a worker")
        name = header["node"]
        if caller is not None and caller != name:
            raise ValueError("certificate does not identify this component")
        if name not in self.plan["components"]:
            raise ValueError("unknown component")
        if op == "register":
            if header.get("plan_id") != self.plan_id:
                raise ValueError("application plan mismatch")
            if name in self.sessions:
                try:
                    self.current(name)
                except ValueError:
                    self.retire(name)
                else:
                    raise ValueError("component already has a live generation")
            host, port = header["host"], header["port"]
            if not isinstance(host, str) or not host or len(host) > 253 or type(port) is not int or not 0 < port < 65536:
                raise ValueError("invalid component endpoint")
            if self.rpc.context is None and not loopback(host):
                raise ValueError("insecure endpoints must be loopback")
            token = uuid.uuid4().hex
            self.sessions[name] = dict(token=token, host=host, port=port, peer=peer,
                                       ready=False, seen=lease_clock(), metrics={},
                                       observation_generation=uuid.uuid4().hex, metric_time=None, presence_time=None)
            self.metrics["registrations"] += 1
            reporting = {'reporting': self.demand.register(peer)} if header.get('observe_metrics') is True else {}
            return {"token": token, **self.directory(), **reporting}, None
        session = self.current(name, header["token"])
        if session["peer"] is not peer:
            raise ValueError("generation belongs to another control connection")
        if op == 'catalogue':
            text = self.catalogues.get(header['catalogue_id'])
            if text is None:
                raise ValueError('unknown catalogue')
            return {}, text.encode('utf-8')
        if op == 'output_recording_state':
            version, active = await self.recording.activation(header.get('after'))
            return dict(version=version, topics=sorted(active & self.producers.topics.keys())), None
        if op == 'output_recording_ack':
            version = header['version']
            if type(version) is not int or version != self.recording.activation_version:
                return {}, None
            session['output_recording_version'] = version
            return {}, None
        if op == "recording_authorize":
            self.current(name, header["token"], ready=True)
            if not self.activated:
                raise ValueError("graph is not activated")
            return {}, None
        if op == "recording_declare":
            local = validate_name(header["name"])
            points = session.setdefault("recording_points", {})
            if (session["ready"] and self.components.observed[name] is True) or local in points or f"{name}.record.{local}" in self.plan.get("external_topics", {}) or len(points) >= MAX_POINTS:
                raise ValueError("recording declarations require initialization and unique names")
            root, description, catalogue = header["root"], header.get("description", ""), body.decode()
            if not isinstance(description, str) or len(description.encode()) > 1024 or "\0" in description:
                raise ValueError("invalid recording description")
            if root not in self.codec.api.message_names(self.codec.api.prepare(catalogue)):
                raise ValueError("unknown recording root")
            points[local] = dict(root=root, description=description, catalogue=catalogue)
            return {}, None
        if op == "heartbeat":
            session["seen"] = lease_clock()
            return self.directory(), None
        if op == "unregister":
            self.retire(name)
            return {}, None
        if op == "ready":
            session["ready"] = True
            if set(self.sessions) == set(self.plan["components"]) and all(s["ready"] for s in self.sessions.values()):
                self.activated = True
            reporting = {'reporting': self.demand.state()} if peer in self.demand.members else {}
            return {**self.directory(), **reporting}, None
        if op == "metrics":
            value = json.loads(body)
            if not isinstance(value, dict):
                raise ValueError("metrics must be an object")
            full = header.get('details', True) is not False and not set(value) <= {'log_records', 'logs_dropped'}
            if 'measurements' in value:
                from ose.execution.measurements import validate_snapshot
                value['measurements'] = validate_snapshot(value['measurements'])
            self.logs.append(name, session['observation_generation'], value.pop('log_records', []))
            if not full:
                value = {**session['metrics'], **value}
            session["metrics"] = value
            session['presence_time'] = time.monotonic()
            if full:
                session["metric_time"] = session['presence_time']
                self.demand.received(peer, header.get('report_version'))
            reporting = {'reporting': self.demand.state()} if peer in self.demand.members else {}
            return reporting, None
        if op == "authorize":
            sub = self.subscriptions[header["subscription"]]
            if sub["to"].split(".")[0] != name:
                raise ValueError("authorization must come from the recipient")
            self.authorize(sub, header["source_token"], session["token"])
            return {"epoch": self.epoch}, None
        if op == "publish":
            self.current(name, header["token"], ready=True)
            if not self.activated:
                raise ValueError("graph is not activated")
            topic = header["topic"]
            source, port = topic.split(".")
            if source != name or port not in self.plan["components"][name]["outputs"]:
                raise ValueError("publication from wrong component")
            desc = self.plan["components"][name]["outputs"][port]
            self.codec.validate_only(desc["catalogue"], desc["root"], body)
            sequence = header["sequence"]
            if type(sequence) is not int or sequence < 1:
                raise ValueError("invalid publication sequence")
            self.recording.observe(topic, body, generation=self.epoch, sequence=sequence,
                catalogue=desc['catalogue'], root=desc['root'], producer=session['token'], **application_id(header))
            record = dict(id=f"{session['token']}:{sequence}", epoch=self.epoch, sequence=sequence,
                          source_token=session["token"], source=name, body=body, topic=topic, **application_id(header))
            return await self.publish_record(record), None
        if op in ("poll", "ack"):
            key = header["subscription"]
            sub = self.subscriptions[key]
            if sub["to"].split(".")[0] != name:
                raise ValueError("invalid broker subscription")
            delivery = self.engines.get(key)
            if delivery is None:
                await asyncio.sleep(.25)
                return {'empty': True}, None
            if op == "ack":
                delivery.acknowledge(header["id"])
                return {}, None
            # Short long-polls bound idle traffic and share the control connection.
            deadline = time.monotonic() + .25
            while True:
                self.current(name, header["token"], ready=True)
                delivery.store.changed.clear()
                for record in list(delivery.due(limit=1)):
                    try:
                        self.authorize(sub, record["source_token"], session["token"])
                    except ValueError:
                        delivery.fail(record["id"], "obsolete_generation")
                        continue
                    delivery.sent(record["id"])
                    return {"record": {key: value for key, value in record.items()
                                       if key in ("id", "epoch", "sequence", "source_token", "source", "topic", "app_id", "target_version")}}, record["body"]
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return {"empty": True}, None
                next_due = delivery.store.next_deadline()
                if next_due is not None:
                    remaining = min(remaining, max(.001, next_due - time.monotonic()))
                try:
                    await asyncio.wait_for(delivery.store.changed.wait(), remaining)
                except asyncio.TimeoutError:
                    pass
        raise ValueError("unknown distributed operation")

    def snapshot(self):
        from ose.execution.identity import describe
        observation = {**self.observation, "components": {name: {
            **desc, "connected": name in self.sessions,
            "ready": self.sessions.get(name, {}).get("ready", False),
            "generation": self.sessions.get(name, {}).get("observation_generation"),
            "presence_age_ms": (time.monotonic() - self.sessions[name]['presence_time']) * 1000
                if self.sessions.get(name, {}).get('presence_time') is not None else None,
            "report_age_ms": (time.monotonic() - self.sessions[name]["metric_time"]) * 1000
                if self.sessions.get(name, {}).get("metric_time") is not None else None}
            for name, desc in self.observation["components"].items()}}
        return {"epoch": self.epoch, "process": describe("coord", self.plan), "logs_version": 1, "measurements_version": 1, "observation": observation, "parameters": self.parameters.service.snapshot(),
                "component_states": self.components.snapshot(), "enabled": self.activated, "metrics": dict(self.metrics),
                "external_publishers": len(self.external_peers),
                "components": {name: {"ready": session["ready"], "host": session["host"],
                    "port": session["port"], "metrics": session["metrics"]} for name, session in self.sessions.items()},
                "subscriptions": {key: delivery.snapshot() for key, delivery in self.engines.items()}}

    async def close(self):
        await self.demand.close()
        self.recording.close()
        if hasattr(self, "monitor"):
            self.monitor.cancel()
            await asyncio.gather(self.monitor, return_exceptions=True)
        await self.rpc.close()
        self.parameters.close()
        for delivery in self.engines.values():
            delivery.store.close()


from ose.execution.realtime import RealtimeRuntime


class Worker(RealtimeRuntime):
    def __init__(self, plan, name, host, port, *, listen_host="127.0.0.1", listen_port=0,
                 advertise_host="127.0.0.1", server_context=None, client_context=None, working_directory=None):
        check_plan(plan)
        self.plan, self.name, self.node = plan, name, plan["components"][name]
        self.host, self.port = host, port
        self.listen_host, self.listen_port, self.advertise_host = listen_host, listen_port, advertise_host
        self.context = client_context
        self.codec = SDL2(plan["sdl_root"])
        self.subscriptions = {s["id"]: s for s in plan["subscriptions"]}
        self.incoming = {key: s for key, s in self.subscriptions.items() if s["to"].split(".")[0] == name}
        self.outgoing = {key: s for key, s in self.subscriptions.items()
                         if s["from"].split(".")[0] == name and s.get("routing", "broker") == "direct"}
        self.setup_execution(self.incoming.values(), working_directory=working_directory)
        self.locks = {key: asyncio.Lock() for key in self.incoming}
        self.engines = {key: engine(sub) for key, sub in self.outgoing.items()}
        for delivery in self.engines.values():
            delivery.store.on_change = self.reports.mark
        self.recording = TapHub(self.recording_points, plan["max_frame"], codec=self.codec)
        self.input_recording = InputRecording(self.codec, self.node,
                                             self._active_inputs, self._emit_input)
        self.internal_recording = ComponentRecording(self.codec, declare=self._declare_recording,
            active=self._active_recording, emit=self._emit_recording)
        self.recording_endpoint = self.internal_recording
        self.recording_sequence = 0
        self.rpc = RPCServer(plan["max_frame"], self.receive, server_context, self.recording.disconnected)
        self.metrics = Counter()
        self.directory = {"nodes": {}, "enabled": False}
        self.started = asyncio.Event()
        self.started_at = 0
        self.sequence = 0
        self.observed_outputs = set()
        self.direct_clients = {}

    def recording_points(self):
        points = {f"{self.name}.{port}": dict(topic=f"{self.name}.{port}", root=desc["root"], catalogue=desc["catalogue"], kind="output",
                    generation=getattr(self, "token", None), available=self.started.is_set(), endpoint=None)
                for port, desc in self.node["outputs"].items()}
        points.update(input_points(self.plan, self.name, getattr(self, "token", None), self.started.is_set()))
        for local, desc in self.internal_recording.points.items():
            topic = f"{self.name}.record.{local}"
            points[topic] = dict(desc, topic=topic, kind="internal", component=self.name, name=local,
                generation=getattr(self, "token", None), available=self.started.is_set(), endpoint=None)
        return points

    def _emit_input(self, port, desc, envelope, tick_index, *, app_id=None):
        self.recording.observe(input_key(self.plan, self.name, port), envelope,
            generation=self.token, sequence=tick_index, catalogue=desc["catalogue"], root=desc["root"], app_id=app_id)

    def _active_inputs(self):
        if not self.recording.sessions:
            return ()
        active = self.recording.active_points()
        return {port for port in self.node["inputs"] if input_key(self.plan, self.name, port) in active}

    async def _declare_recording(self, name, desc):
        await self.control("recording_declare", desc["catalogue"].encode(), name=name,
                           root=desc["root"], description=desc["description"])

    def _active_recording(self):
        prefix = f"{self.name}.record."
        return [topic[len(prefix):] for topic in self.recording.active_points() if topic.startswith(prefix)]

    def _emit_recording(self, name, desc, payload):
        topic = f"{self.name}.record.{name}"
        if topic not in self.recording.active_points():
            return
        envelope = self.codec.wrap(desc["catalogue"], payload)
        self.codec.validate_only(desc["catalogue"], desc["root"], envelope)
        self.recording_sequence += 1
        self.recording.observe(topic, envelope, generation=self.token, sequence=self.recording_sequence,
                               catalogue=desc["catalogue"], root=desc["root"])

    async def control(self, op, body=b"", **kwargs):
        kwargs.setdefault('timeout', self.request_timeout)
        return await self.client.request(op, body, node=self.name, token=self.token, **kwargs)

    async def run(self):
        tasks = []
        try:
            port = await self.rpc.start(self.listen_host, self.listen_port)
            self.client = await connect(self.host, self.port, self.plan["max_frame"], self.context, "coordinator")
            self.client.on_event = self.reports.event
            result, _ = await self.client.request("register", node=self.name, host=self.advertise_host,
                port=port, plan_id=fingerprint(self.plan), observe_metrics=True)
            self.reports.configure(result.get('reporting'))
            self.token, self.epoch = result["token"], result["epoch"]
            self.lease_seconds = result["lease_seconds"]
            self.last_contact = lease_clock()
            # Renew during native/user initialization too.
            tasks = [asyncio.create_task(self._heartbeat()), asyncio.create_task(self._watchdog()), self.client.task]
            self.parameters = await RemoteBindings(self.node, self.control, self.activity).open()
            if self.parameters.task:
                tasks.append(self.parameters.task)
            self.supervision_request = self.control
            initialization = asyncio.create_task(self.initialize_component_state())
            tasks.append(initialization)
            done, _ = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
            if initialization not in done:
                initialization.cancel()
                await asyncio.gather(initialization, return_exceptions=True)
                for task in done:
                    task.result()
                raise RuntimeError("control connection failed during initialization")
            initialization.result()
            tasks.remove(initialization)
            self.directory, _ = await self.control("ready")
            self.reports.configure(self.directory.get('reporting'))
            self.started.set()
            tasks += [asyncio.create_task(self._output_recording()), asyncio.create_task(self._work()), asyncio.create_task(self._metrics()),
                      asyncio.create_task(self.component_exit()), asyncio.create_task(self.component_control()),
                      *(asyncio.create_task(self._poll(key)) for key, sub in self.incoming.items()
                        ),
                      *(asyncio.create_task(self._send(key)) for key in self.outgoing)]
            done, _ = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
            for task in done:
                task.result()
            raise RuntimeError(f"component {self.name} stopped")
        finally:
            await finish_cleanup(self.close(tasks))

    async def close(self, tasks):
        # Python 3.10 wait_for can absorb cancellation when its child has just
        # completed. Wakeups and explicit state stop those loops anyway.
        self.closing = True
        self.reports.close()
        self.activity.set()
        if hasattr(self, "parameters"):
            await self.parameters.close()
        for delivery in self.engines.values():
            delivery.store.notify()
        self.recording.close()
        reader = getattr(getattr(self, "client", None), "task", None)
        for task in tasks:
            if task is not reader:
                task.cancel()
        await asyncio.gather(*(task for task in tasks if task is not reader), return_exceptions=True)
        await self.rpc.close()
        if hasattr(self, "process"):
            await stop_component(self.process)
        if hasattr(self, 'token'):
            try:
                if hasattr(self, 'process'):
                    await asyncio.wait_for(self._send_report(True), .5)
                while self.component_logs.pending:
                    await asyncio.wait_for(self.control('metrics', json.dumps({
                        'log_records': self.component_logs.drain(), 'logs_dropped': self.component_logs.dropped}).encode()), .5)
            except (OSError, EOFError, ValueError, asyncio.TimeoutError):
                pass
        if hasattr(self, "token"):
            try:
                await asyncio.wait_for(self.control("unregister"), .5)
            except (OSError, EOFError, ValueError, asyncio.TimeoutError):
                pass
        for _, client in self.direct_clients.values():
            await client.close()
        if hasattr(self, "client"):
            await self.client.close()
        self.close_execution()
        for delivery in self.engines.values():
            delivery.store.close()

    async def _output_recording(self):
        version = None
        while not self.closing:
            state, _ = await self.control('output_recording_state', after=version)
            self.observed_outputs = set(state['topics'])
            version = state['version']
            await self.control('output_recording_ack', version=version)

    async def _heartbeat(self):
        while not self.closing:
            sent = lease_clock()
            result, _ = await self.control("heartbeat")
            if self.closing:
                return
            if result["epoch"] != self.epoch:
                raise ValueError("coordinator generation changed")
            elapsed = lease_clock() - sent
            if elapsed >= self.lease_seconds:
                raise RuntimeError("coordinator authorization expired")
            self.directory = result
            # The coordinator renews while processing the request, not when
            # its reply finally reaches us. A delayed reply cannot revive us.
            self.last_contact = sent
            self.activity.set()
            for delivery in self.engines.values():
                delivery.store.notify()
            await asyncio.sleep(max(0, self.lease_seconds / 4 - elapsed))

    async def _watchdog(self):
        while not self.closing:
            await asyncio.sleep(self.lease_seconds / 4)
            if lease_clock() - self.last_contact >= self.lease_seconds:
                raise RuntimeError("coordinator authorization expired")

    async def _send_report(self, full):
        from ose.execution.identity import describe
        process = getattr(self, 'process', None)
        metrics = ({**self.metrics, 'running': bool(self.started_at) and self.component_active,
            'process': describe('w', self.plan, self.name, process),
            **self.timings.snapshot(),
            'inputs': {key: value.snapshot() for key, value in self.buffers.items()},
            'direct': {key: value.snapshot() for key, value in self.engines.items()},
            'measurements': process.measurements.snapshot() if process is not None else {}} if full else
            ({'running': bool(self.started_at) and self.component_active} if self.reports.managed else {}))
        metrics['log_records'] = self.component_logs.drain()
        metrics['logs_dropped'] = self.component_logs.dropped
        result, _ = await self.control('metrics', json.dumps(metrics).encode(),
            details=full, report_version=self.reports.version)
        self.reports.configure(result.get('reporting'))

    async def _metrics(self):
        while not self.closing:
            full = await self.reports.next(self.component_logs)
            if full is None:
                return
            await self._send_report(full)
            if self.closing:
                return

    async def receive(self, peer, header, body):
        if not hasattr(self, "token"):
            raise ValueError("recipient is not registered")
        if header["op"] in RECORDING_OPERATIONS:
            await self.control("recording_authorize")
            return await self.recording.request(peer, header)
        key = header["subscription"]
        sub = self.incoming[key]
        source = sub["from"].split(".")[0]
        caller = identity(peer.writer)
        if header["op"] != "deliver" or sub.get("routing") != "direct" or (caller is not None and caller != source):
            raise ValueError("invalid direct sender or route")
        if header.get("target_token") != self.token:
            raise ValueError("obsolete target generation")
        record = {key: header[key] for key in ("id", "epoch", "sequence", "source_token", "source", "topic")}
        if 'target_version' in header:
            record['target_version'] = header['target_version']
        record.update(application_id(header))
        result = await self._admit(key, record, body)
        self.metrics["direct_bytes_received"] += len(body)
        return {"admission": result}, None

    async def _admit(self, key, record, body):
        sub = self.incoming[key]
        if record.get('target_version', 0) != (self.component_version or 0):
            raise ValueError('obsolete target activation')
        if record["epoch"] != self.epoch or record["topic"] != sub["from"]:
            raise ValueError("wrong publication identity")
        if type(record["sequence"]) is not int or record["sequence"] < 1 or record["id"] != f"{record['source_token']}:{record['sequence']}":
            raise ValueError("invalid publication sequence")
        if "from_topic" in sub:
            desc = self.plan["external_topics"][sub["from_topic"]]
        else:
            source_name, source_port = sub["from"].split(".")
            desc = self.plan["components"][source_name]["outputs"][source_port]
        port = sub["to"].split(".")[1]
        catalogue = await self.decode_input(port, body, self.control, desc)
        value = dict(record, body=body, payload=body[self.codec.api.ENVELOPE_HEADER_SIZE:], catalogue=catalogue,
                     **application_id(record))
        from ose.execution.input_ipc import check_record_size
        check_record_size(port, value)
        # Latest sequence numbers belong to a source generation, not a whole run.
        value["epoch"] = record["source_token"]
        value['stream'] = record['source_token']
        async def authorize():
            await self.control("authorize", subscription=key, source_token=record["source_token"])
        async with self.locks[key]:
            return await self.admit_input(port, value, version=record.get('target_version', 0), authorize=authorize)

    async def _poll(self, key):
        while not self.closing:
            result, body = await self.control("poll", subscription=key)
            if self.closing:
                return
            if result.get("empty"):
                continue
            record = result["record"]
            try:
                admission = await self._admit(key, record, body)
            except ValueError:
                self.metrics["obsolete_received"] += 1
                continue
            if admission in ("accepted", "duplicate", "stale") and self.subscriptions[key]["policy"]["ack"] == "received":
                await self.control("ack", subscription=key, id=record["id"])

    async def _work(self):
        while not self.closing and not self.directory["enabled"]:
            self.activity.clear()
            if not self.directory["enabled"]:
                await self.activity.wait()
        if self.closing:
            return
        self.started_at = self.activated_at_ns = time.monotonic_ns()
        await self._input_work()

    async def publish(self, topic, body, *, app_id=None):
        extra = {"app_id": validate_app_id(app_id)} if app_id is not None else {}
        self.sequence += 1
        if self.recording.sessions:
            desc = self.node["outputs"][topic.split(".")[1]]
            self.recording.observe(topic, body, generation=self.token, sequence=self.sequence,
                                   catalogue=desc["catalogue"], root=desc["root"], **extra)
        record = dict(id=f"{self.token}:{self.sequence}", epoch=self.epoch, sequence=self.sequence,
                      source_token=self.token, source=self.name, body=body, topic=topic, **extra)
        broker = any(s["from"] == topic and s.get("routing", "broker") == "broker"
                     for s in self.subscriptions.values())
        if broker or topic in self.observed_outputs:
            result, _ = await self.control("publish", body, topic=topic, sequence=self.sequence, **extra)
            self.metrics["refused"] += sum(v not in ("accepted", "stale") for v in result["delivery"].values())
        for key, sub in self.outgoing.items():
            if sub["from"] != topic:
                continue
            target = self.directory["nodes"].get(sub["to"].split(".")[0])
            if target is None:
                self.metrics["unavailable"] += 1
                continue
            value = dict(record, target_token=target["token"], target_version=target.get('version', 0))
            # Refuse oversize DATA before queue admission, including framing metadata.
            encode(self._header(key, value), body, self.plan["max_frame"])
            admission = await admit(self.engines[key], value)
            if admission not in ("accepted", "stale"):
                self.metrics["refused"] += 1
        self.metrics["publications"] += 1

    def _header(self, key, record):
        return {"op": "deliver", "request": 1, "subscription": key,
                **{key: record[key] for key in ("id", "epoch", "sequence", "source_token", "target_token", "source", "topic", "app_id", "target_version") if key in record}}

    async def _send(self, key):
        sub, delivery = self.outgoing[key], self.engines[key]
        name = sub["to"].split(".")[0]
        while not self.closing:
            delivery.store.changed.clear()
            target = self.directory["nodes"].get(name)
            for record in list(delivery.due(limit=32)):
                if self.closing:
                    return
                if target is None or record["target_token"] != target["token"] or record.get('target_version', 0) != target.get('version', 0):
                    delivery.fail(record["id"], "target_generation_changed")
                    continue
                try:
                    cached = self.direct_clients.get(key)
                    if cached is not None and (cached[0] != target["token"] or cached[1].task.done()):
                        await cached[1].close()
                        self.direct_clients.pop(key)
                        cached = None
                    if cached is None:
                        client = await connect(target["host"], target["port"], self.plan["max_frame"], self.context, name,
                                               timeout=max(1, delivery.policy.wait_timeout_ms / 1000 + 1))
                        self.direct_clients[key] = (target["token"], client)
                    else:
                        client = cached[1]
                    header = self._header(key, record)
                    result, _ = await client.request("deliver", record["body"], **{k: v for k, v in header.items() if k not in ("op", "request")})
                    delivery.sent(record["id"])
                    if result["admission"] in ("accepted", "duplicate", "stale") and delivery.policy.ack == "received":
                        delivery.acknowledge(record["id"])
                    self.metrics["direct_bytes_sent"] += len(record["body"])
                except (OSError, ValueError, asyncio.TimeoutError):
                    # A failed attempt still consumes retry budget, with bounded storage.
                    delivery.sent(record["id"])
            if self.closing:
                return
            deadline = delivery.store.next_deadline()
            timeout = max(.001, deadline - time.monotonic()) if deadline is not None else None
            try:
                await asyncio.wait_for(delivery.store.changed.wait(), timeout)
            except asyncio.TimeoutError:
                pass


async def serve(plan_path, host, port, server_context=None, lease_seconds=6, kubernetes_lease=None, namespace="default", *, disabled=()):
    from ose.execution.identity import identify
    plan = read_plan(plan_path)
    identify("coord", plan)
    from ose.execution.leadership import KubernetesLease
    leader = KubernetesLease(kubernetes_lease, namespace) if kubernetes_lease else None
    coordinator = Coordinator(plan, server_context, lease_seconds,
                              leader.check if leader else lambda: None, disabled=disabled)
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, stop.set)
    stopping = asyncio.create_task(stop.wait())
    acquiring = None
    try:
        if leader:
            acquiring = asyncio.create_task(leader.acquire())
            done, _ = await asyncio.wait([acquiring, stopping], return_when=asyncio.FIRST_COMPLETED)
            if stopping in done:
                return
            acquiring.result()
        await coordinator.start(host, port)
        print(json.dumps({"running": True, "host": host, "port": coordinator.port}), flush=True)
        if leader:
            done, _ = await asyncio.wait([stopping, leader.task], return_when=asyncio.FIRST_COMPLETED)
            if leader.task in done:
                leader.task.result()
        else:
            await stopping
    finally:
        await coordinator.close()
        if acquiring:
            acquiring.cancel()
            await asyncio.gather(acquiring, return_exceptions=True)
        if leader:
            await leader.close()
        stopping.cancel()
        await asyncio.gather(stopping, return_exceptions=True)
        for sig in (signal.SIGINT, signal.SIGTERM):
            loop.remove_signal_handler(sig)


async def work(plan_path, name, host, port, **kwargs):
    plan = read_plan(plan_path)
    from ose.execution.identity import identify
    identify("w", plan, name)
    check_plan(plan)
    if name not in plan["components"]:
        raise ValueError("unknown component")
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, stop.set)
    waiting = asyncio.create_task(stop.wait())
    try:
        # Reconnection is a cold restart; no application or delivery state is restored.
        while not stop.is_set():
            worker = Worker(plan, name, host, port, **kwargs)
            running = asyncio.create_task(worker.run())
            done, _ = await asyncio.wait([running, waiting], return_when=asyncio.FIRST_COMPLETED)
            if waiting in done:
                running.cancel()
            try:
                await running
            except asyncio.CancelledError:
                if not stop.is_set():
                    raise
            except (OSError, EOFError, ValueError, RuntimeError, asyncio.TimeoutError) as error:
                import sys
                print(f"{name}: cold restart: {error}", file=sys.stderr, flush=True)
            if not stop.is_set():
                try:
                    await asyncio.wait_for(stop.wait(), 1)
                except asyncio.TimeoutError:
                    pass
    finally:
        waiting.cancel()
        await asyncio.gather(waiting, return_exceptions=True)
        for sig in (signal.SIGINT, signal.SIGTERM):
            loop.remove_signal_handler(sig)
