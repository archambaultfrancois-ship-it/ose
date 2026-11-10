"""Structural graph validation, independent of SDL and execution."""
import re
from ose.modeling.model import fields
from ose.delivery.policies import SubscriptionPolicy, reception_policies


def validate_graph(model, deployment):
    fields(model, ("version", "schemas", "components", "subscriptions", "external_topics", "shared_parameters"), ("version", "schemas", "components", "subscriptions"))
    fields(deployment, ("version", "components", "subscriptions", "max_frame", "max_datagram", "broker", "execution", "parameter_clients", "process_names"), ("version", "components"))
    if type(model["version"]) is not int or model["version"] != 2:
        raise ValueError("expected a version 2 application model")
    if type(deployment["version"]) is not int or deployment["version"] != 1:
        raise ValueError("expected a version 1 deployment")
    if deployment.get("execution", "local") not in ("local", "distributed"):
        raise ValueError("execution must be local or distributed")
    distributed = deployment.get("execution") == "distributed"
    if not isinstance(model["schemas"], str) or not model["schemas"]:
        raise ValueError("schemas must be a nonempty path")
    if not isinstance(deployment["components"], dict) or not isinstance(deployment.get("subscriptions", {}), dict):
        raise ValueError("deployment components and subscriptions must be objects")
    broker = deployment.get("broker", {})
    fields(broker, ("host", "external"))
    if broker.get("host", "127.0.0.1") not in ("127.0.0.1", "0.0.0.0"):
        raise ValueError("local deployment broker host must be 127.0.0.1 or 0.0.0.0")
    from ose.config import EXTERNAL_LIMITS
    limits = broker.get("external", {})
    fields(limits, tuple(EXTERNAL_LIMITS))
    for key, value in limits.items():
        if key == "enabled":
            if type(value) is not bool:
                raise ValueError("external enabled must be boolean")
        elif type(value) is not int or value <= 0:
            raise ValueError(f"{key} must be positive")
    external = model.get("external_topics", {})
    if not isinstance(external, dict):
        raise ValueError("external_topics must be an object")
    for topic, desc in external.items():
        if not isinstance(topic, str) or not 0 < len(topic) <= 200:
            raise ValueError("invalid external topic")
        fields(desc, ("root", "schema"), ("root",))
        if not isinstance(desc["root"], str) or not desc["root"]:
            raise ValueError("invalid external topic root")
        if "schema" in desc and (not isinstance(desc["schema"], str) or not desc["schema"]):
            raise ValueError("invalid external topic schema")
    components = model["components"]
    if not isinstance(components, dict) or not components:
        raise ValueError("components must be a nonempty object")
    if any(topic.startswith(name + ".record.") for topic in external for name in components):
        raise ValueError("external topic uses a component's reserved recording namespace")
    if set(deployment["components"]) != set(components):
        raise ValueError("deployment must cover exactly the model components")
    from ose.execution.identity import validate as validate_process_names
    validate_process_names(deployment.get("process_names", {}), components)
    from ose.parameters.model import validate_parameters
    validate_parameters(model, deployment)
    for name, node in components.items():
        if not name or "." in name:
            raise ValueError("component names must be nonempty and contain no dots")
        from ose.modeling.activation import component_inputs
        component_inputs(node)
        if not isinstance(node.get("recording_types", {}), dict):
            raise ValueError("recording_types must map aliases to SDL type descriptions")
        for desc in node.get("recording_types", {}).values():
            fields(desc, ("root", "schema"), ("root",))
            if not isinstance(desc["root"], str) or not desc["root"]:
                raise ValueError("invalid recording type root")
            if "schema" in desc and (not isinstance(desc["schema"], str) or not desc["schema"]):
                raise ValueError("invalid recording type schema")
        if not isinstance(node.get("parameters", {}), dict):
            raise ValueError("parameters must be an object")
        execution = deployment["components"][name]
        fields(execution, ("command", "container", "build", "cwd", "env", "placement"))
        if ("command" in execution) == ("container" in execution):
            raise ValueError("each component needs exactly one command or container")
        if "container" in execution:
            container = execution["container"]
            fields(container, ("language", "handler", "class", "unit"), ("language", "handler"))
            language = container["language"]
            if language not in ("python", "c", "rust", "java", "ada"):
                raise ValueError("container language must be python, c, rust, java or ada")
            from ose.modeling.managed_containers import validate_names
            validate_names(language, name, node, container)
            if language in ("rust", "c"):
                methods = [f"publish_{port}{suffix}" for port in node["outputs"] for suffix in ("", "_with_id")]
                if len(methods) != len(set(methods)):
                    raise ValueError(f"{language.title()} output names conflict with generated publication ID methods")
            if not isinstance(container["handler"], str) or not container["handler"]:
                raise ValueError("container handler must be a nonempty file path")
            if not name.isidentifier():
                raise ValueError("generated component names must be identifiers")
            native_keywords = set(("auto break case char const continue default do double else enum extern float for goto if inline int long register restrict return short signed sizeof static struct switch typedef union unsigned void volatile while _Bool _Complex _Imaginary bool true false complex imaginary I NULL offsetof as crate fn impl in let loop match mod move mut pub ref self Self super trait type unsafe use where async await dyn abstract become box final macro override priv typeof unsized virtual yield try").split())
            if language != "python" and not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]*", name):
                raise ValueError("generated native component names must be ASCII identifiers")
            for direction in ("inputs", "outputs"):
                for port in node[direction]:
                    if not port.isidentifier():
                        raise ValueError("generated port names must be identifiers")
                    if language in ("c", "rust") and (not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]*", port) or port in native_keywords):
                        raise ValueError("generated native port names must be portable, non-reserved ASCII identifiers")
                    if direction == "inputs" and port in ("start", "stop", "tick"):
                        raise ValueError(f"input {port!r} conflicts with a reserved callback")
        placement = execution.get("placement", "local")
        if placement != "local":
            if not distributed:
                raise ValueError("remote placement requires execution=distributed")
            fields(placement, ("node_selector", "resources"))
            selector = placement.get("node_selector", {})
            if not isinstance(selector, dict) or not all(isinstance(k, str) and isinstance(v, str) for k, v in selector.items()):
                raise ValueError("node_selector must map strings to strings")
            resources = placement.get("resources", {})
            fields(resources, ("requests", "limits"))
            for quantities in resources.values():
                if not isinstance(quantities, dict) or not all(isinstance(k, str) and isinstance(v, str) and v for k, v in quantities.items()):
                    raise ValueError("resource quantities must be nonempty strings")
        if not isinstance(execution.get("build", []), list):
            raise ValueError("build must be a list of argv commands")
        if not isinstance(execution.get("cwd", "."), str):
            raise ValueError("cwd must be a path string")
        env = execution.get("env", {})
        if not isinstance(env, dict) or not all(isinstance(k, str) and isinstance(v, str) for k, v in env.items()):
            raise ValueError("env must map strings to strings")
        for command in ([execution["command"]] if "command" in execution else []) + execution.get("build", []):
            if not isinstance(command, list) or not command or not all(isinstance(x, str) for x in command):
                raise ValueError("commands must be nonempty argv lists")
    if set(external) & {f"{name}.{port}" for name, node in components.items() for port in node["outputs"]}:
        raise ValueError("external topic conflicts with an internal output")
    ids, subscriptions = set(), []
    if not isinstance(model["subscriptions"], list):
        raise ValueError("subscriptions must be a list")
    for sub in model["subscriptions"]:
        fields(sub, ("id", "from", "from_topic", "to", "qos"), ("id", "to"))
        if ("from" in sub) == ("from_topic" in sub):
            raise ValueError("subscription needs exactly one from or from_topic")
        if not isinstance(sub["id"], str) or not sub["id"] or sub["id"] in ids:
            raise ValueError("subscription IDs must be unique, nonempty strings")
        ids.add(sub["id"])
        if "from" in sub:
            endpoint(sub["from"], components, "outputs")
        elif sub["from_topic"] not in external:
            raise ValueError("unknown external topic")
        endpoint(sub["to"], components, "inputs")
        binding = deployment.get("subscriptions", {}).get(sub["id"], {})
        fields(binding, ("transport", "routing"))
        policy = SubscriptionPolicy.resolve(sub.get("qos"), binding.get("transport", "tcp"))
        subscriptions.append({"to": sub["to"], "policy": policy.to_dict()})
    if set(deployment.get("subscriptions", {})) - ids:
        raise ValueError("unknown subscription in deployment")
    reception_policies(subscriptions)
    from ose.modeling.activation import activation_graph
    activation_graph(model)


def endpoint(value, components, direction):
    if not isinstance(value, str) or value.count(".") != 1:
        raise ValueError(f"invalid endpoint: {value}")
    node, port = value.split(".")
    if node not in components or port not in components[node][direction]:
        raise ValueError(f"unknown {direction} endpoint: {value}")
    return node, port
