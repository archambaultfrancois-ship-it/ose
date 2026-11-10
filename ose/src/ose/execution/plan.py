"""Version gates shared by compiled-plan readers and execution entry points."""
import json
from pathlib import Path


def require_v2(plan):
    if not isinstance(plan, dict) or type(plan.get("version")) is not int or plan["version"] != 2:
        raise ValueError("execution requires a version 2 application plan; rebuild the model")
    components = plan.get("components")
    if not isinstance(components, dict) or not components:
        raise ValueError("application plan components must be a nonempty object")
    for name, node in components.items():
        if not isinstance(node, dict) or type(node.get("execution_model")) is not int or node["execution_model"] != 2:
            raise ValueError(f"component {name}: execution requires model 2; rebuild the container")
    return plan


def read_plan(path):
    return require_v2(json.loads(Path(path).read_text()))
