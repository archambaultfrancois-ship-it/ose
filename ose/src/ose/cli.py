"""Standalone project command line."""
import argparse
import asyncio
import json
import math
import os
from pathlib import Path
import signal
import subprocess
import sys
from ose.modeling.compiler import build, validate
from ose.recording import DEFAULT_LABEL, session_label


def _command(commands, name, summary, description, examples):
    return commands.add_parser(
        name, help=summary, description=description,
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="Examples:\n" + "\n".join("  " + example for example in examples))


def create_parser():
    parser = argparse.ArgumentParser(
        prog="ose", formatter_class=argparse.RawDescriptionHelpFormatter,
        description="Build, run and observe SDL applications with OSE.",
        epilog="""Application workflows:
  ose app run
  ose app record
  ose app parameter get
  ose app --root /path/to/application env

Explicit model/plan workflow:
  ose validate --model model.json --deployment deployment.json
  ose build --model model.json --deployment deployment.json
  ose run

From another terminal while the application runs:
  ose top
  ose logs -f
  ose measure 'analyzer_*.vibration.rms' --watch
  ose record

Use ose COMMAND --help for options and examples.
Use ose parameter ACTION --help for parameter operations.
Local observation commands use build/endpoint.json by default.
For distributed execution, start serve and worker instead of run.""")
    commands = parser.add_subparsers(dest="action", required=True, title="commands", metavar="COMMAND")
    from ose.logs import add_parser as log_parser
    log_parser(commands)
    for name in ("validate", "build"):
        summary = "validate the model and deployment" if name == "validate" else "generate a plan and compile component executables"
        command = _command(commands, name, summary,
            "Validate the SDL model and its deployment without compiling components." if name == "validate" else
            "Validate the model and deployment, write plan.json in the build directory and compile components.\n"
            "Use --component to rebuild one component while retaining the complete plan.",
            [f"ose {name} --model model.json --deployment deployment.json"])
        command.add_argument("--model", required=True, help="application model JSON file")
        command.add_argument("--deployment", required=True, help="deployment JSON file")
        command.add_argument("--sdl-root", default=os.environ.get("SDL_ROOT", str(Path(__file__).resolve().parents[3] / "sdl")),
                             help="SDL checkout (default: SDL_ROOT or the sibling sdl directory of the OSE source checkout)")
        command.add_argument("--build-dir", default="build", help="build output directory (default: build; build command only)")
        if name == "build":
            command.add_argument("--component", help="compile only this component, retaining the complete plan")
    for name in ("serve", "worker", "cluster-status"):
        summaries = {"serve": "start the distributed coordinator",
                     "worker": "run one component of a distributed plan",
                     "cluster-status": "print distributed cluster status as JSON"}
        descriptions = {
            "serve": "Coordinate workers, routing and leases for a distributed plan.\n"
                     "Use mutual TLS in deployment, or --insecure on loopback for development.",
            "worker": "Launch the named component and connect it to the distributed coordinator.\n"
                      "Start one worker per component; advertise an address reachable by peers.",
            "cluster-status": "Query the coordinator for the current workers and distributed application status."}
        examples = {"serve": ["ose serve --insecure"],
                    "worker": ["ose worker --node analyzer --insecure"],
                    "cluster-status": ["ose cluster-status --insecure"]}
        command = _command(commands, name, summaries[name], descriptions[name], examples[name])
        command.add_argument("--host", default="127.0.0.1", help="listen address for serve; coordinator address otherwise (default: 127.0.0.1)")
        command.add_argument("--port", type=int, default=7000, help="coordinator TCP port (default: 7000)")
        command.add_argument("--tls-ca", help="trusted CA certificate file for mutual TLS")
        command.add_argument("--tls-cert", help="certificate file; identity is coordinator for serve, node name for worker")
        command.add_argument("--tls-key", help="private key file matching --tls-cert")
        command.add_argument("--insecure", action="store_true", help="explicit loopback-only development mode")
        if name != "cluster-status":
            command.add_argument("--plan", default="build/plan.json", help="compiled deployment plan (default: build/plan.json)")
        if name == "serve":
            command.add_argument("--lease-seconds", type=float, default=6, help="worker lease duration in seconds (default: 6)")
            command.add_argument("--kubernetes-lease", help="pre-created Kubernetes Lease for coordinator fencing")
            command.add_argument("--namespace", default="default", help="namespace of --kubernetes-lease (default: default)")
        if name == "worker":
            command.add_argument("--node", required=True, help="component name in the plan")
            command.add_argument("--listen-host", default="127.0.0.1", help="worker listen address (default: 127.0.0.1)")
            command.add_argument("--listen-port", type=int, default=0, help="worker TCP port (default: 0, automatically assigned)")
            command.add_argument("--advertise-host", default="127.0.0.1", help="worker address announced to peers (default: 127.0.0.1)")
    command = _command(commands, "export-kubernetes", "generate Kubernetes manifests for a distributed plan",
        "Write Kubernetes manifests using digest-pinned images for the coordinator and every component.\n"
        "This command generates files; apply them separately to deploy.",
        ["ose export-kubernetes --images images.json --output manifests.json"])
    command.add_argument("--plan", default="build/plan.json", help="compiled distributed plan (default: build/plan.json)")
    command.add_argument("--images", required=True, help="JSON file mapping coordinator and component names to image digests")
    command.add_argument("--namespace", default="ose-app", help="generated application namespace (default: ose-app)")
    command.add_argument("--image-pull-secret", action="append", default=[], help="existing image pull secret name; repeat for multiple secrets")
    command.add_argument("--output", required=True, help="output Kubernetes manifest JSON file")
    command = _command(commands, "run", "run a local application in real time or simulated time",
        "Execute a compiled plan; real-time mode starts the local broker and component processes.\n"
        "Real-time execution runs until interrupted unless --duration is provided.\n"
        "Simulation requires --duration in virtual seconds. Distributed plans use serve and worker.",
        ["ose run", "ose run --duration 30",
         "ose run --mode simulated --duration 60 --workers 4 --trace trace.jsonl"])
    command.add_argument("--plan", default="build/plan.json", help="compiled deployment plan (default: build/plan.json)")
    command.add_argument("--duration", type=float, help="positive execution duration in seconds; required in simulated mode")
    command.add_argument("--working-directory", help="component working directory (default: plan settings; app run: launch directory)")
    command.add_argument("--port", type=int, help="local broker TCP port; real-time only (default: automatically assigned)")
    command.add_argument("--mode", choices=("realtime", "simulated"), default="realtime", help="execution clock (default: realtime)")
    command.add_argument("--workers", type=int, help="simulation worker count (default: 1; simulated mode only)")
    command.add_argument("--scenario", help="external-input scenario JSON file (simulated mode only)")
    command.add_argument("--trace", help="output deterministic trace file (simulated mode only)")
    command.add_argument("--max-tick-output-bytes", type=int, help="output byte limit per component tick (default: 16777216; simulated mode only)")
    command = _command(commands, "replay", "replay a recording into simulated or live external inputs",
        "Map recorded topics to external input topics declared in the plan.\n"
        "Simulation starts an application and requires --duration. Real-time replay publishes\n"
        "into an already running application and supports playback controls.\n"
        "Connection options, --speed, --interactive and --command require --mode realtime.",
        ["ose replay capture.mcap --map recorded.topic=external.topic --duration 30",
         "ose replay capture.mcap --map recorded.topic=external.topic --mode realtime --interactive",
         "ose replay capture.mcap --map recorded.topic=external.topic --duration 30 --schema-policy compatible"])
    command.add_argument("recording", help="SDL PCAPNG or MCAP capture")
    command.add_argument("--plan", default="build/plan.json", help="target deployment plan (default: build/plan.json)")
    command.add_argument("--map", dest="mappings", action="append", required=True,
                         metavar="RECORDED_TOPIC=EXTERNAL_TOPIC", help="select and map a topic; repeat for each input")
    command.add_argument("--mode", choices=("simulated", "realtime"), default="simulated", help="replay destination and clock (default: simulated)")
    command.add_argument("--schema-policy", choices=("exact", "compatible"), default="exact",
                         help="recorded schema policy (default: exact; compatible adapts messages to the target contract)")
    command.add_argument("--loops", type=int, default=1, help="total playback passes (default: 1; 0: infinite)")
    command.add_argument("--duration", type=float, help="recorded-time horizon in seconds; required for simulation")
    command.add_argument("--workers", type=int, help="simulation worker count (default: 1; simulated mode only)")
    command.add_argument("--start-ns", type=int, help="inclusive recorded log timestamp in nanoseconds")
    command.add_argument("--end-ns", type=int, help="exclusive recorded log timestamp in nanoseconds")
    command.add_argument("--trace", help="output deterministic trace file (simulated mode only)")
    command.add_argument("--speed", type=float, help="real-time playback rate (default: 1; 0: maximum speed)")
    command.add_argument("--interactive", action="store_true", help="live speed, pause, resume and stop controls")
    command.add_argument("--command", action="append",
                         help="execute a live replay command in order; repeat to script a session (wait SECONDS supported)")
    command.add_argument("--endpoint", help="live endpoint JSON (default: local plan's endpoint.json); exclusive with --host/--port")
    command.add_argument("--host", help="target broker or coordinator host")
    command.add_argument("--port", type=int, help="target broker or coordinator TCP port")
    command.add_argument("--distributed", action="store_true", help="publish through the distributed coordinator")
    command.add_argument("--tls-ca", help="trusted CA certificate file for distributed mutual TLS")
    command.add_argument("--tls-cert", help="distributed client certificate with common name replay")
    command.add_argument("--tls-key", help="private key file matching --tls-cert")
    command.add_argument("--insecure", action="store_true", help="distributed loopback-only development mode")
    command = _command(commands, "inspect", "print local application status as JSON",
        "Query the running local broker, or read its last saved status with --last.\n"
        "For a distributed application, use cluster-status or top --distributed.",
        ["ose inspect", "ose inspect --last"])
    command.add_argument("--endpoint", default="build/endpoint.json", help="local endpoint JSON (default: build/endpoint.json)")
    command.add_argument("--last", action="store_true", help="read last-status.json beside the endpoint without contacting the broker")
    command = _command(commands, "top", "show live component, queue and delivery diagnostics",
        "Monitor execution rates, queues and delivery diagnostics locally or through a coordinator.\n"
        "Text output is interactive on a terminal: arrows navigate, Enter opens details,\n"
        "Space pauses the display and q quits. Use JSONL for scripts and --count to stop.",
        ["ose top", "ose top --format jsonl --count 5",
         "ose top --distributed --host 127.0.0.1 --port 7000 --insecure"])
    command.add_argument("--endpoint", help="local endpoint JSON (default: build/endpoint.json); exclusive with --host/--port")
    command.add_argument("--host", help="target broker or coordinator host")
    command.add_argument("--port", type=int, help="target broker or coordinator TCP port")
    command.add_argument("--distributed", action="store_true", help="query a distributed coordinator instead of the local broker")
    command.add_argument("--tls-ca", help="trusted CA certificate file for distributed mutual TLS")
    command.add_argument("--tls-cert", help="distributed client certificate file")
    command.add_argument("--tls-key", help="private key file matching --tls-cert")
    command.add_argument("--insecure", action="store_true", help="distributed loopback-only development mode")
    command.add_argument("--interval", type=float, default=1, help="refresh interval in seconds (default: 1; minimum: 0.1)")
    command.add_argument("--window", type=float, default=5, help="rate window in seconds (default: 5; range: 0.1 to 60)")
    command.add_argument("--count", type=int, help="exit after N samples")
    command.add_argument("--format", choices=("text", "jsonl"), default="text", help="display format (default: text); jsonl emits one JSON object per sample")
    command.add_argument("--color", choices=("auto", "always", "never"), default="auto", help="text color policy (default: auto)")
    command.add_argument("--no-color", dest="color", action="store_const", const="never", help="alias for --color never")
    command = _command(commands, 'measure', 'read current application measurements (RAM-only fl64 values)',
        'Read the latest value of each declared application measurement.\n'
        'Only the last published fl64 value is kept in RAM; there is no stored history.\n'
        'A declared measure has no value until its first publication. By default, print one\n'
        'snapshot of all measures. Quote glob patterns to prevent shell expansion.',
        ['ose measure', "ose measure 'analyzer_*.vibration.rms' --watch",
         "ose measure 'vibration.*' --count 10 --interval 0.5 --format jsonl"])
    command.add_argument('patterns', nargs='*', help='glob patterns matching component.name or local dotted names (default: all)')
    command.add_argument('--endpoint', help='local endpoint JSON (default: build/endpoint.json); exclusive with --host/--port')
    command.add_argument('--host', help='target broker or coordinator host')
    command.add_argument('--port', type=int, help='target broker or coordinator TCP port')
    command.add_argument('--distributed', action='store_true', help='query a distributed coordinator instead of the local broker')
    command.add_argument('--tls-ca', help='trusted CA certificate file for distributed mutual TLS')
    command.add_argument('--tls-cert', help='distributed client certificate file')
    command.add_argument('--tls-key', help='private key file matching --tls-cert')
    command.add_argument('--insecure', action='store_true', help='explicit distributed loopback-only development mode')
    command.add_argument('--watch', action='store_true', help='repeat until interrupted')
    command.add_argument('--interval', type=float, default=1, help='sampling interval in seconds (default: 1; minimum: 0.1)')
    command.add_argument('--count', type=int, help='number of snapshots (default: one, or unlimited with --watch)')
    command.add_argument('--format', choices=('text', 'jsonl'), default='text', help='output format (default: text); jsonl emits one JSON object per snapshot')
    command = _command(commands, "record", "discover and interactively record live application outputs",
        "Open the recorder console and discover outputs of the running application.\n"
        "Use list, show and select to choose outputs, then start and stop to capture.\n"
        "Type help in the console for commands, filters and output-file settings.\n"
        "Repeat --command to execute console commands in order.",
        ["ose record", "ose record --command list --command quit",
         "ose record --command 'select all' --command 'start capture.mcap' --command 'wait 10' --command quit",
         "ose record --distributed --host 127.0.0.1 --port 7000 --insecure"])
    command.add_argument("-l", "--label", type=session_label, default=DEFAULT_LABEL,
                         help="informational session label (default: recorder)")
    command.add_argument("--endpoint", help="local endpoint JSON (default: build/endpoint.json when present); exclusive with --host/--port")
    command.add_argument("--host", help="target broker or coordinator host")
    command.add_argument("--port", type=int, help="target broker or coordinator TCP port")
    command.add_argument("--distributed", action="store_true", help="discover remote workers through the coordinator")
    command.add_argument("--tls-ca", help="trusted CA certificate file for distributed mutual TLS")
    command.add_argument("--tls-cert", help="distributed client certificate file")
    command.add_argument("--tls-key", help="private key file matching --tls-cert")
    command.add_argument("--insecure", action="store_true", help="distributed loopback-only development mode")
    command.add_argument("--sdl-root", default=os.environ.get("SDL_ROOT"), help="SDL checkout for recording support (default: SDL_ROOT or installed runtime)")
    command.add_argument("--capacity", type=int, default=256, help="capture queue message limit per endpoint (default: 256)")
    command.add_argument("--byte-budget", type=int, default=8 * 1024 * 1024, help="capture queue byte limit per endpoint (default: 8388608 / 8 MiB)")
    command.add_argument("--max-frame", type=int, default=1024 * 1024, help="maximum transport frame size in bytes (default: 1048576 / 1 MiB)")
    command.add_argument("--command", action="append", help="execute a console command; repeat to script a session")
    command = _command(commands, "adapter", "internal: run a local component adapter (started by run)",
        "Internal process launched automatically by ose run for each local component.\n"
        "Launch the component, connect it to the local broker and drive its ticks and IPC.\n"
        "Use ose run to start an application; this command is for runtime debugging.",
        ["ose adapter --plan build/plan.json --node analyzer --port 7001"])
    command.add_argument("--plan", required=True, help="compiled deployment plan JSON file")
    command.add_argument("--node", required=True, help="component name in the plan")
    command.add_argument("--working-directory", help="override the component working directory")
    command.add_argument("--port", type=int, required=True, help="TCP port of the already running local broker")
    from ose.parameters import cli as parameter_cli
    parameter_cli.parser(commands)
    command = _command(commands, "completion", "generate shell completion",
        "Print a Bash completion script for commands, options and option choices.\n"
        "Load it in the current Bash terminal or add it to ~/.bashrc.",
        ["source <(ose completion bash)"])
    command.add_argument("shell", choices=("bash",), help="target shell")
    from ose.app import add_parser
    add_parser(commands)
    return parser


