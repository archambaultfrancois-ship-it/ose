# Vibration Watch

<a href="../README.md"><img align="right" src="../docs/branding/ose-logo.svg" alt="OSE project home" width="64" height="64"></a>

An SDL/OSE application in the [OSE repository](../README.md): two synthetic C sensors,
two Rust analyzers, Python alarm policy and a Python terminal observer. Each signal block contains
16,384 float samples by default (64 KiB); results and alarm observations are small messages.
The sensor uses the OSE clock and a seeded generator, so simulation is reproducible.
The application uses model v2 with explicit sensor timers and single-input
callbacks; deployment and scenario documents remain version 1. See the
[migration guide](../ose/docs/model-v2-migration.md) for the execution changes.

## Requirements and first run

New to the framework? The **[beginner OSE quick start](../docs/quick-start.md)**
walks through this application, its output, live parameter changes, simulation
and optional web/recording/distributed workshops.

Python 3.10+, a C99 compiler, Rust/Cargo and Git. OpenSSL is needed to generate
development TLS certificates. The optional parameter directory sync uses
`inotify-simple` on Linux/Termux; ordinary runs need no third-party Python packages.

```sh
cd vibration-watch  # from the OSE repository root
make validate
make demo-local DURATION=3
make simulate WORKERS=4 DURATION=2
make test
```

On Android/Termux, keep the host awake during real-time/distributed tests with
`termux-wake-lock`, then release it with `termux-wake-unlock` afterwards. Host
suspension expires worker leases by design.

The framework is the parent directory of `vibration-watch`; no submodule is needed.
To use another framework checkout with the command-line tools, set
`FRAMEWORK_ROOT=/absolute/path/to/ose`. Build artifacts and prepared absolute
paths live in `build/`. Rebuild after moving a checkout. The workflow uses the
public `python -m ose.cli` interface from the selected framework checkout.

`make clean` removes the entire application `build/` directory, including compiled
components, compilation caches, traces, generated certificates and any captures
saved there. The next build regenerates its artifacts.

For local development, load the application environment and shortcuts in each
Bash terminal from `vibration-watch`:

```sh
source ./setenv.sh
app-run
# In a second terminal, source ./setenv.sh again, then:
ose app record
```

The application environment selects Vibration Watch as `OSE_APP_ROOT` and sources
`../setenv.sh`, including when `FRAMEWORK_ROOT` was previously set to
another checkout. `app-run` builds and runs until Ctrl+C;
`app-run DURATION=60` limits the run. Component callbacks read and write relative
files in the directory where you launch `app-run`; `--working-directory PATH`
selects another directory relative to it. Builds and runtime status files stay
in the application directory. `ose app record` prepares missing recording/filter
dependencies and opens the external console. `ose app env` shows the selected paths; `ose app --help` lists workflows.
Interactive Bash terminals also enable `ose` completion for commands,
subcommands, options and option choices. Source `./setenv.sh`, then press Tab.
The `ose-editor` command opens this application's graphical model editor at
`http://127.0.0.1:8082`, using the same PATH Python as the application.
`ose app record inspect FILE` displays all recorded messages; use `--limit N` to show
only the first N selected messages.

All checkout commands share the detected Python 3.10+ from `PATH` (`python3`
or a versioned command such as `python3.12`). Run `make setup` from
the repository root to prepare their shared dependencies in `build/python-deps`.

Rebuild application components after changing the Python installation in PATH.

### Linux process names

Local and cluster deployments use the instance prefix `vw`. After rebuilding,
`top` shows names such as `vw-broker`, `vw-a-sensor1`, `vw-c-sensor1`,
`vw-c-analyzer1`, and `vw-web`. The `a` process runs OSE's adapter; the `c`
process runs the business callbacks. Both are named automatically before
business initialization, including Python components and the
console. No handler changes are needed.

For a second independent instance, export `OSE_PROCESS_PREFIX=vw2` before
sourcing `setenv.sh` in each terminal. In `top`, use the short-name display (`c`
toggles it), then `o` and `COMMAND=vw-` to filter an instance. `H` shows threads.
The compiled plan stores aliases, and status snapshots provide names and PIDs.
See [OSE process identification](../ose/docs/process-names.md) for the full convention.

### Web console for a local run

The framework includes the English-language OSE Web Console. Install its
optional Python dependencies once (no Node.js build is needed):

