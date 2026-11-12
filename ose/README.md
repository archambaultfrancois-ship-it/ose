# OSE — Orchestration, Simulation, Exécution

<a href="../README.md"><img align="right" src="../docs/branding/ose-logo.svg" alt="OSE project home" width="64" height="64"></a>

Executable component graphs with SDL2 contracts and per-subscription quality of
service. See the [feature overview](docs/features.md) for the available user-facing services.
The shared [technical glossary](../GLOSSARY.md) defines SDL, OSE and Web Console terminology.

The [Push/Pull and CENV/2 transition](docs/cenv2-transition.md) tracks the staged
execution-model and transport evolution. Python, Rust, C, Java and Ada components use model
v2 input callbacks, timers and passive Pull inboxes in local/distributed real-time
execution or local parallel simulation; see the [feedback example](examples/feedback-pull-v2/README.md).
Network exchanges use [binary CENV/2](docs/cenv2.md); upgrade all peers together.
See [Rust input callbacks](docs/rust-inputs.md), [C input callbacks](docs/c-inputs.md)
and [Java input callbacks](docs/java-inputs.md). [Ada input callbacks](docs/ada-inputs.md)
are qualified with GNAT 12 on Linux. Actual JVM 7 execution remains deferred;
the Java 7 source/API target is qualified with JDK 17.

Linux processes and SDK threads have short, stable names for `top` and `ps`.
See [process identification](docs/process-names.md) for deployment aliases and instance prefixes.

For a browser interface, install the separate [OSE Web Console](../ose-ws/README.md)
package. It observes running applications and edits parameters through existing
OSE RPCs, without adding web framework dependencies to this engine.

The Python distribution, import namespace and command-line tool are named `ose`.
Install with `python3 -m pip install .`, then use `ose --help` or
`python3 -m ose.cli --help`. Rebuild application plans and generated containers
after upgrading from an earlier package layout.

Enable Bash completion with `source <(ose completion bash)` (also suitable
for `~/.bashrc`). The framework's `setenv.sh` enables it automatically in
interactive Bash terminals. Completion covers commands, nested parameter actions,
options and option choices, with Bash's usual filename fallback.

Use `ose app` as the application control point:

```sh
ose app --root /path/to/application run --duration 60
ose app record
ose app record inspect capture.mcap --check
ose app parameter get > parameters.json
ose app parameter set --file parameters.json
ose app parameter sync --dir parameters
ose app env
```

`--root`, `OSE_APP_ROOT` and the current directory select the application in that
order. Run builds through its Makefile first and is unlimited by default.
Components use the launch directory for relative file reads and writes; use
`--working-directory PATH` to override it, with relative paths resolved from the
launch directory. Plans, scenario inputs, endpoint files and shutdown status
remain in the application directory; relative `--trace` output follows the launch
directory. Low-level `ose run` keeps each component's plan `cwd` unless overridden.
Record prepares only missing services; parameter commands resolve client
credentials on each invocation. Get exports `{path: block}`. Parameter JSON and
capture paths are relative to the application, except sync storage, which follows
the terminal directory.
The shell keeps only `app-cd`, `app-build` and `app-run`, plus the `ose` checkout
entry point. All workflow behavior also works from the installed package.

This directory is a standalone project designed for extraction into a
separate repository. In the [OSE repository](../README.md), the engine is in
`ose/` and uses the sibling `../sdl` project by default. An explicit path
supports a separate SDL dependency checkout.

Requirements: Python 3.10+ in PATH, a C99 compiler, Rust/Cargo, and a compatible SDL
checkout. Java components additionally need a Java 7-compatible compiler/JVM;
Ada components need GNAT with the full Ada 2012 runtime. The complete `make check`
requires both toolchains; `make check TEST_LANGUAGES=python,c,rust` selects the
original three-language suites. The Python engine uses only the standard library. All delivery queues,
catalogues, retry state and deduplication history live in bounded RAM structures.
The dependency reference, repository, and subdirectory within that repository are
recorded in `sdl-dependency.json`. Standalone CI follows `main` and uses its `sdl/`
project. Development accepts an explicit compatible checkout containing
`sdlc/generator.py` and `runtime/`. Neither building nor running automatically
downloads SDL. OSE uses `sdlc/generator.py` for compilation.

## Quick start

For a step-by-step introduction with prerequisites, expected results and a live
parameter exercise, see the **[beginner quick start](../docs/quick-start.md)**.

From this project's root (`cd ose` from the repository root):

```sh
make validate
make demo
make check
```

For multi-machine real-time execution, see [distributed deployment](docs/distributed.md).
OSE provides authenticated `serve`/`worker` processes, direct or broker routes,
cold component replacement and Kubernetes manifest export.

