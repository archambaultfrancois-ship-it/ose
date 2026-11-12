#!/usr/bin/env python3
"""Application workflow using the public ose CLI and reproducible inputs."""
import argparse
import base64
import asyncio
import json
import math
import os
from pathlib import Path
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]


def read(path):
    with Path(path).open() as stream:
        return json.load(stream)


def write(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, allow_nan=False) + "\n")


def framework(args):
    root = Path(args.framework_root or os.environ.get("FRAMEWORK_ROOT", ROOT.parent)).resolve()
    if not (root / "sdl/sdlc/generator.py").is_file() or not (root / "ose/src/ose/cli.py").is_file():
        raise ValueError("framework not found; use the parent SDL/OSE checkout or set FRAMEWORK_ROOT")
    return root


def environment(args):
    root = framework(args)
    return dict(os.environ, SDL_ROOT=str(root / "sdl"), PYTHONPATH=os.pathsep.join([str(root / "ose/src"), str(root / "build/python-deps")]),
                OSE_PROCESS_PREFIX=os.environ.get("OSE_PROCESS_PREFIX", "vw"))


def cli(args, *arguments, **kwargs):
    return subprocess.run([sys.executable, "-m", "ose.cli", *map(str, arguments)],
                          cwd=ROOT, env=environment(args), check=True, **kwargs)


def recording_options(args):
    options = []
    for clause in getattr(args, "record", None) or []:
        options += ["--record", clause]
    for name in ("record_file", "record_from", "record_until"):
        value = getattr(args, name, None)
        if value is not None:
            options += ["--" + name.replace("_", "-"), value]
    return options


def finite(value, minimum, maximum, name, integer=False):
    if type(value) not in (int, float) or not math.isfinite(value) or not minimum <= value <= maximum:
        raise ValueError(f"invalid {name}")
    if integer and (type(value) is not int):
        raise ValueError(f"{name} must be an integer")
    return value


def sensor_settings(values):
    expected = {"samples", "sample_rate_hz", "frequency_hz", "amplitude", "noise", "seed", "ramp_per_second"}
    if not isinstance(values, dict) or set(values) != expected:
        raise ValueError("sensor settings require exactly: " + ", ".join(sorted(expected)))
    finite(values["samples"], 1, 131072, "sample count", True)
    finite(values["seed"], 1, 2**32 - 1, "seed", True)
    rate = finite(values["sample_rate_hz"], 1, 1e9, "sample rate")
    finite(values["frequency_hz"], 0, rate / 2, "frequency")
    for name in ("amplitude", "noise", "ramp_per_second"):
        finite(values[name], 0, 1e6, name)
    return values


def sensor_settings_document(value, machine):
    path = f"machines/{machine}/sensor"
    if isinstance(value, dict) and path in value:
        if set(value) != {path}:
            raise ValueError(f"set-sensor requires an export containing only {path}")
        value = value[path]
    return sensor_settings(value)