```sh
source ./setenv.sh
ose-ws setup
```

The console uses the same PATH Python as the application and editor. `setup`
installs its dependencies into the framework's `build/python-deps` directory
and builds the SDL services. The framework provides this launcher for every
application, like `ose-editor`; Vibration Watch has no separate web-console
script. Missing Python dependencies are also prepared on the first server
launch. Help, `init` and `check` do not prepare dependencies.

Start the local application in one Bash terminal:

```sh
source ./setenv.sh
app-run
```

In a second Bash terminal:

```sh
source ./setenv.sh
ose-ws
```

Open **http://127.0.0.1:8080** and enter the token from
`vibration-watch/.ose-ws/access.token`. The first launch exclusively creates
`ose-ws.json` and that owner-only token in the application directory. These local
files and the capture/database state are ignored by Git. `ose-ws` discovers the
current `build/endpoint.json` and loads the `operator` parameter token from the
current `build/plan.json`, falling back to `deployment.json` before a build.
An explicit `OSE_PARAMETER_TOKEN` takes precedence. Sourcing the environment
does not read or export credentials. The web login token and OSE parameter
client token are separate credentials.

State and captures are written under `.ose-ws/` in the current directory where
you launch `ose-ws`, even after sourcing the environment from elsewhere. Use
`ose-ws --working-directory PATH` to select another existing runtime directory.
The configuration, login token, application endpoint and parameter credentials
remain attached to the application. Relative `state_dir` and `OSE_WS_STATE_DIR`
paths follow the runtime directory; absolute state paths retain their location.
Launch from the same directory to reopen its database and captures.

The launcher works from another directory after sourcing this application's
environment. Every command uses the same detected interpreter from `PATH`.

Use `ose-ws --port 8081` for another HTTP port, `ose-ws check` to check an existing
configuration, or `ose-ws --config /path/to/console.json` for an explicit
configuration (which must already exist). Endpoint overrides follow the caller's
directory. Closing the console leaves the application running. After replacing
the framework, restart the application when new engine capabilities are needed.
See the [console guide](../ose-ws/README.md) for captures, permissions,
network access and the complete-block parameter overwrite semantics.

#### Observe a simulation

```sh
source ./setenv.sh
ose app simulate --duration 60 --interactive
# In another terminal, source ./setenv.sh, then:
ose-ws
```

Open **Simulation** to advance virtual time. The run starts paused; the server
stays available after completion until Ctrl+C. Components, typed parameters,
logs and latest scalar measurements are available. Measurement charts and
application rates use simulated time; resource usage uses real time. Fast
advances may skip intermediate measurements in the console's collected history.
See [simulation server](../ose/docs/simulation-server.md).

#### Compatible contract example

In **Components → Application flow**, select **events**
(`alarm.event → observer.input`), then **Diagnose compatibility** and
**Differences only**. The result is **Compatible**, with one source-only field:

| Field ID | Alarm source | Observer destination | Type |
| --- | --- | --- | --- |
| 7 | optional `source_note` | Not declared (ignored) | `string` |

The alarm publisher fills this optional note with `RMS threshold evaluation`.
SDL skips source fields whose numeric IDs are unknown to the consumer, so the
observer still reads all its declared fields. Both contracts keep the logical
root `Alarm`. The source contract is in
`schemas/alarm.sdl`; the observer's input contract is in
`components/observer/observer_alarm.sdl`. **Live** shows the note in source
publications; the observer's terminal JSON keeps its existing fields. The other
links use matching field declarations. Rebuild and restart the application after updating
these sources; the diagnostic reads the running application's compiled plan.

Recording content filters are evaluated at the source: in the broker for local
execution, or in each producer worker for distributed execution, before capture
queue admission and network delivery. Each recorder has independent filters.
Build the SDL filter service with `make -C ../sdl filter-python` using
the application's Python interpreter, then restart the application with the
updated framework. Cluster images build this service for their own interpreter;
rebuild and redeploy images after updating the framework.

The `normal` scenario keeps both motors below their RMS thresholds. The default
`increasing-vibration` profile raises motor 1's amplitude with elapsed OSE time;
it crosses the 1 m/s² RMS threshold while motor 2 stays normal. These are
application signal-generation profiles, not OSE external-message scenario files.