The [interactive recorder](docs/recording.md), `ose record`, discovers
live outputs without a model, selects points and SDL filters, and starts/stops
local PCAPNG or MCAP files. It supports the local broker and distributed workers.
Use [`ose replay`](docs/replay.md) to feed captured topics into a model's
external inputs for paced local/distributed execution by default, with optional
interactive speed and pause controls. Select `--mode simulated` for simulation.
Both replay modes and simulated `run` support `step N`, `step 100ms` and temporal
breakpoints, interactively or through `--command` / `--command-file`. Simulation
with `--microsteps` also supports `microstep N`. Controlled sessions start paused;
real-time `run` rejects these controls.

`SDL_ROOT` defaults to the sibling SDL project (`../sdl`). To use another checkout,
set `SDL_ROOT=/absolute/path/to/ose/sdl` in the environment or on the `make` command
line. The CLI also accepts `--sdl-root`, which takes precedence over `SDL_ROOT`.
For a separately extracted or installed project, set the dependency path explicitly.

The example runs a C measurement source, a Rust transformation doubling values,
and two Python observers. All four use generated containers and user callbacks;
the Python observers share their callback implementation. The transformation consumes a TCP FIFO with receipt
ACKs and retries. An observer uses a RAM UDP FIFO with receipt ACKs
and retries. A slower display reads the latest UDP state without retries.

Results are printed as JSON lines to stderr; the example does not write message
files. The display can skip intermediate states. Restarting discards pending
messages and the last state. Each broker run has a fresh identity, so a restarted
source may restart its own sequence without colliding with prior message IDs.
Receipt ACKs confirm admission in RAM, not survival of a crash or power loss.

You can also install the CLI with `python3 -m pip install .`. Without installing:

```sh
export PYTHONPATH="$PWD/src"
python3 -m ose.cli validate --model examples/acquisition/model.json \
  --deployment examples/acquisition/deployment.json
python3 -m ose.cli build --model examples/acquisition/model.json \
  --deployment examples/acquisition/deployment.json
python3 -m ose.cli run --plan build/plan.json
```

In another terminal, with the same environment:

```sh
python3 -m ose.cli inspect
python3 -m ose.cli top
```

[`ose top`](docs/monitoring.md) shows recent component rates, FIFO occupancy,
tick p95 and colored diagnostics locally or through the distributed coordinator.
Use arrows/Enter for details, Space to freeze, or `--format jsonl` for collection.

[`ose logs`](docs/logs.md) reads component logs retained in runtime memory;
`ose logs -f` follows the stream. Python, C, Rust, Java and Ada SDKs deposit
DEBUG, INFO, WARNING, ERROR, CRITICAL and FATAL entries. The web console's
second page displays selectively filtered logs. Entries are volatile, with
bounded retention and explicit loss reporting.

[`ose measure`](docs/measurements.md) reads application-defined fl64 values,
retaining only the last value per declared dot-separated name in RAM. Declare
with `ctx.declare_measurement("moteur.temperature")` in Python `on_start`, emit
with `ctx.measure("moteur.temperature", value)` in tick callbacks, and read with
`ose measure --watch` or `--count 60 --format jsonl`. Equivalent APIs exist
in C, Rust, Java and Ada. Components restart with no saved measurements.

Ctrl+C stops OSE and its component processes. `run --duration 2` provides
a finite execution; `inspect --last` reads the final saved counters after shutdown.
Supervised adapters and components run in separate process sessions so terminal
Ctrl+C is handled by the supervisor. Components receive the normal IPC stop
request, allowing `on_stop` callbacks to run without `KeyboardInterrupt`
tracebacks. Component errors continue to use stderr normally.
Counters include queue depths, age, replacement, refusal, duplicate, retry and
failure counts, as well as observed average publication and processing rates.

## Model and SDK

[Shared parameter blocks](docs/parameters.md) provide named SDL settings with
per-component read/write bindings, initial values before `on_start`, and updates
between callbacks. Several components may share a block; the last complete write
replaces its value. CLI and external clients can read, write and watch selected
blocks locally or through the distributed coordinator. See the runnable
[two-detector example](examples/shared_parameters/README.md).
On Linux/Termux, `pip install '.[sync]'` enables
`ose parameter sync --dir parameters --client operator` to synchronize
unit JSON files with the running application using inotify and OSE subscriptions.
JSON imports allow 16 MiB per input and 64 MiB per directory by default.

See the [model.json grammar reference](docs/model-json.md) for accepted fields,
defaults and validation constraints, [architecture](docs/architecture.md) for
document fields and process layout,
[runtime execution](docs/runtime.md) for ticks, message flow and acknowledgements,
[simulation](docs/simulation.md) for reproducible virtual-time execution and parallel workers,
[delivery](docs/delivery.md) for FIFO/latest semantics and transport guarantees,
and [component protocol](sdk/protocol.md) for the Python, C, Rust, Java and Ada SDK boundary.
The example documents are complete runnable model/deployment templates.