def prepare(args):
    if not args.scenario.replace("-", "").isalnum():
        raise ValueError("invalid scenario name")
    model = read(ROOT / "model.json")
    deployment = read(ROOT / "deployment.json")
    if args.profile == "local":
        deployment.pop("execution", None)
        deployment.pop("subscriptions", None)
        for component in deployment["components"].values():
            component.pop("placement", None)
    elif not (args.task == "distributed-local" and args.params_sync is not None):
        # Development tokens are only used locally and in the loopback sync demo.
        for client in deployment.get("parameter_clients", {}).values():
            client.pop("token", None)
    machines = read(ROOT / "config/machines.json")
    thresholds = read(ROOT / "config/thresholds.json")
    scenario = read(ROOT / "scenarios" / (args.scenario + ".json"))
    if any(document.get("version") != 1 for document in (machines, thresholds, scenario)):
        raise ValueError("application configurations require version 1")
    if any(set(document) != {"1", "2"} for document in
           (machines["machines"], thresholds["rms"], scenario["ramp_per_second"])):
        raise ValueError("configuration must describe machines 1 and 2")
    model["schemas"] = str(ROOT / "schemas")
    # Prepared models move under build/; preserve explicit per-port/block schemas.
    descriptors = list(model.get("shared_parameters", {}).values()) + list(model.get("external_topics", {}).values())
    for node in model["components"].values():
        for direction in ("inputs", "outputs", "recording_types"):
            descriptors.extend(node.get(direction, {}).values())
    for desc in descriptors:
        if "schema" in desc:
            desc["schema"] = str((ROOT / desc["schema"]).resolve())
    for identity, machine in machines["machines"].items():
        number = int(identity)
        values = sensor_settings({**{key: value for key, value in machine.items() if key != "name"},
                                  "ramp_per_second": scenario["ramp_per_second"][identity]})
        threshold = finite(thresholds["rms"][identity], 1e-12, 1e12, "threshold")
        model["shared_parameters"][f"machines/{number}/sensor"]["initial"] = values
        model["shared_parameters"][f"machines/{number}/detection"]["initial"] = {"rms_threshold": threshold}
        deployment["components"][f"sensor_{number}"]["env"] = {"VW_MACHINE": str(number)}
    for component in deployment["components"].values():
        component["container"]["handler"] = str(ROOT / component["container"]["handler"])
        component["cwd"] = str(ROOT)
    if args.profile == "cluster" and args.routing == "broker":
        deployment["subscriptions"] = {key: {"routing": "broker", "transport": "tcp"}
                                       for key in deployment.get("subscriptions", {})}
    destination = ROOT / "build/prepared"
    write(destination / "model.json", model)
    write(destination / "deployment.json", deployment)
    write(destination / "inputs.json", {"scenario": args.scenario, "profile": args.profile, "routing": args.routing})
    return destination


def compile_app(args, *, validate=False):
    prepared = prepare(args)
    options = ["validate" if validate else "build", "--model", prepared / "model.json",
               "--deployment", prepared / "deployment.json", "--build-dir", prepared.parent]
    if not validate and args.component:
        options += ["--component", args.component]
    cli(args, *options)


def distributed_local(args):
    """Prepare the application; OSE owns cluster startup, supervision and shutdown."""
    args.profile = "cluster"
    compile_app(args)
    from ose.execution.local_cluster import run
    plan = ROOT / "build/plan.json"
    sync = None
    if args.params_sync is not None:
        sync = dict(directory=str(Path(args.params_sync).resolve()),
                    token=read(plan)["parameter_clients"]["operator"].get("token"))

    async def demonstration(cluster):
        if args.fail_node:
            if args.fail_node not in cluster.plan["components"]:
                raise ValueError("unknown fail-node")
            await asyncio.sleep(args.duration / 2)
            await cluster.restart(args.fail_node)
            await asyncio.sleep(args.duration / 2)
        else:
            await asyncio.sleep(args.duration)
    previous = dict(os.environ)
    try:
        os.environ.update(environment(args))
        asyncio.run(run(plan, args.duration, port=args.port or 0, params_sync=sync,
                        exercise=demonstration))
    finally:
        os.environ.clear()
        os.environ.update(previous)
        observer = ROOT / "build/distributed-logs/observer.log"
        if observer.exists():
            print(observer.read_text(), file=sys.stderr, end="")