```sh
make simulate SCENARIO=normal
make demo-distributed DURATION=8 FAIL_NODE=analyzer_1
make demo-distributed DURATION=3 ROUTING=broker
```

The distributed local demonstration launches a coordinator and six independent
worker processes over TCP. It uses explicit loopback-only plaintext mode.
`FAIL_NODE` terminates and replaces that worker; survivor PIDs remain unchanged.
Logs, counters and before/after PIDs are saved under `build/`. This exercises
the distributed protocol without claiming to test a physical node outage.

## Record an analyzer's internal input

The Rust analyzer declares the internal point `dsp.input` during `on_start`.
It records the `SignalBlock` received by the analyzer before calculating RMS
and peak. This lets you inspect what each analyzer actually received, including
the machine ID, sequence, signal timestamp, sample rate and samples. Each instance
has its own point: `analyzer_1.record.dsp.input` and
`analyzer_2.record.dsp.input`. No additional output or graph subscription is needed.
The SDK skips serialization while the point is disabled.

The OSE framework in the parent directory includes the required component SDK.
Rebuild before starting the application:

```sh
# First terminal, from the Vibration Watch directory:
make run DURATION=60

# Second Bash terminal, also from the Vibration Watch directory:
source ./setenv.sh
ose app record
```

```text
record> list --kind internal
record> show analyzer_1.record.dsp.input
record> select analyzer_*.record.dsp.input
record> start analyzer-inputs.pcapng
record> status
record> stop
```

These points observe analyzer inputs; `sensor_*.signal` observes sensor
publications. Capture both to compare sequences across delivery. Internal points
are also available in distributed execution and in simulation when selected
with `--record`. Unselected internal points stay disabled.

The application launcher accepts `--record 'TOPIC_GLOB[, FILTER]'` (repeatable),
`--record-file`, `--record-from` and `--record-until` for `run`, `simulate` and
`replay`. Select internal points using their full topic names:

```sh
python3 tools/vibration.py simulate --duration 1 \
  --record 'analyzer_*.record.dsp.input' --record-file build/analyzer-inputs.mcap
source ./setenv.sh
ose app record inspect build/analyzer-inputs.mcap --check --summary --format json
```

This captures the signal blocks observed inside both analyzers. To also capture
sensor publications, add `--record 'sensor_*.signal'`. The output defaults to
`capture.pcapng`; an existing output is overwritten. Optional `--record-from 0.2s`
and `--record-until 0.8s` select the half-open interval `[0.2s, 0.8s)` in virtual
time for simulation and replay, or elapsed time for `run`. The file and time
options require `--record`.

## Read the current RMS measurement

Each Rust analyzer declares the fl64 measurement `vibration.rms` in `on_start`
and updates it after calculating the RMS of each input block. Values use the same
m/s² unit as the `Analysis.rms` output. Each component retains only its latest
value in OSE RAM, without a measurement history or restoration after restart.

After updating the framework, rebuild and launch the application, then
read the measurements from a second Bash terminal:

```sh
# First terminal, from the Vibration Watch directory:
source ./setenv.sh
app-run

# Second terminal, from the same directory:
source ./setenv.sh
ose measure 'analyzer_*.vibration.rms' --watch
```

The report shows `analyzer_1` and `analyzer_2` separately. Omit `--watch` for a
single reading, or use `--interval 1 --count 10 --format jsonl` for ten readings.
These are snapshots of the latest reported values; intermediate publications
between readings are not retained.

## Run with parameter directory sync

Install the optional watcher once using the application's Python interpreter:

```sh
make -C .. setup
make run PARAMS_SYNC=1 DURATION=60
make run PARAMS_SYNC=1 PARAMS_DIR=./my-parameters SCENARIO=normal DURATION=60
make demo-distributed PARAMS_SYNC=1 DURATION=60
```

The Python launcher accepts the same option:

```sh
"$PYTHON" tools/vibration.py run --params-sync --duration 60
"$PYTHON" tools/vibration.py run --params-sync ./my-parameters --duration 60
```

`--params-sync` defaults to the `parameters/` directory. Each block has a unit
export file, for example `parameters/machines/1/detection.json`:

```json
{"machines/1/detection": {"rms_threshold": 1.0}}
```