def main(argv=None):
    parser = create_parser()
    return execute(parser.parse_args(argv), parser)


def execute(args, parser=None):
    try:
        if args.action == 'app':
            from ose.app import run
            return run(args)
        elif args.action == 'completion':
            from ose.completion import bash_completion
            print(bash_completion(parser), end='')
        elif args.action == 'logs':
            from ose.logs import run
            try:
                return asyncio.run(run(args))
            except KeyboardInterrupt:
                return 130
        elif args.action == 'measure':
            from ose.measures import run
            try:
                return asyncio.run(run(args))
            except KeyboardInterrupt:
                return 130
        elif args.action == "parameter":
            from ose.parameters import cli as parameter_cli
            try:
                asyncio.run(parameter_cli.run(args))
            except KeyboardInterrupt:
                return 130
        elif args.action in ("build", "validate"):
            if not args.sdl_root:
                raise ValueError("set SDL_ROOT or pass --sdl-root explicitly")
            if args.action == "validate":
                validate(args.model, args.deployment, args.sdl_root)
                print(json.dumps({"valid": True}))
            else:
                print(build(args.model, args.deployment, args.sdl_root, args.build_dir, args.component))
        elif args.action == "export-kubernetes":
            from ose.modeling.kubernetes import export
            export(args.plan, args.images, args.namespace, args.output, args.image_pull_secret)
        elif args.action in ("serve", "worker", "cluster-status"):
            from ose.transport.secure import contexts, connect
            hosts = [args.host]
            if args.action == "worker":
                hosts += [args.listen_host, args.advertise_host]
            server_context, client_context = contexts(args.tls_ca, args.tls_cert, args.tls_key,
                                                     insecure=args.insecure, hosts=hosts)
            if args.action == "serve":
                from ose.execution.distributed import serve
                asyncio.run(serve(args.plan, args.host, args.port, server_context, args.lease_seconds,
                                  args.kubernetes_lease, args.namespace))
            elif args.action == "worker":
                from ose.execution.distributed import work
                asyncio.run(work(args.plan, args.node, args.host, args.port, listen_host=args.listen_host,
                    listen_port=args.listen_port, advertise_host=args.advertise_host,
                    server_context=server_context, client_context=client_context))
            else:
                async def status():
                    client = await connect(args.host, args.port, 1024 * 1024, client_context, "coordinator")
                    try:
                        result, _ = await client.request("status")
                        print(json.dumps({k: v for k, v in result.items() if k not in ("op", "request", "content")}, indent=2))
                    finally:
                        await client.close()
                asyncio.run(status())
        elif args.action == "run":
            execution_options = {}
            if args.working_directory is not None:
                directory = Path(args.working_directory).resolve()
                if not directory.is_dir():
                    raise ValueError(f"working directory does not exist: {directory}")
                execution_options["working_directory"] = str(directory)
            if args.duration is not None and (not math.isfinite(args.duration) or args.duration <= 0):
                raise ValueError("duration must be finite and positive")
            if args.mode == "simulated":
                from ose.execution.simulation import run
                from ose.execution.simulation.coordinator import DEFAULT_OUTPUT_LIMIT
                if args.duration is None:
                    raise ValueError("simulated execution requires --duration in virtual seconds")
                if args.port is not None:
                    raise ValueError("--port is unavailable in simulated mode")
                summary = asyncio.run(run(args.plan, args.duration,
                    workers=1 if args.workers is None else args.workers, scenario=args.scenario, trace=args.trace,
                    max_tick_output_bytes=DEFAULT_OUTPUT_LIMIT if args.max_tick_output_bytes is None else args.max_tick_output_bytes,
                    **execution_options))
                print(json.dumps(summary), flush=True)
            else:
                from ose.execution.supervisor import run
                if json.loads(Path(args.plan).read_text()).get("execution") == "distributed":
                    raise ValueError("use serve and worker for a distributed plan")
                if any(value is not None for value in (args.workers, args.scenario, args.trace, args.max_tick_output_bytes)):
                    raise ValueError("--workers, --scenario, --trace and --max-tick-output-bytes require simulated mode")
                asyncio.run(run(args.plan, args.duration, args.port or 0, **execution_options))
        elif args.action == "replay":
            if args.mode == "simulated":
                from ose.recording.replay import run
                if args.duration is None:
                    raise ValueError("simulated replay requires --duration")
                if (args.interactive or args.command is not None or args.distributed or args.insecure or any(value is not None for value in
                        (args.speed, args.endpoint, args.host, args.port, args.tls_ca, args.tls_cert, args.tls_key))):
                    raise ValueError("speed, replay commands, interactive controls and connection options require --mode realtime")
                summary = asyncio.run(run(args.recording, args.plan, args.duration, mappings=args.mappings,
                    start_ns=args.start_ns, end_ns=args.end_ns,
                    workers=1 if args.workers is None else args.workers, trace=args.trace, loops=args.loops,
                    schema_policy=args.schema_policy))
            else:
                from ose.recording.live_replay import run
                if args.workers is not None or args.trace is not None:
                    raise ValueError("--workers and --trace require simulated replay")
                async def replay():
                    task = asyncio.create_task(run(args.recording, args.plan, args.duration, mappings=args.mappings,
                        start_ns=args.start_ns, end_ns=args.end_ns, speed=1 if args.speed is None else args.speed,
                        endpoint=args.endpoint, host=args.host, port=args.port, distributed=args.distributed,
                        tls_ca=args.tls_ca, tls_cert=args.tls_cert, tls_key=args.tls_key,
                        insecure=args.insecure, interactive=args.interactive, commands=args.command, loops=args.loops,
                        schema_policy=args.schema_policy))
                    loop = asyncio.get_running_loop()
                    for sig in (signal.SIGINT, signal.SIGTERM):
                        loop.add_signal_handler(sig, task.cancel)
                    try:
                        return await task
                    except asyncio.CancelledError:
                        return None
                    finally:
                        for sig in (signal.SIGINT, signal.SIGTERM):
                            loop.remove_signal_handler(sig)
                summary = asyncio.run(replay())
                if summary is None:
                    return 130
            print(json.dumps(summary), flush=True)
            if summary.get("refused", 0):
                return 1
        elif args.action == "top":
            from ose.top import run
            async def top():
                task = asyncio.create_task(run(args))
                loop = asyncio.get_running_loop()
                for sig in (signal.SIGINT, signal.SIGTERM):
                    loop.add_signal_handler(sig, task.cancel)
                try:
                    return await task
                except asyncio.CancelledError:
                    return 130
                finally:
                    for sig in (signal.SIGINT, signal.SIGTERM):
                        loop.remove_signal_handler(sig)
            return asyncio.run(top())
        elif args.action == "record":
            from ose.recording.console import run
            async def recording():
                task = asyncio.create_task(run(args))
                loop = asyncio.get_running_loop()
                for sig in (signal.SIGINT, signal.SIGTERM):
                    loop.add_signal_handler(sig, task.cancel)
                try:
                    return await task
                except asyncio.CancelledError:
                    return 130
            return asyncio.run(recording())
        elif args.action == "inspect":
            if args.last:
                print((Path(args.endpoint).parent / "last-status.json").read_text())
            else:
                from ose.transport.tcp import Client
                endpoint = json.loads(Path(args.endpoint).read_text())
                async def inspect():
                    client = await Client.connect(endpoint["host"], endpoint["port"])
                    try:
                        result, _ = await client.request("status")
                        print(json.dumps(result, indent=2))
                    finally:
                        await client.close()
                asyncio.run(inspect())
        else:
            from ose.execution.adapter import Adapter
            plan = json.loads(Path(args.plan).read_text())
            async def execute():
                adapter = Adapter(plan, args.node, working_directory=args.working_directory)
                task = asyncio.create_task(adapter.run("127.0.0.1", args.port))
                loop = asyncio.get_running_loop()
                for sig in (signal.SIGINT, signal.SIGTERM):
                    loop.add_signal_handler(sig, task.cancel)
                try:
                    await task
                except asyncio.CancelledError:
                    pass
            asyncio.run(execute())
    except KeyboardInterrupt:
        return 130
    except (ValueError, OSError, EOFError, RuntimeError, subprocess.CalledProcessError) as error:
        print(f"ose: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
