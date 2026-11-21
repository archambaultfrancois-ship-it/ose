# Thermal Workshop — OSE Web Console load application

Sixteen Python components and four C analyzers model four independently heated cells, with four local
feedback loops and four supervisory feedback paths. This is a synthetic workload
for **ose ws**, not a physical plant model or a performance benchmark result.
It uses the actual SDL generated codecs, OSE processes, queues and parameter RPCs.

## Graph

Each `cell_N` instance contains `plant`, `analyzer`, `controller`, `actuator`:

```mermaid
flowchart LR
    P[Plant ×4] -->|SensorFrame| A[Analyzer ×4]
    A -->|Analysis| C[Controller ×4]
    C -->|Command| V[Actuator ×4]
    V -->|Actuation · Pull| P
    A --> F[Fusion]
    F -->|FleetReport| S[Supervisor]
    S -->|Policy · Pull| C
    F -->|FleetReport · Pull| H[Historian]
    H --> U[Audit]
    S --> U
```

There are **20 components, 28 subscriptions, 5 editable parameter blocks and
83 scalar measurements**. The four control loops regulate temperature using
feedforward plus proportional correction. Actuators limit power slew. The
supervisor adjusts cell targets to reduce thermal imbalance. Fusion reports the
latest analysis from each cell; it is an asynchronous aggregation, not a strict
join of matching timestamps. Historian keeps a bounded recent history.

Plant and historian timers drive the graph. Feedback uses bounded `latest` Pull
inboxes, consumed by the next activation, so cycles do not spin indefinitely at
one simulated instant. Forward paths use bounded FIFOs with `drop_oldest`; fusion
uses `latest` buffers. Under overload, losses/replacements are expected and visible
in the console. Runtime keeps its regular component diagnostics and real-time
queue policies.

## Subsystem composition

The application model connects six instances of three definitions:

| Definition | Instances | Local components | Public message ports |
| --- | --- | --- | --- |
| [Cell](subsystems/cell/subsystem.json) | `cell_1` … `cell_4` | plant, analyzer, controller, actuator | input `policy`; outputs `frame`, `analysis` |
| [Supervision](subsystems/supervision/subsystem.json) | `supervision` | fusion, supervisor | inputs `cell_1` … `cell_4`; outputs `report`, `policy` |
| [Recording](subsystems/recording/subsystem.json) | `recording` | historian, audit | inputs `report`, `policy`; output `archive` |

The cell definition owns its complete local feedback loop. Supervision aggregates
its four analysis inputs and produces the policy shared by all controllers.
Recording owns the history timer and audit paths. The root model only connects
these public ports; internal connections retain their original queue policies.
Public ports are aliases and add no component or queue.

The root still owns exactly five parameter blocks. Every instance binds its
public `workload` parameter to the same root block; each cell's `settings` binds
to `cells/N/settings`. Supervision binds to these same settings, so live edits
remain shared between each control loop and the supervisor. Definition defaults
are used only when a subsystem runs independently.

Cell identity is fixed at process startup by `THERMAL_CELL` in the plant and
controller deployments. The cell deployment defaults to `1`; the root deployment
overrides it for each instance. This keeps one reusable model and leaves the SDL
message and editable parameter contracts unchanged. To add a cell to this
four-cell application, extend supervision's inputs/settings and the root wiring
as well as instantiating the cell definition.

From this directory, with `source ./setenv.sh` loaded:

```sh
ose editor --root .
ose subsystem validate subsystems/cell
ose subsystem test subsystems/cell --build-dir build/cell-test
```

Open shared definitions in the editor to change all their instances. In the web
console, use **Subsystem scope** to inspect a cell, supervision or recording and
their crossing links. Logical leaf names are now `cell_1.plant`,
`supervision.fusion`, etc.; execution uses generated stable IDs. Rebuild the plan
and recreate watches referring to the old flat component or subscription IDs.

The standalone cell test runs its real C analyzer, injects a supervisory policy,
updates public settings and checks ten sensor frames and analyses. It uses the
Python workload fallback; the full application build also enables native kernels.

## Start with the web console

From the repository root, in a Bash terminal:

```sh
cd thermal-workshop
source ./setenv.sh
ose app run LOAD=nominal
```

The run is unlimited until Ctrl+C. In a second Bash terminal:

```sh
cd thermal-workshop
source ./setenv.sh
ose ws
```

Open **http://127.0.0.1:8080** for direct local access. Use `ose ws --auth`
to require the browser token in `thermal-workshop/.ose-ws/access.token`.
The application environment selects this
application's plan, parameter client and endpoint; it does not reuse Vibration
Watch's state. First launch prepares missing console services if needed.
This example's local operator credential in `deployment.json` is for loopback
development, as in Vibration Watch. Use the console's normal proxy/authentication
configuration for remote access.

Useful pages and exercises:

- **Components**: inspect 20 nodes, 28 links, metrics, link health and history.
- **Live** on a plant → analyzer link: expand nested channels and watch changing
  sample counts, optional diagnostics, notes and tags. Real-time execution is
  required for Live previews and dynamic captures.
- **Measurements**: select temperatures, powers, targets, samples and message
  counts from several cells; compare combined and separate charts.
- **Parameters**: edit `cells/N/settings` targets/gains and the shared `workload`
  block. All cells see the shared workload. Change `max_samples`, `burst`,
  `cpu_iterations` or `log_every` during a run, and observe the resulting load.
