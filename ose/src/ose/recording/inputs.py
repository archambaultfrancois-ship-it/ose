"""Input observation at tick selection, independent of component SDKs."""
from ose.delivery.policies import SubscriptionPolicy
from ose.recording.schema_adapter import SchemaAdapters


def input_key(plan, component, port):
    topic = f"{component}.{port}"
    # The public name stays natural; only the wire identity disambiguates a
    # same-named output or external publication point.
    collision = port in plan["components"][component]["outputs"] or topic in plan.get("external_topics", {})
    return "input:" + topic if collision else topic


def input_points(plan, component, generation, available, endpoint=None):
    incoming = {}
    for sub in plan["subscriptions"]:
        if sub["to"].split(".")[0] == component:
            incoming.setdefault(sub["to"].split(".")[1], []).append(sub)
    result = {}
    for port, desc in plan["components"][component]["inputs"].items():
        if desc.get("kind") == "timer":
            continue
        topic = f"{component}.{port}"
        subscriptions = incoming.get(port, [])
        policy = SubscriptionPolicy.from_dict(subscriptions[0]["policy"]) if subscriptions else SubscriptionPolicy()
        key = input_key(plan, component, port)
        result[key] = dict(topic=topic, root=desc["root"], catalogue=desc["catalogue"],
            kind="input", component=component, port=port, generation=generation,
            available=available, endpoint=endpoint, observation="tick",
            sources=sorted({sub["from"] for sub in subscriptions}), reception=policy.reception())
        if key != topic:
            result[key]["id"] = key
    return result


class InputRecording:
    def __init__(self, codec, node, active, emit):
        self.codec, self.node, self.active, self.emit = codec, node, active, emit
        self.adapters = SchemaAdapters(codec, "compatible")

    def observe(self, selected, tick_index):
        active = self.active()
        if not active:
            return
        for port, record in selected.items():
            if port not in active or record is None:
                continue
            desc = self.node["inputs"][port]
            if record["catalogue"] == desc["catalogue"]:
                envelope = record["body"]
            else:
                adapter = self.adapters.get(record["catalogue"], desc["catalogue"], desc["root"])
                envelope = adapter.adapt(record["payload"])
            extra = {"app_id": record["app_id"]} if "app_id" in record else {}
            self.emit(port, desc, envelope, tick_index, **extra)
