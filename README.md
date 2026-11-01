# Vibration Watch

A standalone SDL/OSE application: two synthetic C sensors, two Rust analyzers,
Python alarm policy and a Python terminal observer. Each signal block contains
16,384 float samples by default (64 KiB); results and alarm observations are small messages.
The sensor uses the OSE clock and a seeded generator, so simulation is reproducible.

## Requirements and first run

Python 3.10+, a C99 compiler, Rust/Cargo and Git. OpenSSL is needed to generate
development TLS certificates. The optional parameter directory sync uses
`inotify-simple` on Linux/Termux; ordinary runs need no third-party Python packages.

```sh
git submodule update --init --recursive
make validate
make demo-local DURATION=3
make simulate WORKERS=4 DURATION=2
make test
```

On Android/Termux, keep the host awake during real-time/distributed tests with
`termux-wake-lock`, then release it with `termux-wake-unlock` afterwards. Host
suspension expires worker leases by design.

The pinned framework (`e1a7018`, including internal recording points and current
application measurements) lives in `deps/sdl-ose`; for framework development, set
`FRAMEWORK_ROOT=/absolute/path/to/sdl-ose`. Build artifacts and prepared absolute
paths live in `build/`. Rebuild after moving a checkout. The workflow uses the
public `python -m ose.cli` interface from the selected framework checkout.

For local development, initialize the pinned framework submodule, then load its
environment and shortcuts in each Bash terminal:

```sh
git submodule update --init --recursive
source ./setenv.sh
app-run
# In a second terminal, source ./setenv.sh again, then:
record
```

The application environment selects Vibration Watch as `OSE_APP_ROOT` and sources
`deps/sdl-ose/setenv.sh`, including when `FRAMEWORK_ROOT` was previously set to
another checkout. `app-run` builds and runs until Ctrl+C;
`app-run DURATION=60` limits the run. `record` prepares missing recording/filter
dependencies and opens the external console. `ose-env` lists the shortcuts.
`record-inspect FILE` displays all recorded messages; use `--limit N` to show
only the first N selected messages.
Recording content filters are evaluated at the source: in the broker for local
execution, or in each producer worker for distributed execution, before capture
queue admission and network delivery. Each recorder has independent filters.
Build the SDL filter service with `make -C deps/sdl-ose/sdl filter-python` using
the application's Python interpreter, then restart the application with the
updated framework. Cluster images build this service for their own interpreter;
rebuild and redeploy images after updating the framework submodule.

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

The pinned OSE framework includes the required component SDK. Initialize the
submodule and rebuild before starting the application:

```sh
# First terminal, from the Vibration Watch directory:
git submodule update --init --recursive
make run DURATION=60

# Second Bash terminal, also from the Vibration Watch directory:
source ./setenv.sh
record
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
are also available in distributed execution and stay disabled in simulation.

## Read the current RMS measurement

Each Rust analyzer declares the fl64 measurement `vibration.rms` in `on_start`
and updates it after calculating the RMS of each input block. Values use the same
m/s² unit as the `Analysis.rms` output. Each component retains only its latest
value in OSE RAM, without a measurement history or restoration after restart.

After updating the framework submodule, rebuild and launch the application, then
read the measurements from a second Bash terminal:

```sh
# First terminal, from the Vibration Watch directory:
source ./setenv.sh
app-run

# Second terminal, from the same directory:
source ./setenv.sh
ose-ctl measures 'analyzer_*.vibration.rms' --watch
```

The report shows `analyzer_1` and `analyzer_2` separately. Omit `--watch` for a
single reading, or use `--interval 1 --count 10 --format jsonl` for ten readings.
These are snapshots of the latest reported values; intermediate publications
between readings are not retained.

## Run with parameter directory sync

Install the optional watcher once using the application's Python interpreter:

```sh
python3 -m pip install './deps/sdl-ose/ose[sync]'
make run PARAMS_SYNC=1 DURATION=60
make run PARAMS_SYNC=1 PARAMS_DIR=./my-parameters SCENARIO=normal DURATION=60
make demo-distributed PARAMS_SYNC=1 DURATION=60
```

The Python launcher accepts the same option:

```sh
python3 tools/vibration.py run --params-sync --duration 60
python3 tools/vibration.py run --params-sync ./my-parameters --duration 60
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

After sourcing the environment, `app-make run PARAMS_SYNC=1 DURATION=60` forwards
these Make options. The framework's generic `app-run` shortcut starts OSE directly;
use `make run` or `app-make run` to enable Vibration's managed synchronizer.

## Project resources