- **Recordings**: capture plant frames and fleet reports; inspect the resulting
  variable payloads offline with `ose app record inspect FILE --summary`.
- **Logs**: lifecycle events and sampled workload logs; reduce `log_every` to
  increase log volume without enabling per-message terminal printing.

## Load profiles

```sh
ose app run LOAD=busy
ose app run LOAD=stress --duration 30
# Finite standalone run, without the shell environment:
make run LOAD=nominal DURATION=30
```

| Profile | Plant period | Frames per plant tick | First-channel samples | Analyzer CPU iterations |
| --- | ---: | ---: | ---: | ---: |
| nominal | 100 ms | 1 | 64–256 | 0 |
| busy | 20 ms | 2 | 256–2,048 | 1,000 |
| stress | 10 ms | 4 | 1,024–8,192 | 5,000 |

Each plant frame has 1–4 channels; subsequent channels have fewer samples. The
profile also changes fusion's period (twice the plant period) and historian's
period (five times the plant period). Profiles are selected at build/start;
parameter edits affect payload size, burst, CPU work, logs and regulation live,
but not timer periods. Rebuilding/reset restores initial profile values.

These are **offered workloads**, not promised throughput. `stress` can saturate
this Termux device; start with `nominal`, then `busy`. CPU work, burst, channel
samples and retained history are bounded in the handlers even when parameters
are edited. Per-frame limits: 1–4 channels, at most 8,192 samples in the first
channel, at most 16 frames per plant activation, 200,000 CPU iterations per
analysis. History retains 64 means. Logs and web histories use the framework's
normal bounded stores. Bounds on values do not make arbitrary strings or every
possible parameter document a fixed-size workload.

## Simulation

```sh
source ./setenv.sh
ose app simulate --duration 60 --interactive LOAD=nominal
# Second terminal: source ./setenv.sh; ose ws
```

The server starts paused. The Simulation page supports stepping, pause/resume,
reset and persistent time breakpoints. CLI controls share the same command
service. Virtual time labels measurements and rates; component resources and
connection freshness use real time. A fast simulated run can skip intermediate
observations in ose-ws. Use **real time** for sustained console/Live/capture load;
use simulation for repeatable functional scenarios and control testing.

```sh
make simulate LOAD=nominal DURATION=3 WORKERS=4
```

All signals and message shapes are deterministic functions of logical time,
cell and sequence; no random or wall-clock inputs are used in the handlers.
Real-time scheduling may drop messages according to the configured policies.

## Message structures

The SDL contract is [schemas/messages.sdl](schemas/messages.sdl). Six root types
are exchanged, with nested structs rather than JSON strings inside messages:

| Root | Variable structure |
| --- | --- |
| `SensorFrame` | 1–4 channels, packed float samples, repeated tags, optional note and diagnostic with repeated hints |
| `Analysis` | repeated features, optional units, optional spectrum with variable bins, optional diagnostic |
| `Command` | repeated reasons, optional ramp with a varying stage count |
| `Actuation` | variable stages, optional stage notes, optional protection diagnostic |
| `FleetReport` | repeated analyses and alarms with nested evidence, variable bounded history, optional note |
| `Policy` | repeated cell targets, optional per-cell reason and optional global note |

Optional fields alternate between present and absent, array lengths vary across
frames, and timestamps/sequences use uint64. The web console's exact integer
representation and expandable message tree can therefore be exercised together.

## Build and tests

```sh
make validate
make build LOAD=busy
make test
```

The complete analyzer uses the generated C SDK for decoding, shared parameters,
callbacks, publishing and measurements. A C99 compiler and libm are required.
`components/analyzer.py` remains the reference implementation; select it with
`make build LOAD=stress ANALYZER=python`, or `--analyzer python` on
`tools/workshop.py`. The default is `ANALYZER=c`.

Normal application builds also compile `native/workload.c` into `build/native/`
with the selected Python's development headers. Generated component environments
load this module directly: plant sample generation and the optional Python
analyzer's burn loop run in C. The callback code uses ordinary imports and calls,
without source transformations. The helpers have a Python
fallback for running handlers independently of an application build. Runtime IPC,
parameter caching and simulation observation optimizations are provided by OSE;
build its optional extension with `make -C ../ose native`.

The C analyzer uses ordinary double accumulation and multiplication for squared
deviations. Tests check numerical agreement within tolerance, message structure,
and measurements. Rounding and simulation trace hashes can differ from Python.
Workload logs retain their fields and cadence; C formats floating values with
17 significant digits instead of Python's shortest representation.

`model.json`, `deployment.json` and `subsystems/*/*.json` are editable source
files. The preparation script selects a load profile and copies the composed
model, definitions and deployments into ignored `build/prepared/`, preserving
hierarchy metadata and resolving schemas/handlers to their authored locations;
generated containers and SDL bindings are in `build/components/`.
Tests build the actual application in an isolated temporary directory, exercise
all 20 subprocesses and feedback, round-trip changing nested messages through
SDL, and verify ose ws graph, parameter, log and measurement APIs. A real-time
test also checks Live message previews and concurrent API reads. Tests stop
their component processes and leave the application's selected build untouched.
The web integration tests require the
optional ose ws dependencies (`source ./setenv.sh; ose ws setup`).