def certs(args):
    directory = ROOT / "build/pki"
    directory.mkdir(parents=True, exist_ok=True)
    os.chmod(directory, 0o700)
    def openssl(*options):
        subprocess.run(["openssl", *map(str, options)], check=True, capture_output=True)
    ca_key, ca_cert = directory / "ca.key", directory / "ca.crt"
    if not ca_key.exists():
        openssl("req", "-x509", "-newkey", "rsa:2048", "-nodes", "-days", "365", "-subj", "/CN=vibration-watch-dev-ca",
                "-addext", "basicConstraints=critical,CA:TRUE", "-addext", "keyUsage=critical,keyCertSign,cRLSign",
                "-keyout", ca_key, "-out", ca_cert)
        os.chmod(ca_key, 0o600)
    extensions = directory / "leaf.ext"
    extensions.write_text("basicConstraints=critical,CA:FALSE\nkeyUsage=critical,digitalSignature,keyEncipherment\nextendedKeyUsage=serverAuth,clientAuth\nsubjectKeyIdentifier=hash\nauthorityKeyIdentifier=keyid,issuer\n")
    items = []
    for name in ["coordinator", *read(ROOT / "model.json")["components"],
                 *read(ROOT / "deployment.json").get("parameter_clients", {})]:
        key, csr, cert = (directory / (name + suffix) for suffix in (".key", ".csr", ".crt"))
        if not cert.exists():
            openssl("req", "-newkey", "rsa:2048", "-nodes", "-subj", "/CN=" + name, "-keyout", key, "-out", csr)
            os.chmod(key, 0o600)
            openssl("x509", "-req", "-in", csr, "-CA", ca_cert, "-CAkey", ca_key, "-CAcreateserial", "-days", "365", "-extfile", extensions, "-out", cert)
        items.append({"apiVersion": "v1", "kind": "Secret", "type": "Opaque",
            "metadata": {"name": "ose-" + name.replace("_", "-") + "-tls", "namespace": args.namespace},
            "data": {field: base64.b64encode(path.read_bytes()).decode() for field, path in
                     (("ca.crt", ca_cert), ("tls.crt", cert), ("tls.key", key))}})
    destination = ROOT / "build/tls-secrets.json"
    write(destination, {"apiVersion": "v1", "kind": "List", "items": items})
    os.chmod(destination, 0o600)
    print(destination)


def set_parameters(args):
    if args.machine is None:
        raise ValueError(f"{args.task} requires --machine")
    if args.task == "set-threshold":
        if args.rms_threshold is None:
            raise ValueError("set-threshold requires --rms-threshold")
        value = {"rms_threshold": finite(args.rms_threshold, 1e-12, 1e12, "threshold")}
        block = "detection"
    else:
        if args.file is None:
            raise ValueError("set-sensor requires --file with a complete sensor settings block")
        value = sensor_settings_document(read(args.file), args.machine)
        block = "sensor"
    with tempfile.TemporaryDirectory(prefix="vibration-parameters-") as temporary:
        source = Path(temporary) / "settings.json"
        write(source, value)
        options = ["app", "--root", ROOT, "parameter", "set", f"machines/{args.machine}/{block}",
                   "--client", "operator", "--file", source, "--plan", args.plan,
                   "--endpoint", args.endpoint]
        for name in ("host", "port", "tls_ca", "tls_cert", "tls_key"):
            if getattr(args, name) is not None:
                options += ["--" + name.replace("_", "-"), getattr(args, name)]
        if args.distributed:
            options += ["--distributed"]
        if args.insecure:
            options += ["--insecure"]
        cli(args, *options)


def images(args):
    if not args.registry:
        raise ValueError("pass --registry REGISTRY/PROJECT (for example registry.lan/vibration-watch)")
    root = framework(args)
    if root != ROOT.parent:
        raise ValueError("images use the parent SDL/OSE checkout; omit FRAMEWORK_ROOT")
    revision = subprocess.check_output(["git", "-C", str(root), "rev-parse", "HEAD"], text=True).strip()[:12]
    if subprocess.check_output(["git", "-C", str(root), "status", "--porcelain"], text=True).strip():
        raise ValueError("repository must be clean before building release images")
    source_hash = sources_digest()
    published = {}
    for name in read(ROOT / "model.json")["components"]:
        image = f"{args.registry.rstrip('/')}/{name.replace('_', '-')}:{revision}-{source_hash[:12]}-{args.scenario}-{args.routing}"
        subprocess.run(["docker", "build", "-f", "vibration-watch/packaging/Dockerfile", "--build-arg", "COMPONENT=" + name,
                        "--build-arg", "SCENARIO=" + args.scenario, "--build-arg", "ROUTING=" + args.routing,
                        "-t", image, "."], cwd=root, check=True)
        if args.push:
            subprocess.run(["docker", "push", image], check=True)
            refs = json.loads(subprocess.check_output(["docker", "image", "inspect", image, "--format", "{{json .RepoDigests}}"], text=True))
            published[name] = next(ref for ref in refs if ref.split("@")[0] == image.rsplit(":", 1)[0])
    if args.push:
        published["coordinator"] = published["observer"]
        write(ROOT / "build/images.json", published)
        write(ROOT / "build/release.json", {"scenario": args.scenario, "routing": args.routing,
              "framework_revision": revision, "sources_sha256": source_hash})