Components can provide only user callbacks: declare
`"container": {"language": "python", "handler": "components/analyse/logic.py"}`
in their deployment entry. Use `language: "c"` with a `.c` handler or
`language: "rust"` with a `.rs` handler for native containers. The builder generates
the executable wrapper, native compilation commands, typed decoding and
publication methods, and creates a handler skeleton only
if the file is missing. Runnable examples use single-input callbacks and explicit
timers. See [model v2 migration](docs/model-v2-migration.md) for the model and API
changes, and [callback containers](docs/callbacks.md) for generation and lifecycle rules.

Push inputs can set `"timeout": 0.5` (seconds). At expiry, their callback receives
a timeout reference without a message. Push inputs activate independently;
Pull inboxes are accessed explicitly during another callback. Output activation
permissions are declared per input event. See the [v2 execution rules](docs/cenv2-transition.md).

Raw Python components use
`from ose.execution.input_process import InputSession`; the
`sdk/python/sdl_ose_component.py` entry point also exports the CH/2 session,
typed callback context and input references.
The C socketpair SDK is compiled from `sdk/c`; its optional callback runner uses SDL.
The dependency-free Rust socketpair crate lives in
`sdk/rust`. They expose the same binary lifecycle while generated SDL bindings
perform the actual message encoding and decoding.

## Tests and extraction

`make check` runs all test suites in sequence with a compact progress bar. It
prints the complete output of a failing suite and stops immediately. Use
`make test` for verbose output from every suite.

`make test-simulated` checks deterministic virtual-time execution across worker
counts, native containers, scenarios, timeouts and bounded queue admission.
Choose `ose run --mode simulated --duration 60 --workers 4` to simulate
60 virtual seconds. The default remains real-time execution. See the
[parallel simulation example](docs/simulation.md#runnable-parallel-example).

`make test-unit` needs only Python. `make test-sdk` runs the Rust SDK checks.
`make test-integration` uses sockets, injects
packet/ACK loss, tests RAM bounds, volatility and schema compatibility, and copies this
entire project into a temporary standalone directory before building and running
the C → Rust → Python OSE example. Integration tests require SDL_ROOT; invoking them
directly without it skips SDL-dependent tests.

Backpressure regressions also cover 1100 queued TCP messages with a delayed
catalogue reply, independent input admission and sender retries, per-port schema
checks, callback-scoped inbox access and bounded shutdown of a non-reading child.
Delivery-limit regressions cover a real blocked TCP reader, outgoing packet
size checks, a 20-second admission wait and status/metrics exceeding 64 KiB.
CH/2 generated-container tests cover typed publications, Push/Pull references,
timers, dynamic schema evolution, deferred withdrawals, callback failure and
preservation of user code. All five SDKs run feedback examples and shared services;
Java's default source/API/bytecode target is checked with JDK 17, while actual
JVM 7 execution is deferred. Application models and plans require version 2;
CH/1 execution and the snapshot scheduler are removed. See the
[transition status](docs/cenv2-transition.md).
RAM tests verify shared binary payloads, bounded retry indexes, wakeups and loss
of queued state on restart. Delivery has no SQLite dependency or file I/O.

Legacy `durability: "memory"` declarations are accepted and omitted from new
resolved policies. `durability: "disk"` is rejected explicitly: there is no persistent
backend and old database files are not used. Rebuild previous plans before running.
Build artefacts, `endpoint.json` and `last-status.json` remain diagnostic files;
they do not store messages or restore delivery state.

To extract, copy this project root into the new repository, retaining its SDKs,
examples, tests and dependency lock. The nested `.github/workflows` directory
becomes active when this directory becomes the repository root. No source paths
need changing; build plans are regenerated for the new checkout location.

The [Python, C, Rust, Java and Ada external client SDKs](docs/clients.md) support publishing
and subscribing to the broker without a client-side model. The application model
can also declare `external_topics` and connect a topic to an input using
`from_topic`. See the examples in `examples/external/`.

Java and Ada: see [SDKs, callbacks, external clients and toolchains](docs/java-ada.md).
The [multilingual example](examples/multilingual/model.json) connects Python, C, Rust, Java and Ada in one graph.

Batch captures can be attached to `run` and `replay` in either clock mode with
`--record 'TOPIC_GLOB[, FILTER]'` and optional `--record-file capture.mcap`.
The default output is `capture.pcapng`; existing capture files are overwritten.
Use `--record-from 10s --record-until 20s` to restrict capture to `[10s, 20s)`;
replay windows follow playback position, including speed and pauses.
See [batch recording](docs/recording.md#batch-capture-with-run-and-replay).

Simulated replay does not require `--duration`: after the requested input passes,
it processes remaining events until quiescence or interruption. Periodic timers
may keep it running indefinitely; provide `--duration` for a virtual time limit.