- `schemas/`: contracts and units; `vibration_signal.sdl` avoids shadowing Python's `signal` module.
- `components/`: reusable business implementations plus thin OSE handlers.
- `config/`: machine definitions, block sizes, seeds and thresholds.
- `model.json`: ports, graph, triggers and bounded QoS.
- `deployment.local.json`: ordinary OSE execution through the broker.
- `deployment.cluster.json`: distributed execution, direct signal routes, broker results and placement resources.
- `scenarios/`: deterministic profiles; `tests/`: business, end-to-end and opt-in cluster tests.
- `packaging/` and `deploy/`: image construction and cluster deployment instructions.

The alarm inputs are independent and optional: losing one motor's branch does
not stall the other. Every analysis produces an observation with state 0 or 1;
this application does not retain an alarm history or implement debouncing.

## Change settings while running

Each machine has two typed SDL parameter blocks:

| Path | Fields | Reader |
| --- | --- | --- |
| `machines/1/sensor`, `machines/2/sensor` | `samples`, `sample_rate_hz`, `frequency_hz`, `amplitude`, `noise`, `seed`, `ramp_per_second` | Corresponding C sensor |
| `machines/1/detection`, `machines/2/detection` | `rms_threshold` | Python alarm policy |

`config/machines.json`, `config/thresholds.json` and the selected signal profile
provide the initial values when building. The alarm and sensors read generated
typed bindings at every callback. Machine identity, graph, tick period and queue
policies remain deployment/model configuration.

In a first Bash terminal, start a normal run so changes are easy to observe:

```sh
source ./setenv.sh
app-run SCENARIO=normal
```

In a second Bash terminal, source the application environment again. It selects
the pinned framework and loads the local operator token automatically:

```sh
source ./setenv.sh
params-list
params-get                            # All four blocks
params-get '^machines/1/'              # Both blocks for motor 1
params-get '/detection$'               # Both alarm thresholds
params-get --exact machines/1/detection
python3 tools/vibration.py set-threshold --machine 1 --rms-threshold 2.0
params-watch machines/1/detection --count 2
```

`params-get` takes a Python regular expression by default and returns one JSON
object mapping each selected path to its complete block. Omitting the pattern
selects all blocks; `params-get-all` is a synonym. Use `--exact` for a literal path and quote
patterns to prevent shell expansion. Changing a threshold affects subsequent
observations in the first terminal; stop a run or a watch with Ctrl+C.

The `operator` client has read/write rights on all four blocks; application
components have read access only. `source ./setenv.sh` loads
`OSE_PARAMETER_TOKEN` from `build/plan.json`, or from `deployment.local.json`
before a build. The local development token is declared in that deployment.
An explicit environment token is preserved. Set `OSE_PARAMETER_CLIENT` before
sourcing to choose a different configured client.

To change a sensor, export only its settings, edit the JSON, and submit it with
the application's business validation:

```sh
params-get --exact machines/1/sensor > build/sensor-1.json
# Edit the seven fields under "machines/1/sensor" in build/sensor-1.json.
python3 tools/vibration.py set-sensor --machine 1 --file build/sensor-1.json
```

`set-sensor` accepts this single-block export or the original bare settings
object. Exports for another machine or multiple blocks are rejected.

For a backup and restore, the path-keyed format works for one or several blocks:

```sh
params-get > build/parameters.json
params-set --file build/parameters.json
# Reinject only the selected thresholds through stdin:
params-get '/detection$' | params-set --file -
# Submit an explicit path-keyed threshold value:
printf '%s\n' '{"machines/1/detection":{"rms_threshold":0.1}}' | params-set --file -
```

Exports can also be split across files and restored recursively:

```sh
mkdir -p build/parameters/motors
params-get '^machines/1/' > build/parameters/motors/motor-1.json
params-get '^machines/2/' > build/parameters/motors/motor-2.json
params-set --dir build/parameters
```

`--dir` reads all `*.json` files, including files in subdirectories, in relative
filename order. Every file contains path-keyed exports; filenames do not select
parameter paths. Duplicate paths, invalid files or invalid blocks stop the import
before the first write. `--dir` cannot be combined with `--file` or a positional
path. `--exact` selects a literal path for `params-get` and `params-sync`;
it is not an import option for `params-set`.

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
The generic `params-set` / `ose-ctl parameters set` commands check SDL types;
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
updates using `ose-ctl run --mode simulated --scenario FILE`, as described in the
[framework parameter guide](deps/sdl-ose/ose/docs/parameters.md).

The same commands accept `--host`, `--port` and `--plan`; use
`--plan build/replay/plan.json --endpoint build/replay/endpoint.json` for a local
replay application (only detection blocks are retained there). Distributed clients
use `--distributed --tls-ca build/pki/ca.crt --tls-cert build/pki/operator.crt
--tls-key build/pki/operator.key` with an explicit host and port. `make certs`
generates the development operator credentials. Cluster access authenticates the
`operator` certificate identity; it does not use the local development token.
The ordinary `ose-ctl parameters get PATH --client operator` still returns a
bare block for existing scripts; the shell shortcut returns a path-keyed export.
See [cluster deployment](deploy/README.md) for credentials and connectivity.

