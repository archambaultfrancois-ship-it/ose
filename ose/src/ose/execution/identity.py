"""Stable, short Linux process/thread names; independent of Python's version."""
import hashlib
import itertools
from collections import Counter
import os
from pathlib import Path
import re
import sys
import socket
import threading


_PREFIX = re.compile(r"[a-z][a-z0-9]{0,4}\Z")
_ALIAS = re.compile(r"[a-z0-9][a-z0-9-]*\Z")
_label = None
_threads = itertools.count(1)


def validate(config, components):
    if not isinstance(config, dict) or set(config) - {"prefix", "components"}:
        raise ValueError("process_names must contain prefix and/or components")
    prefix = config.get("prefix", "ose")
    if not isinstance(prefix, str) or not _PREFIX.fullmatch(prefix):
        raise ValueError("process_names prefix must be 1..5 lowercase ASCII letters/digits, starting with a letter")
    aliases = config.get("components", {})
    if not isinstance(aliases, dict) or set(aliases) - set(components):
        raise ValueError("process_names components must map known component names to aliases")
    limit = 15 - len(prefix) - 3
    if any(not isinstance(value, str) or not _ALIAS.fullmatch(value) or len(value) > limit
           for value in aliases.values()):
        raise ValueError(f"explicit process aliases must be 1..{limit} lowercase ASCII letters/digits/hyphens")
    resolved = aliases_for(components, aliases, limit)
    if len(set(resolved.values())) != len(resolved):
        raise ValueError("process aliases collide; assign distinct aliases in process_names.components")


def short(text, limit):
    normalized = re.sub(r"[^a-z0-9]+", "", text.lower()) or "node"
    if len(normalized) <= limit:
        return normalized
    digest = hashlib.sha256(text.encode()).hexdigest()
    if limit <= 5:
        return digest[:limit]
    return normalized[:limit - 5] + "-" + digest[:4]


def aliases_for(components, overrides=None, limit=9):
    overrides = overrides or {}
    # Hash lossy normalizations too, so sensor_1 and sensor1 remain distinguishable.
    normalized = {name: short(name, limit) for name in components}
    counts = Counter(normalized.values())
    result = {}
    for name, alias in normalized.items():
        if name in overrides:
            alias = overrides[name] if len(overrides[name]) <= limit else short(overrides[name], limit)
        elif counts[alias] > 1 or alias in overrides.values():
            alias = alias[:limit - 5] + "-" + hashlib.sha256(name.encode()).hexdigest()[:4]
        result[name] = alias
    return result


def prefix(config=None):
    value = os.environ.get("OSE_PROCESS_PREFIX", (config or {}).get("prefix", "ose"))
    if not isinstance(value, str) or not _PREFIX.fullmatch(value):
        raise ValueError("OSE_PROCESS_PREFIX must be 1..5 lowercase ASCII letters/digits, starting with a letter")
    return value


def name(role, plan=None, component=None):
    plan = plan or {}
    config = plan.get("process_names", {})
    result = prefix(config) + "-" + role
    if component is not None:
        aliases = aliases_for(plan.get("components", {component: {}}), config.get("components"),
                              15 - len(prefix(config)) - 3)
        if len(set(aliases.values())) != len(aliases):
            raise ValueError("process aliases collide with the selected OSE_PROCESS_PREFIX")
        result += "-" + aliases[component]
    if len(result) > 15 or not re.fullmatch(r"[a-z0-9-]+", result):
        raise ValueError("invalid process role or alias")
    return result


def set_thread_name(value):
    """Name the calling Linux task, never fail a workload for observability."""
    if not value or len(value) > 15 or not re.fullmatch(r"[a-z0-9-]+", value):
        return False
    if not sys.platform.startswith("linux"):
        return False
    try:
        Path("/proc/thread-self/comm").write_text(value, encoding="ascii")
        return True
    except OSError:
        return False


def identify(role, plan=None, component=None):
    global _label
    _label = name(role, plan, component)
    set_thread_name(_label)
    return _label


def initialize_component():
    """Called after INIT, before user callbacks, by the Python SDK."""
    global _label
    value = os.environ.get("OSE_PROCESS_NAME")
    if value and set_thread_name(value):
        _label = value


def component_environment(node):
    config = node.get("process_identity", {})
    component = config.get("component", "component")
    plan = {"process_names": {"prefix": config.get("prefix", "ose"),
                              "components": {component: config.get("alias", short(component, 9))}},
            "components": {component: node}}
    return {"OSE_PROCESS_NAME": name("c", plan, component),
            "OSE_PROCESS_PREFIX": prefix(plan["process_names"])}


def thread_name(role):
    """Capture the owner at thread creation; keep each worker's suffix unique."""
    owner = _label or os.environ.get("OSE_PROCESS_NAME") or prefix() + "-client"
    suffix = "-" + role + str(next(_threads))
    budget = 15 - len(suffix)
    if len(owner) > budget:
        instance, _, rest = owner.partition("-")
        owner = instance + "-" + short(rest, budget - len(instance) - 1)
    return owner + suffix


def initialize_thread(value):
    threading.current_thread().name = value
    set_thread_name(value)


def describe(role, plan, component=None, child=None):
    result = {"name": name(role, plan, component), "pid": os.getpid(),
              "ppid": os.getppid(), "host": socket.gethostname()}
    if component is not None:
        result["component"] = component
    if child is not None:
        result["child"] = {"name": name("c", plan, component), "pid": child.pid,
                           "ppid": os.getpid()}
    return result