Existing valid files are restored when connecting; missing files are generated
from OSE. File edits update the running application, and application parameter
changes update their files. This restoration can occur after component `on_start`.
The launcher selects the current run's endpoint and loads the operator token
from its compiled plan, so sourcing `setenv.sh` is optional for these commands.
The distributed loopback demo enables its development operator token in the
prepared plan; production TLS deployment settings remain unchanged.

The synchronizer stops with the application, including Ctrl+C, SIGTERM and the
duration limit. An unexpected synchronizer exit stops the run. Distributed logs
include `build/distributed-logs/params-sync.log`; local sync messages appear on
stderr. Synchronization is available for real-time `run` and `distributed-local`,
and is disabled by default.

OSE now owns the synchronizer lifecycle and the local cluster orchestration.
The application launcher retains scenario preparation and the deliberate worker
replacement exercise (`--fail-node`); existing Make commands remain available.
After sourcing the environment, the generic entry points are:

```sh
ose app run --params-sync parameters --duration 60 SCENARIO=normal
ose app distributed --duration 60 SCENARIO=normal
ose app make demo-distributed PARAMS_SYNC=1 DURATION=60
```

Distributed launches show component output live on stderr, for example
`[observer] {"event": "vibration", ...}`. Original lines remain in
`build/distributed-logs/observer.log`; they are displayed once as they arrive.

For the synchronized cluster demonstration, retain the last command: it also
prepares the loopback operator token. A plain `PROFILE=cluster` build keeps the
production configuration without that development token. Generic launchers honor
an explicitly supplied `OSE_PARAMETER_TOKEN`; Vibration's demonstration wrapper
selects its own prepared operator token. `app-run --params-sync parameters` also
uses the generic OSE synchronizer.

## Project resources

- `schemas/`: contracts and units; `vibration_signal.sdl` avoids shadowing Python's `signal` module.
- `components/`: reusable business implementations plus thin OSE handlers.
- `config/`: machine definitions, block sizes, seeds and thresholds.
- `model.json`: ports, timers, activation links and bounded QoS.
- `deployment.json`: the single deployment source, describing component implementations,
  distributed routes and cluster placement resources. The launcher uses OSE's
  deployment profiles: local execution removes distributed route overrides and
  placements, and communication uses the broker. Both profiles keep the same components and
  parameter permissions. The development operator token supports local tools before
  the first build; cluster preparation removes it, except for the loopback sync demo.
- `scenarios/`: deterministic profiles; `tests/`: business, end-to-end and opt-in cluster tests.
- `packaging/`: image construction.
- `docs/deployment.md`: cluster deployment instructions.

The alarm inputs are independent and optional: losing one motor's branch does
not stall the other. Every analysis produces an observation with state 0 or 1;
this application does not retain an alarm history or implement debouncing.

## Follow component logs

The SDKs deposit structured logs into a bounded in-memory runtime history.
Sensors, analyzers, alarm policy and observer log their lifecycle under the
`lifecycle` category. The web console's **Logs** page is the second navigation
item, after **Components**; filter by component, minimum level, category or text,
pause/resume the display, or export displayed entries as JSONL.

Operational `logf` deposits include the machine and source sequence. Sensors,
analyzers and the observer emit a `DEBUG` summary on the first block and every
ten blocks under `acquisition`, `analysis` and `display`, respectively (about
once per second with the default sensor period). The alarm policy reports its
initial state and each transition under `detection`: `WARNING` for ALARM and
`INFO` for NORMAL, plus a `DEBUG` summary every ten blocks when unchanged.

```sh
source ./setenv.sh
ose app logs -f
ose app logs 'sensor_*' --logger lifecycle
ose app logs --level DEBUG -f
ose app logs alarm --logger detection -f
ose app logs --level FATAL --format jsonl
```

Levels are DEBUG, INFO, WARNING, ERROR, CRITICAL and FATAL. FATAL is a log level;
the handler decides whether to stop. Logs survive component replacements while
the runtime remains running, within 10,000 entries and 8 MiB. They disappear when
the runtime exits. See [the SDK log service](../ose/docs/logs.md) for
deposit APIs, retention and distributed access.

## Change settings while running

Each machine has two typed SDL parameter blocks, and the terminal observer has
one display block:

