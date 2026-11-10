"""Version 2 input declarations and event-to-output activation dependencies."""
from dataclasses import dataclass
from collections.abc import Mapping
from graphlib import CycleError, TopologicalSorter
from types import MappingProxyType

from ose.modeling.model import fields
from ose.timing import seconds_ns


@dataclass(frozen=True)
class Input:
    kind: str
    mode: str
    timeout_ns: int
    timer_mode: str
    automatic: bool
    outputs: Mapping[str, frozenset[str]]


def component_inputs(node, *, compiled=False):
    """Validate a v2 component and return normalized, immutable input declarations."""
    derived = ("execution_model", "process_identity", "command", "build", "cwd", "env", "container", "placement") if compiled else ()
    contracts = ("catalogue", "catalogue_id") if compiled else ()
    fields(node, ("inputs", "outputs", "parameters", "parameter_bindings", "recording_types", *derived),
           ("inputs", "outputs"))
    for direction in ("inputs", "outputs"):
        if not isinstance(node[direction], dict):
            raise ValueError("ports must be an object")
        for name in node[direction]:
            if not isinstance(name, str) or not name or "." in name:
                raise ValueError("port names must be nonempty and contain no dots")
    for desc in node["outputs"].values():
        fields(desc, ("root", "schema", *contracts), ("root",))
        _contract(desc)
    result = {}
    for name, desc in node["inputs"].items():
        if not isinstance(desc, dict):
            raise ValueError("input description must be an object")
        kind = desc.get("kind", "message")
        if kind == "timer":
            fields(desc, ("kind", "timer", "activates"), ("kind", "timer"))
            timer = desc["timer"]
            fields(timer, ("mode", "delay", "start"), ("mode", "delay"))
            if timer["mode"] not in ("periodic", "one_shot"):
                raise ValueError("timer mode must be periodic or one_shot")
            if timer.get("start", "automatic") not in ("automatic", "manual"):
                raise ValueError("timer start must be automatic or manual")
            mode, timer_mode = "push", timer["mode"]
            duration = seconds_ns(timer["delay"], positive=True)
            automatic = timer.get("start", "automatic") == "automatic"
            events = ("timeout",)
        elif kind == "message":
            fields(desc, ("kind", "mode", "root", "schema", "timeout", "activates", *contracts), ("root",))
            _contract(desc)
            mode = desc.get("mode", "push")
            if mode not in ("push", "pull"):
                raise ValueError("input mode must be push or pull")
            if mode == "pull" and "timeout" in desc:
                raise ValueError("a passive Pull input cannot declare an activating timeout")
            duration = seconds_ns(desc["timeout"], positive=True) if "timeout" in desc else 0
            timer_mode, automatic = "", False
            events = ("message", "timeout") if duration else ("message",)
            if mode == "pull":
                events = ()
        else:
            raise ValueError("input kind must be message or timer")
        links = desc.get("activates", {})
        fields(links, events)
        normalized = {}
        for event in events:
            outputs = links.get(event, [])
            if (not isinstance(outputs, list) or any(not isinstance(port, str) for port in outputs)
                    or len(outputs) != len(set(outputs)) or set(outputs) - node["outputs"].keys()):
                raise ValueError(f"invalid {event} activation outputs for input {name}")
            normalized[event] = frozenset(outputs)
        result[name] = Input(kind, mode, duration, timer_mode, automatic, MappingProxyType(normalized))
    return result


def _contract(desc):
    if not isinstance(desc["root"], str) or not desc["root"]:
        raise ValueError("port root must be a nonempty string")
    if "schema" in desc and (not isinstance(desc["schema"], str) or not desc["schema"]):
        raise ValueError("schema must be a nonempty path")


def activation_graph(model):
    """Return dependencies between actual input events and output publications.

    Timeouts are autonomous events, not consequences of a received message.
    A Push callback with no declared outputs is a leaf, even in a data cycle.
    """
    from ose.modeling.validation import endpoint
    components = model["components"]
    inputs = {name: component_inputs(node, compiled=node.get("execution_model") == 2) for name, node in components.items()}
    graph = {}
    for name in sorted(components):
        for port in sorted(components[name]["outputs"]):
            graph[f"{name}.{port}:publish"] = set()
        for port, desc in sorted(inputs[name].items()):
            for event, outputs in desc.outputs.items():
                origin = f"{name}.{port}:{event}"
                graph.setdefault(origin, set())
                for output in outputs:
                    graph[f"{name}.{output}:publish"].add(origin)
    for sub in model["subscriptions"]:
        target, port = endpoint(sub["to"], components, "inputs")
        desc = inputs[target][port]
        if desc.kind == "timer":
            raise ValueError(f"timer input {sub['to']} cannot receive a subscription")
        # Compiled external subscriptions also carry a normalized "from"
        # topic for routing; it does not name a component output.
        if "from" in sub and "from_topic" not in sub and desc.mode == "push":
            source, output = endpoint(sub["from"], components, "outputs")
            graph[f"{target}.{port}:message"].add(f"{source}.{output}:publish")
    return graph


def activation_order(model):
    graph = activation_graph(model)
    try:
        return tuple(TopologicalSorter({key: sorted(graph[key]) for key in sorted(graph)}).static_order())
    except CycleError as error:
        raise ValueError("activation graph contains a cycle: " + " -> ".join(error.args[1])) from None