def sources_digest():
    import hashlib
    content = hashlib.sha256()
    for folder in ("schemas", "components", "config", "scenarios", "tools", "packaging"):
        for path in sorted((ROOT / folder).rglob("*")):
            if path.is_file() and "__pycache__" not in path.parts:
                content.update(path.relative_to(ROOT).as_posix().encode() + b"\0" + path.read_bytes())
    for path in sorted(ROOT.glob("*.json")):
        content.update(path.name.encode() + b"\0" + path.read_bytes())
    return content.hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("task", choices=("prepare", "validate", "build", "run", "simulate", "replay", "replay-build", "recording-deps", "distributed-local", "set-threshold", "set-sensor", "certs", "images", "render"))
    parser.add_argument("--profile", choices=("local", "cluster"), default="local")
    parser.add_argument("--scenario", default="increasing-vibration")
    parser.add_argument("--routing", choices=("mixed", "broker"), default="mixed")
    parser.add_argument("--duration", type=float, default=3)
    parser.add_argument("--params-sync", nargs="?", const="parameters", metavar="DIR",
                        help="start parameter directory sync for run/distributed-local (default directory: parameters)")
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--loops", type=int, default=1, help="total replay passes; 0 repeats until the duration limit")
    parser.add_argument("--recording", help="PCAPNG/MCAP signal capture for replay")
    parser.add_argument("--record", action="append", metavar="TOPIC_GLOB[, FILTER]",
                        help="capture matching points during run, simulate or replay; repeat to union selections")
    parser.add_argument("--record-file", metavar="FILE",
                        help="capture destination .pcapng or .mcap (default: capture.pcapng)")
    parser.add_argument("--record-from", metavar="TIME", help="inclusive capture start position")
    parser.add_argument("--record-until", metavar="TIME", help="exclusive capture end position")
    parser.add_argument("--start-ns", type=int)
    parser.add_argument("--end-ns", type=int)
    parser.add_argument("--file", help="bare sensor settings or single path-keyed sensor export for set-sensor")
    parser.add_argument("--machine", type=int, choices=(1, 2))
    parser.add_argument("--rms-threshold", type=float, help="positive RMS alarm threshold in m/s^2")
    parser.add_argument("--plan", default="build/plan.json")
    parser.add_argument("--endpoint", default="build/endpoint.json")
    parser.add_argument("--host")
    parser.add_argument("--port", type=int)
    parser.add_argument("--distributed", action="store_true")
    parser.add_argument("--insecure", action="store_true")
    parser.add_argument("--tls-ca")
    parser.add_argument("--tls-cert")
    parser.add_argument("--tls-key")
    parser.add_argument("--component")
    parser.add_argument("--fail-node")
    parser.add_argument("--framework-root")
    parser.add_argument("--registry")
    parser.add_argument("--push", action="store_true")
    parser.add_argument("--namespace", default="vibration-watch")
    parser.add_argument("--image-pull-secret", action="append", default=[])
    args = parser.parse_args()
    try:
        if sys.version_info < (3, 10):
            raise ValueError(
                f"Python 3.10+ is required; {sys.executable} is Python {sys.version.split()[0]}. "
                "Use a Python 3.10+ interpreter from PATH.")
        finite(args.duration, 1e-6, 1e9, "duration")
        if recording_options(args) and args.task not in ("run", "simulate", "replay"):
            raise ValueError("recording options are supported for run, simulate and replay only")
        if args.task in ('run', 'distributed-local', 'simulate', 'replay'):
            sys.path[:0] = [str(framework(args) / 'ose/src'), str(framework(args) / 'build/python-deps')]
            from ose.execution.identity import identify
            identify('launch', {'process_names': {'prefix': 'vw'}})
        finite(args.workers, 1, 256, "workers", True)
        if args.loops < 0:
            raise ValueError("loops must be nonnegative")
        if args.params_sync is not None:
            if args.task not in ("run", "distributed-local"):
                raise ValueError("--params-sync is supported for run and distributed-local only")
            if not args.params_sync:
                raise ValueError("--params-sync directory must not be empty")
            from ose.execution.launch import require_sync
            require_sync(args.params_sync)
        if args.task == "recording-deps":
            subprocess.run(["make", "-C", str(framework(args) / "sdl"),
                            "PYTHON=" + sys.executable, "recording-deps"], check=True)
        elif args.task == "prepare":
            print(prepare(args))
        elif args.task in ("validate", "build", "replay-build"):
            compile_app(args, validate=args.task == "validate")
        elif args.task in ("run", "simulate"):
            args.profile = "local"
            compile_app(args)
            options = ["run", "--duration", args.duration, *recording_options(args)]
            if args.task == "simulate":
                options += ["--mode", "simulated", "--workers", args.workers, "--trace", ROOT / "build/simulation.jsonl"]
            if args.params_sync is not None:
                options += ["--params-sync", args.params_sync, "--params-client", "operator"]
                if args.port is not None:
                    options += ["--port", args.port]
                # Replace the launcher so terminal signals reach the OSE supervisor.
                env = environment(args)
                env["OSE_PARAMETER_TOKEN"] = read(ROOT / "build/plan.json")["parameter_clients"]["operator"]["token"]
                os.chdir(ROOT)
                os.execve(sys.executable, [sys.executable, "-m", "ose.cli", *map(str, options)], env)
            else:
                cli(args, *options)
        elif args.task == "replay":
            if not args.recording:
                raise ValueError("replay requires --recording FILE")
            args.profile = "local"
            compile_app(args)
            (ROOT / 'build/replay').mkdir(parents=True, exist_ok=True)
            options = ["replay", args.recording, "--mode", "simulated", "--plan", ROOT / "build/plan.json",
                       "--duration", args.duration, "--workers", args.workers, "--loops", args.loops,
                       "--trace", ROOT / "build/replay/trace.jsonl"]
            for number in (1, 2):
                options += ["--map", f"sensor_{number}.signal=sensor_{number}.signal",
                            "--disable-component", f"sensor_{number}"]
            for name in ("start_ns", "end_ns"):
                if getattr(args, name) is not None:
                    options += ["--" + name.replace("_", "-"), getattr(args, name)]
            options += recording_options(args)
            cli(args, *options)
        elif args.task == "distributed-local":
            distributed_local(args)
        elif args.task in ("set-threshold", "set-sensor"):
            set_parameters(args)
        elif args.task == "certs":
            certs(args)
        elif args.task == "images":
            images(args)
        elif args.task == "render":
            release = read(ROOT / "build/release.json")
            revision = subprocess.check_output(["git", "-C", str(framework(args)), "rev-parse", "HEAD"], text=True).strip()[:12]
            if release != {"scenario": args.scenario, "routing": args.routing, "framework_revision": revision,
                           "sources_sha256": sources_digest()}:
                raise ValueError("release images do not match these sources/configuration; rebuild and publish images")
            args.profile = "cluster"
            # Resolve the entire graph while building only the Python observer.
            args.component = "observer"
            compile_app(args)
            cli(args, "export-kubernetes", "--images", ROOT / "build/images.json", "--namespace", args.namespace,
                "--output", ROOT / "build/kubernetes.json",
                *(option for name in args.image_pull_secret for option in ("--image-pull-secret", name)))
            write(ROOT / "build/namespace.json", {"apiVersion": "v1", "kind": "Namespace", "metadata": {"name": args.namespace}})
    except KeyboardInterrupt:
        return 130
    except (ValueError, KeyError, OSError, RuntimeError, subprocess.CalledProcessError) as error:
        print(f"vibration-watch: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