| Path | Fields | Reader |
| --- | --- | --- |
| `machines/1/sensor`, `machines/2/sensor` | `samples`, `sample_rate_hz`, `frequency_hz`, `amplitude`, `noise`, `seed`, `ramp_per_second` | Corresponding C sensor |
| `machines/1/detection`, `machines/2/detection` | `rms_threshold` | Python alarm policy |
| `observer/display` | Optional `label` and `output`, variable-length `machine_ids`, nested `output.format` | Python terminal observer |

`config/machines.json`, `config/thresholds.json` and the selected signal profile
provide the machine blocks' initial values when building. The sensors, alarm and
observer read generated typed bindings at every callback. Machine identity,
graph, tick period and queue policies remain deployment/model configuration.

`observer/display` starts with the following value from `model.json`:

```json
{
  "label": null,
  "machine_ids": [1, 2],
  "output": {
    "format": {
      "include_time_ns": true,
      "include_sequence": true
    }
  }
}
```

Its SDL type, `ObserverSettings`, contains an optional string, a repeated
`uint32` field and three message levels: `ObserverSettings` →
`ObserverOutputSettings` (optional `output`) → `ObserverOutputFormat` (`output.format`).
The format flags control whether terminal observations contain `time_ns` and
`sequence`; both are enabled by default. Setting `output` to `null` uses this
default format; when `output` is present, its `format` structure is required.
An absent label (`null`) adds no label to terminal observations; an empty string
is a present label. `machine_ids` filters only the observer's terminal output.
An empty list shows all machines. Sensor generation,
analysis and alarm processing continue for every machine.

In **ose-ws → Parameters → observer → display**, toggle the presence of `label`
or `output`, add or remove `machine_ids` elements, and expand `output` then
`format` to edit the third-level options. Validate the draft, then apply the
complete block.
Restart/rebuild the application after changing this schema to
make the new block available. The CLI can also apply it:

```sh
printf '%s\n' '{"observer/display":{"label":"Workshop A","machine_ids":[1],"output":{"format":{"include_time_ns":true,"include_sequence":false}}}}' | ose app parameter set --file -
ose app parameter get --exact observer/display
# Remove the label, display all machines and use the default output format:
printf '%s\n' '{"observer/display":{"label":null,"machine_ids":[],"output":null}}' | ose app parameter set --file -
```

In a first Bash terminal, start a normal run so changes are easy to observe:

```sh
source ./setenv.sh
app-run SCENARIO=normal
```

In a second Bash terminal, source the application environment again. It selects
the parent framework. Parameter commands resolve the selected client token at invocation:

```sh
source ./setenv.sh
ose app parameter list
ose app parameter get                            # All five blocks
ose app parameter get '^machines/1/'              # Both blocks for motor 1
ose app parameter get '/detection$'               # Both alarm thresholds
ose app parameter get --exact machines/1/detection
"$PYTHON" tools/vibration.py set-threshold --machine 1 --rms-threshold 2.0
ose app parameter watch machines/1/detection --count 2
```

`ose app parameter get` takes a Python regular expression by default and returns one JSON
object mapping each selected path to its complete block. Omitting the pattern
selects all blocks. Use `--exact` for a literal path and quote
patterns to prevent shell expansion. Changing a threshold affects subsequent
observations in the first terminal; stop a run or a watch with Ctrl+C.

The `operator` client has read/write rights on all five blocks; application
components have read access only. `ose app parameter` resolves the selected
client's token from `build/plan.json`, or from `deployment.json` before a
build. Explicit environment tokens take precedence. Use `OSE_PARAMETER_CLIENT`
or `--client NAME` to select another configured client; no new source is needed.

To change a sensor, export only its settings, edit the JSON, and submit it with
the application's business validation:

```sh
ose app parameter get --exact machines/1/sensor > build/sensor-1.json
# Edit the seven fields under "machines/1/sensor" in build/sensor-1.json.
"$PYTHON" tools/vibration.py set-sensor --machine 1 --file build/sensor-1.json
```

`set-sensor` accepts this single-block export or the original bare settings
object. Exports for another machine or multiple blocks are rejected.

For a backup and restore, the path-keyed format works for one or several blocks:

```sh
ose app parameter get > build/parameters.json
ose app parameter set --file build/parameters.json
# Reinject only the selected thresholds through stdin:
ose app parameter get '/detection$' | ose app parameter set --file -
# Submit an explicit path-keyed threshold value:
printf '%s\n' '{"machines/1/detection":{"rms_threshold":0.1}}' | ose app parameter set --file -
```

