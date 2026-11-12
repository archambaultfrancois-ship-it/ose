"""Run disjoint CI integration groups; reject missing or repeated modules."""
import argparse
from collections import Counter
from pathlib import Path
import subprocess
import sys


GROUPS = {
    "runtime": (
        "test_application_directory", "test_component_recording", "test_containers",
        "test_event_control", "test_example_inputs", "test_input_callbacks",
        "test_input_timeouts", "test_logs", "test_managed_launch", "test_measurements",
        "test_native_containers", "test_ose", "test_parameter_sync",
        "test_parameters", "test_process_identity", "test_shutdown",
    ),
    "transport": (
        "test_backpressure", "test_broker", "test_c_inputs",
        "test_component_transport", "test_delivery_limits", "test_distributed",
        "test_distributed_inputs", "test_external_client", "test_external_native",
        "test_fanin", "test_input_process", "test_java_inputs", "test_ram",
        "test_rust_inputs",
    ),
}


def validate_groups(directory, groups=GROUPS):
    discovered = {
        str(path.relative_to(directory).with_suffix("")).replace("/", ".")
        for path in directory.rglob("test*.py")
    }
    counts = Counter(module for modules in groups.values() for module in modules)
    problems = []
    for label, modules in (
        ("unassigned", discovered - counts.keys()),
        ("missing", counts.keys() - discovered),
        ("duplicated", {module for module, count in counts.items() if count > 1}),
    ):
        if modules:
            problems.append(f"{label}: {', '.join(sorted(modules))}")
    if problems:
        raise ValueError("Invalid integration groups (" + "; ".join(problems) + ")")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("group", choices=GROUPS)
    parser.add_argument("--list", action="store_true", help="validate and list modules without running tests")
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    try:
        validate_groups(root / "tests/integration")
    except ValueError as error:
        parser.error(str(error))
    modules = ["tests.integration." + module for module in GROUPS[args.group]]
    if args.list:
        print("\n".join(modules))
        return 0
    return subprocess.call([sys.executable, "-m", "unittest", *modules, "-v"], cwd=root)


if __name__ == "__main__":
    sys.exit(main())