## Replay captured signals

The pinned framework provides `ose-ctl replay`. During framework development,
set `FRAMEWORK_ROOT=/absolute/path/to/sdl-ose` to select another checkout. In that
case, source that checkout's `setenv.sh` from the application directory instead
of the application environment below.

Start the application, then capture both sensors in another terminal using the
same framework and Python interpreter:

```sh
make run DURATION=60
# Second terminal, from the application directory:
source ./setenv.sh
record --command 'select sensor_1.signal sensor_2.signal' \
  --command 'start signals.pcapng' --command 'wait 2' --command stop
```

Replay the signals through the Rust analyzers, Python alarm policy and observer:

```sh
make replay RECORDING=signals.pcapng DURATION=3 WORKERS=4
make replay RECORDING=signals.pcapng DURATION=6 LOOPS=2
make test-replay
```

`replay` builds a separate model under `build/replay/`: the C sensors are removed
and their subscriptions are connected to external topics. The workflow invokes
`ose-ctl replay` with `sensor_1.signal=replay.signal_1` and
`sensor_2.signal=replay.signal_2`. It accepts PCAPNG or MCAP captures; both sensor
topics must contain messages. `tools/vibration.py replay --recording FILE`
also accepts `--start-ns` and `--end-ns` for a recorded log-time range.
`LOOPS` (CLI: `--loops`) is the total number of passes, defaulting to 1; 0 repeats
until the global `DURATION` limit. Components retain state between passes, and
payload timestamps and sequence numbers are preserved.

The first selected message becomes virtual time zero; recorded intervals and
payloads are preserved. `DURATION` is the virtual horizon from that origin;
choose it long enough to cover the selected capture. Terminal observations are
recomputed from the samples, including the original payload sequence numbers
and timestamps. The logical trace is saved as `build/replay/trace.jsonl`.

For live replay, build the model without sensors and start it separately:

```sh
make replay-build
ose-ctl run --plan build/replay/plan.json
# In a second terminal with the same framework environment:
ose-ctl replay signals.pcapng --mode realtime --plan build/replay/plan.json \
  --map sensor_1.signal=replay.signal_1 --map sensor_2.signal=replay.signal_2 \
  --speed 1 --interactive
```

Use `speed 0.5`, `speed 2`, `pause`, `resume`, `status` and `stop` in the replay
console. Speed 0 runs as quickly as admission permits. Application timestamps
stay unchanged; changing playback speed changes the spacing between publications.
For a scripted session, replace `--interactive` with repeated options such as
`--command 'speed 2' --command 'wait 3' --command status --command stop`.
Commands run in order; `wait` uses wall time, and the end of the script stops
replay. A capture that finishes earlier cancels the remaining commands.
Add `--loops N` for N passes or `--loops 0` for continuous live replay. In the
latter case, commands, signals or an optional global `--duration` stop playback.

`make replay-build PROFILE=cluster` builds the four-worker distributed version.
Start its coordinator and workers using `build/replay/plan.json`, as described
in the [framework's distributed guide](deps/sdl-ose/ose/docs/distributed.md).
Feed it with the same replay command plus `--host HOST --port PORT` and the TLS
credentials of a client certificate named `replay`. For a loopback development
cluster, use `--host 127.0.0.1 --port 7000 --insecure`. External signal routes use
the coordinator. Live replay uses the running application's state and ordinary
queue policies; increasing speed can cause `drop_oldest` to discard inputs if
the consumers cannot keep up. Check runtime counters when selecting a rate.

`make test-replay` prepares the optional recording dependency, captures a live
two-second signal sequence, and checks that replay reproduces every corresponding
live observation, including RMS, peak and alarm state. It compares simulation
traces from one and four workers, then checks live local and distributed replay.
This test uses temporary captures; it does not consume
existing recordings or require a cluster. It is separate from `make test` so the
ordinary suites keep their standard-library-only Python requirements.

## Cluster deployment

See [deployment instructions](deploy/README.md) and [architecture](docs/architecture.md).
The image build compiles one component per image, with its generated container,
SDL runtime, native recording filter service and OSE adapter. Cluster images are referenced by digest.
Distributed execution is real time; deterministic simulation remains local.

No images are published and no cluster is modified by `make test`.
The GitHub workflow runs the same checks. If the framework repository is private,
configure a read-only `FRAMEWORK_ACCESS_TOKEN` that can fetch its pinned revision.