Exports can also be split across files and restored recursively:

```sh
mkdir -p build/parameters/motors
ose app parameter get '^machines/1/' > build/parameters/motors/motor-1.json
ose app parameter get '^machines/2/' > build/parameters/motors/motor-2.json
ose app parameter set --dir build/parameters
```

`--dir` reads all `*.json` files, including files in subdirectories, in relative
filename order. Every file contains path-keyed exports; filenames do not select
parameter paths. Duplicate paths, invalid files or invalid blocks stop the import
before the first write. `--dir` cannot be combined with `--file` or a positional
path. `--exact` selects a literal path for `ose app parameter get` and `ose app parameter sync`;
it is not an import option for `ose app parameter set`.

There is no `--all` option to manage: imported paths come from the JSON keys.
All imported blocks are checked against their SDL contracts before the first
write. Reads and writes are successive; there is no atomic snapshot or transaction
across blocks. A service failure can leave some blocks applied; its error lists
acknowledged writes. JSON inputs are limited to 16 MiB per file or stdin input;
directory imports additionally limit the sum of file contents to 64 MiB. These
limits can be adjusted with `--max-json-size BYTES` and
`--max-total-json-size BYTES`. The SDL transport limits still apply.

The application commands validate business constraints before submitting values:
1–131072 samples, a nonzero uint32 seed, sample rate 1–1e9 Hz, vibration frequency
0–half the sample rate, amplitude/noise/ramp 0–1e6 and RMS threshold 1e-12–1e12.
The generic `ose app parameter set` / `ose parameter set` commands check SDL types;
writers using those interfaces must also respect these business constraints.

An update applies at a subsequent callback, without restarting the component.
Sensor changes affect the next generated block. Sample count changes resize its
buffer; a different seed resets only the noise generator. Sample index and sequence
continue. Frequency/rate changes may introduce a phase jump. Amplitude remains
`amplitude + ramp_per_second * elapsed OSE time`, including after a ramp change.
An alarm threshold change is used on subsequent analyses; it does not reclassify
previous observations. Updating one machine leaves the other machine's settings
unchanged. Writes replace complete blocks, and separate blocks do not change
atomically.

A replacement worker receives the current settings before starting. Values live
in broker/coordinator memory; restarting it restores the built initial values.
Captures contain signal messages, not parameter updates: replay uses the replay
plan's settings. Reproducing a session with changed settings requires supplying
those settings separately. Deterministic simulations can schedule complete block
updates using `ose run --mode simulated --scenario FILE`, as described in the
[framework parameter guide](../ose/docs/parameters.md).

The same commands accept `--host`, `--port` and `--plan`; use
`--plan build/plan.json --endpoint build/endpoint.json` for a local
replay application (all component parameter blocks remain available).
Distributed clients use `--distributed --tls-ca build/pki/ca.crt --tls-cert build/pki/operator.crt
--tls-key build/pki/operator.key` with an explicit host and port. `make certs`
generates the development operator credentials. Cluster access authenticates the
`operator` certificate identity; it does not use the local development token.
The ordinary `ose parameter get PATH --client operator` still returns a
bare block for existing scripts; the application command returns a path-keyed export.
See [cluster deployment](docs/deployment.md) for credentials and connectivity.

## Replay captured signals

The parent framework provides `ose replay`. During framework development,
set `FRAMEWORK_ROOT=/absolute/path/to/ose` to select another checkout. In that
case, source that checkout's `setenv.sh` from the application directory instead
of the application environment below.

Start the application, then capture both sensors in another terminal using the
same framework and Python interpreter:

```sh
make run DURATION=60
# Second terminal, from the application directory:
source ./setenv.sh
ose app record --command 'select sensor_1.signal sensor_2.signal' \
  --command 'start signals.pcapng' --command 'wait 2' --command stop
```

Replay the signals through the Rust analyzers, Python alarm policy and observer:

```sh
make replay RECORDING=signals.pcapng DURATION=3 WORKERS=4
make replay RECORDING=signals.pcapng DURATION=6 LOOPS=2
make test-replay
```

`replay` uses the normal complete model in `build/plan.json`. It leaves the two
C sensors inactive with `--disable-component sensor_1 --disable-component sensor_2`
and injects the recorded messages into their original `sensor_1.signal` and
`sensor_2.signal` topics. Components, subscriptions and parameter blocks are
retained; no replay-specific model is generated. It accepts PCAPNG or MCAP
captures; both sensor topics must contain messages.
`tools/vibration.py replay --recording FILE` also accepts `--start-ns` and
`--end-ns` for a source publication-time range.
To record internal points while replaying, use

```sh
python3 tools/vibration.py replay --recording signals.pcapng --duration 3 \
  --record 'analyzer_*.record.dsp.input' --record-file build/replayed-inputs.mcap
```

`LOOPS` (CLI: `--loops`) is the total number of passes, defaulting to 1; 0 repeats
until the global `DURATION` limit. Components retain state between passes, and
payload timestamps and sequence numbers are preserved.

The first selected message becomes virtual time zero; recorded intervals and
payloads are preserved. `DURATION` is the virtual horizon from that origin;
choose it long enough to cover the selected capture. Terminal observations are
recomputed from the samples, including the original payload sequence numbers
and timestamps. The logical trace is saved as `build/replay/trace.jsonl`.

For live replay, build the normal model and start it with the sensors inactive:

```sh
make replay-build
ose run --plan build/plan.json --disable-component sensor_1 --disable-component sensor_2
# In a second terminal with the same framework environment:
ose replay signals.pcapng --mode realtime --plan build/plan.json \
  --map sensor_1.signal=sensor_1.signal --map sensor_2.signal=sensor_2.signal \
  --speed 1 --interactive
```

Component supervision is independent of replay. On a running local application,
`ose component disable sensor_1 sensor_2 --endpoint build/endpoint.json` suspends
the sensors; `ose component enable sensor_1 sensor_2` resumes their existing state.
Initially disabled sensors do not run their startup hooks. See the
[supervision guide](../ose/docs/component-supervision.md) for lifecycle semantics.

The replay console starts paused. Enter `help` or `?` for available commands,
then `resume` to start automatic playback. Use `step`, `step 100ms` or
`step-messages 1` to advance manually.

Use `speed 0.5`, `speed 2`, `pause`, `resume`, `status` and `stop` in the replay
console. Speed 0 runs as quickly as admission permits. Application timestamps
stay unchanged; changing playback speed changes the spacing between publications.
For a scripted session, replace `--interactive` with repeated options such as
`--command 'speed 2' --command resume --command 'wait 3' --command status --command stop`.
Commands run in order; `wait` uses wall time, and the end of the script stops
replay. A capture that finishes earlier cancels the remaining commands.
Add `--loops N` for N passes or `--loops 0` for continuous live replay. In the
latter case, commands, signals or an optional global `--duration` stop playback.

`make replay-build` is a compatibility entry point for the ordinary build.
`make replay-build PROFILE=cluster` builds the distributed version.
Start its coordinator with both sensor names passed to `--disable-component`,
and its workers using `build/plan.json`, as described
in the [framework's distributed guide](../ose/docs/distributed.md).
Feed it with the same replay command plus `--host HOST --port PORT` and the TLS
credentials of a valid CA-signed client certificate. For a loopback development
cluster, use `--host 127.0.0.1 --port 7000 --insecure`. Injected signal routes use
the coordinator, including when their ordinary component routes are direct.
Live replay uses the running application's state and ordinary queue policies; increasing speed can cause `drop_oldest` to discard inputs if
the consumers cannot keep up. Check runtime counters when selecting a rate.

`make test-replay` prepares the optional recording dependency, captures a live
two-second signal sequence, and checks that replay reproduces every corresponding
live observation, including RMS, peak and alarm state. It compares simulation
traces from one and four workers, then checks live local and distributed replay.
This test uses temporary captures; it does not consume
existing recordings or require a cluster. It is separate from `make test` so the
ordinary suites keep their standard-library-only Python requirements.

## Cluster deployment

See [deployment instructions](docs/deployment.md) and [architecture](docs/architecture.md).
The image build compiles one component per image, with its generated container,
SDL runtime, native recording filter service and OSE adapter. Cluster images are referenced by digest.
Distributed execution is real time; deterministic simulation remains local.

No images are published and no cluster is modified by `make test`.
The separate `.github/workflows/vibration-watch.yml` workflow at the repository
root runs the same checks. Changes to the application, SDL, OSE, web console or
shared environment script trigger it. It uses the current framework checkout
without a submodule or a separate framework access token.
