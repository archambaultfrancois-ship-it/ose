# Recording replay

`ose replay` feeds selected PCAPNG or MCAP messages into a compiled model's
external topics, either in deterministic simulation or through a running local
broker or distributed coordinator. Use it to reproduce an incident or test a
component change with captured inputs. Real-time replay is the default mode and
publishes into an already running application.
Use `--mode simulated` to start the application with a virtual clock.
`--duration` is optional in both replay modes. Without it, simulation has no
virtual time limit: EOF ends injections for the requested passes, then callbacks
and timers continue until no event remains. Periodic timers or feedback can keep
it running indefinitely; use `stop`, Ctrl+C, or an explicit `--duration` to bound it.

Replay timing and range selection use `publish_time_ns`, the source deposit
time, rather than `log_time_ns`, the recorder arrival time. Real captures use
Unix time; simulated captures use virtual nanoseconds. The first
selected source timestamp is position zero (`origin_publish_time_ns` in the
summary). For real captures, source system clocks must be synchronized across
the cluster; residual uncertainty is accepted. Timestamp ties retain file order for deterministic
replay. Payload timestamps are unchanged. There is no fallback to recorder
arrival timestamps.

Build a model with [external inputs](clients.md#feeding-a-model-input), then run:

```sh
ose replay signals.pcapng --mode simulated --plan build/plan.json \
  --map sensor_1.signal=external.signal_1 \
  --map sensor_2.signal=external.signal_2 \
  --duration 3 --workers 4 --trace build/replay.jsonl
```

`--map` is optional. Without it, replay selects recorded messages whose topic
names exactly match external topics declared in the plan, preserving those names.
Other recorded topics and external topics absent from the selected recording range
are ignored. If no messages match, replay fails before starting components or
publishers and suggests using `--map`. Schema validation still applies.

```sh
ose replay signals.pcapng --interactive
ose replay signals.pcapng --mode simulated --duration 3
```

Each `--map RECORDED_TOPIC=EXTERNAL_TOPIC` selects one recorded topic and assigns
it to a declared external topic. Other recorded topics are ignored. Internal
component outputs cannot be targets: use a model without the original producers
and connect the retained consumers to external topics. A source or destination
may appear only once in the mappings. Each mapped source must have at least one
message in the selected range.

Replay preserves each recorded `ose.app_id` as the optional application ID in
simulation and live local/distributed publication. Zero, uint64 maximum and
duplicate IDs retain their meaning. Time sorting, schema adaptation and repeated
loops do not change application IDs. Replay still creates fresh engine/transport
identities for each publication, including each loop, so equal application IDs
are never transport duplicates. Old recordings without this metadata remain
readable and publish without an application ID; none is inferred from the
container sequence or payload. A malformed selected application ID fails
preflight before components or live publishers start.

## Record during replay

Use `--record 'TOPIC_GLOB[, FILTER]'` and optionally `--record-file FILE` to
capture selected points while replaying, in either mode:

```sh
ose replay inputs.mcap --mode simulated --duration 60 \
  --record 'analyzer_*.vibration, rms > 0.5' --record-file results.mcap
```

Use `--record-from 10s --record-until 20s` for the relative playback window
`[10s, 20s)`, in either mode. In live replay it follows speed, pauses and steps;
it is independent of the absolute input selection `--start-ns` / `--end-ns`.
Recorded timestamps keep their original clock reference.

The default output is `capture.pcapng`; existing output is overwritten. The
source capture cannot also be the output file. Repeated clauses are combined
by OR without duplicate capture of one point. See [batch capture](recording.md#batch-capture-with-run-and-replay)
for selection rules, startup/finalization and clock semantics. This also enables
replaying a simulated capture in real time or another simulation; recorded
microstep indices are descriptive metadata and are not replay scheduling inputs.

## Live replay and speed control

Start the application with external inputs before replaying in real time:

```sh
ose run --plan build/plan.json
# In another terminal:
ose replay signals.pcapng --mode realtime --plan build/plan.json \
  --endpoint build/endpoint.json --map sensor_1.signal=external.signal_1 \
  --speed 2 --interactive
```

`--speed 1` preserves recorded intervals, `0.5` runs twice as slowly, `2` runs
twice as fast, and `0` publishes as quickly as admission permits. A monotonic
clock and absolute deadlines avoid adding publication time to every interval.
The final JSON reports publication and admission counts, elapsed wall time and
current/maximum scheduling lag. Lag above 50 ms is also reported to stderr at
most once per second when publishing. Queue policies still apply; messages can
be replaced or refused under load. Admission confirms RAM receipt, rather than
callback completion. Refusals produce exit status 1; timed-out publications are
not retried automatically because their outcome may be unknown.

## Execution controls: replay and simulated run

One command language controls `ose replay` in both modes and `ose run --mode
simulated`. **Execution controls are rejected by `ose run --mode realtime`**:
there is no global pause or stepping mechanism for a real-time application.
Live replay controls its own injections into an application which keeps running.

Without controls, execution starts automatically. `--interactive`, repeated
`--command` or `--command-file` start **paused at position zero**. Simulation
initializes its component processes before reading commands; no input callback
runs until execution advances. Live replay connects its publisher first.

| Command | Meaning |
| --- | --- |
| `pause` | Suspend progression; acknowledge an in-flight admission or simulated callback wave before reporting status. |
| `resume` | Continue automatic execution from the current position. |
| `step [N]` | Settle the current instant, advance to `N` future event instants and pause at the last one. Default: `1`. |
| `step DURATION` | Execute a relative temporal interval and pause at its exclusive endpoint. A time unit is required. |
| `step-time DURATION` | Compatibility alias for the temporal form of `step`; bare numbers still mean seconds in this alias. |
| `step-messages [N]` | Replay only: inject `N` individual recorded messages, including timestamp ties. Default: `1`. |
| `microstep [N]` | Simulation with `--microsteps` only: execute up to `N` logical callback stages without advancing physical virtual time. Default: `1`. |
| `break-time TIME` | Arm a one-shot breakpoint at an absolute execution time. |
| `breakpoints` | List armed breakpoint times and current status. |
| `clear-break-time TIME` | Remove the breakpoint at that absolute time. |
| `clear-break-time all` | Remove all armed breakpoints. |
| `speed N` | Live replay only: set pacing speed; `0` means maximum speed. |
| `wait DURATION` | Wait in wall-clock time before reading the next command; does not itself advance or resume execution. |
| `status` | Report position, mode, pause state, counters, breakpoints and last step result. |
| `stop` / `quit` | End the session and release its resources. |
| `help` / `?` | Print the command list. |

A bare argument to `step` is always a positive integer count: `step 2` means two
future instants, not two messages or two seconds. A duration requires `ns`, `us`,
`ms`, `s` or `min`: `step 100ms`, `step 1.5s`, `step 2min`. `step 1.0` and
`step 1e2` are rejected to avoid interpreting a unitless decimal as a duration.
Counts for `microstep` and `step-messages` must also be positive integers.

Other duration-taking commands accept bare numbers as seconds. Conversion uses
integer nanoseconds, rounds halves upward and clamps positive sub-nanosecond
values to one nanosecond. Durations must be finite and within uint64 nanoseconds;
relative steps must also have an in-range absolute target. `wait` and breakpoint
times accept zero; temporal steps require a positive duration. Blank lines and
lines starting with `#` are ignored.

**Command migration:** `step N` previously counted replay messages. That behavior
is now named `step-messages N`; scripts that rely on individual-message budgets
must use the new name. `step-time 100ms` continues to work as `step 100ms`.

### Stepping by event instants

In **simulation**, `step N` finishes the immediate callback stages at the current
time, then visits future event instants in chronological order. An event instant
may contain scenario/recording inputs, timer expirations, inactivity timeouts or
pending-admission deadlines. It is counted once regardless of how many events
share that time. All intermediate instants execute normally. At the final instant,
external inputs and due admissions settle, then execution pauses **before selecting
callbacks**, with microstep zero. A following `microstep` can inspect the reactions
there; another `step` settles that instant before advancing farther.

In **live replay**, `step N` injects any remaining messages at the current position,
then advances through `N` later distinct recorded timestamps, injecting the entire
final timestamp group before pausing. Gaps are skipped and injections run as fast
as admission allows, independently of `speed`. A group at or behind the starting
position does not count as a future instant. Application timers are outside the
replay controller and cannot define these steps.

For example, from position zero, a capture with messages at 0, 100, 100 and 200 ms
has its first future message instant at 100 ms. `step` injects the messages at
0 and 100 ms and pauses at 100 ms. In simulation, the 0 ms callbacks have run,
while the 100 ms callbacks have not. In real time, callbacks may already be
processing those inputs. A simulated timer at 50 ms would instead make 50 ms the
first future event instant for simulated `step`.

### Stepping by a duration

`step DURATION` starts at the acknowledged position `t` and executes the interval
**`[t, t + DURATION)`**. Inputs, timers and admissions exactly at the endpoint
remain for the next command. An empty interval still advances the position,
unless a terminal condition is reached first. This exclusive boundary differs
from an instant step, which admits external inputs at its destination before
pausing.

In **live replay**, temporal steps are paced at the selected speed: `step 1s` at
`speed 2` takes approximately half a real second, plus admission lag. At `speed 0`,
they run as fast as admission permits. The position freezes at the exact endpoint
even if an in-flight admission finishes late. Resume reanchors the pacing clock
there, without catching up for time spent paused.

In **simulation**, timers, callbacks and admissions before the endpoint execute
as fast as calculations permit. Virtual time and new callbacks remain suspended
while paused; processes and application state stay alive. Speed control is not
available. Initial state and deterministic application code still determine
reproducibility.

### Individual messages and microsteps

`step-messages N` is available only for replay. It counts attempted external
recorded publications, including refusals, across loop boundaries. Internal
publications and callbacks do not count. Live replay skips recorded gaps and
waits for admission results, not for downstream processing.

Simulation runs intervening timers and settles immediate callback stages at the
last injected timestamp. If the budget splits a same-timestamp input group, the
selected messages are admitted but callbacks at that timestamp wait for the rest
of the group. `pending_timestamp_group: true` reports this state. A subsequent
instant, temporal or microstep command completes that group before callbacks run.
Deferred virtual admissions may remain at a command boundary; status includes
`pending_external`.

`microstep N` requires simulated execution launched with `--microsteps`. It first
admits all external inputs at the current instant, then runs up to `N` stages.
Each stage selects at most one callback per ready component, runs the selected
wave, and releases its publications at the next logical boundary before pausing
or continuing. Physical virtual time stays fixed. If no callback is ready, the
command reports `blocked` with the number of stages actually executed; it never
jumps to a future timer or input. Use `step` or `step DURATION` to advance time.

The `--max-microsteps` bound is cumulative at a physical timestamp, including
across commands. Pausing or issuing another `microstep` does not reset it. A
new physical timestamp resets the microstep index. Equal successive payloads
still do not imply convergence; stepping cannot bypass the feedback limit.

### Temporal breakpoints

`break-time TIME` uses an **absolute** position from execution origin, rather
than a relative duration. For replay, origin is the earliest selected recorded
message; `--start-ns` changes that origin. For simulated run, origin is virtual
zero. Breakpoints are global and do not restart at loop boundaries.

```text
break-time 5s
break-time 12.5s
breakpoints
resume
```

Execution pauses at the earliest armed time **before external admissions, timers
or callbacks at that instant**, even when no message has that exact timestamp.
Simulation can also reach an armed breakpoint when no other future work remains;
without that breakpoint it would stop as quiescent. Live replay remains bounded
by its selected capture and duration. Pausing live injections does not freeze the
application's real-time timers or callbacks.

A breakpoint interrupts `step`, `step-messages` or `microstep` at the current
time as well as automatic execution. If it coincides with a temporal step's
endpoint, the completion reason is `breakpoint`. The pending step is acknowledged
with its partial results and is finished: resume does not restore its remaining
budget. Issue a new step to request another budget. The global exclusive duration
limit and normal end of live replay take precedence over breakpoints at or beyond
those terminal boundaries.

A breakpoint is disarmed when hit, allowing `resume` or another step to process
the paused instant without repeatedly hitting it. Multiple armed times are
supported; adding the same time twice does not duplicate it. A past time, or the
current time after its admissions/callbacks have begun, is rejected. Nothing is
rewound. Use `clear-break-time 12.5s` or `clear-break-time all` to remove armed
times. Breakpoint hits during automatic playback emit a JSON notification even
when no step command is awaiting completion.

### Limits, console and scripts

`--duration`, when supplied, remains an exclusive global limit. Live replay also ends after its
last selected publication; for a temporal step its final boundary is one
nanosecond after the last publication of the last pass. Simulation can continue
processing timers after the recording ends and terminates at an explicit horizon or when
no immediate or future event (including an armed breakpoint) remains. A command
reaching a terminal condition reports its actual position and completed budget.

Controls do not change logical input ordering. With the same initial state,
inputs and horizon, stepping to the same normal termination produces the same
logical trace as continuous execution; explicit `stop` truncates it. Breakpoints
and acknowledgements remain outside the JSONL trace. An armed breakpoint in an
otherwise quiescent tail can extend the final reported stop time, but does not
create application work.

Commands execute serially: a step is acknowledged before the next command is
read. Interactive errors leave the console available; EOF stops the session.
Ctrl+C/SIGTERM can interrupt a long step. Live replay releases its publisher with
exit status 130 while the target application continues running; simulation uses
its normal bounded process cleanup.

Repeat `--command` without reading stdin:

```sh
ose replay signals.pcapng --map sensor_1.signal=external.signal_1 \
  --command 'break-time 5s' --command 'step 10s' \
  --command 'step-messages 2' --command status --command stop
```

The same controls apply to simulated run:

```sh
ose run --mode simulated --duration 60 --microsteps \
  --command 'break-time 5s' --command 'step 10s' \
  --command 'microstep 2' --command step --command stop
```

A UTF-8 `--command-file` supplies one command per line and is mutually exclusive
with `--command`. Either script source takes precedence over `--interactive`;
stdin is not read. For example:

```text
# stop at a selected instant and inspect its first two logical stages
break-time 5s
step 10s
microstep 2
status
stop
```

```sh
ose replay signals.mcap --mode simulated --microsteps \
  --map sensor_1.signal=external.signal_1 --duration 60 \
  --command-file commands.txt --trace build/replay.jsonl
```

Scripts start paused. Add `resume` before `wait` when automatic execution is
intended: `--command 'wait 3'` alone waits three real seconds while paused, then
stops. A step waits for completion; `resume` returns immediately so a subsequent
`wait` lets execution proceed. Script completion stops the session. Invalid script
commands abort with exit status 1. Normal termination cancels remaining script
commands. Scripts run once across all replay loops.

Every completed nonblank command prints JSON status to stderr; stdout contains
the final summary. Status includes exact `position_ns`, `paused`, `pause_reason`,
`breakpoints_ns`, `last_breakpoint_ns` and `last_command`. The last step result
contains its original command, `kind`, positions before/after, actual message and
callback counts, `completed_units`, per-destination `delivery_results` and a
completion reason (`step`, `time`, `microstep`, `blocked`, `breakpoint` or a terminal
reason). A breakpoint result also includes `breakpoint_ns`. Live status reports
pacing and lag; simulated status reports virtual time, ticks, waves, microstep
when enabled, pending work and timestamp groups. Fan-out can yield several
admission results per message; deferred admissions may finish in a later command.

## Distributed live replay

For distributed execution, use the same compiled application plan and connect
to its coordinator with a CA-signed client certificate whose common name is
`replay`:

```sh
ose replay signals.pcapng --mode realtime --plan build/plan.json \
  --host coordinator.example --port 7000 --distributed \
  --tls-ca ca.crt --tls-cert replay.crt --tls-key replay.key \
  --map sensor_1.signal=external.signal_1 --speed 1
```

The plan's execution mode also selects distributed transport automatically.
`--insecure` replaces TLS only for explicit loopback development. External
subscriptions must use broker routing; internal routes can remain direct.
The coordinator checks plan identity and exclusive topic ownership. A new
publisher generation fences the previous one. Accepted records may drain after
disconnect; opening a new publisher clears remaining broker records for its
topics. Replay does not participate in the component READY barrier; it waits up
to 20 seconds for registered workers to report that their processing loops have
started before publishing. Only
declared external topics are writable. The `broker.external.enabled` and
`max_clients` settings control access and session capacity.

Live replay does not start or reset application components. `--endpoint` selects
a local endpoint JSON; otherwise the local plan's `endpoint.json` is used when
present. `--host`/`--port` select an explicit address and cannot be combined with
`--endpoint`. `--workers` and `--trace` are simulation-only.

Replay uses the SDL checkout embedded in the compiled plan. Install its optional
recording dependency with `make -C "$SDL_ROOT" recording-deps PYTHON=python3`,
using the interpreter running OSE. No recording filter service is needed.

## Timing and selection

`--loops N` sets the **total number of passes**, defaulting to `1`. Use `0` for
unlimited repetition, until a stop command, signal, script completion or duration
limit. For example, add `--loops 3` to replay the selection three times, or
`--loops 0 --interactive` to keep replaying under console control.

Each pass uses the same selection and original payload bytes, including sequence
numbers and application timestamps. Only scheduling timestamps advance. A pass
spans the earliest to latest selected source publication timestamp; the next pass starts one
nanosecond after the previous pass's final timestamp. A zero-span capture also
advances by one nanosecond per pass. Recorded intervals within a pass remain
unchanged and playback speed applies across loop boundaries.

Components and publisher connections stay alive between passes. Application
state continues, publication identities stay unique, and a command script runs
once across the whole replay. Repetition reuses the preflighted envelopes without
expanding payload storage or event metadata for every pass. Live `status` and the
final JSON report the requested `loops` and `completed_loops`; live status also
shows `current_loop`. Counts describe publications, rather than completed
downstream processing.

Messages are ordered by recorded **source publication time**, with file order breaking ties.
The earliest selected message becomes playback time zero; intervals retain their
exact integer nanoseconds. Payload bytes, including application timestamps and
sequence numbers, remain unchanged. Recorder arrival times are retained for
diagnostics and do not determine replay timing.

`--start-ns N` and `--end-ns N` restrict absolute source publication timestamps to
`[start, end)`. The origin is recalculated from the earliest remaining message.
`--duration` is the global recorded-time horizon in seconds, starting at that origin
and applying across all passes.
It is optional in both modes. Events at or after an explicit horizon are
excluded. Without it, all requested passes are injected, and simulation continues
until quiescence or an explicit stop; the last message is not an implicit horizon.
`--loops 0` is also allowed without a duration and repeats until interrupted or
stopped. The virtual clock remains an unsigned 64-bit nanosecond clock: unlimited
means no configured time horizon, not an extension of its numeric range.
Choose a horizon long enough to include all intended passes and any input
timeouts you want to exercise in simulation. Finite live playback finishes after
the final pass's last selected publication; its wall-clock duration depends on
speed, pauses and backpressure.

## Validation and results

Before starting simulation components or opening a live publisher, replay scans the complete container for integrity,
validates selected payloads, and checks their root types and catalogues
against the external topics. The default `--schema-policy exact` requires the
declared catalogue. Use `--schema-policy compatible` for compatible schema
evolution, as described below. Missing explicitly mapped topics, empty
selections, oversized envelopes and invalid recordings fail explicitly.

Prepared envelopes are written to temporary files, fingerprinted and checked
again when injected. Temporary data is removed on completion or failure. RAM
stores event metadata rather than the complete capture; temporary disk space is
proportional to selected payloads.

The final JSON contains execution counters and a `replay` selection summary
with mappings, origin, loop period and selected message counts per pass. Selection
counts include messages beyond the execution horizon; execution counters count
all actual publications across passes. `--trace` writes the usual logical trace
and payload fingerprints in simulation; `inspect --last` reads its final status.
Simulation callbacks start with fresh component state. Reproducible outputs require
deterministic application code and the same initial state and parameters, as
described in [simulation](simulation.md). Replay uses normal queue policies, so
a bounded input can still drop messages or refuse admission.

`make test-recording` includes replay tests for both formats, nanosecond timing,
topic mapping, validation failures and identical traces across worker counts.
It also covers live pacing, interactive and scripted controls in both modes,
message budgets, exclusive temporal boundaries, command files and console EOF.
Stepped simulations are compared byte-for-byte with continuous traces across
worker counts, same-time input groups, timers and microsteps. Local and distributed
publications, TLS identities, cancellation, refusals and generation fencing are
covered as well.

## Compatible recorded schemas

Use an older capture with a compatible current contract by selecting:

```sh
ose replay old-signals.pcapng --mode simulated --plan build/plan.json \
  --map sensor_1.signal=external.signal_1 --duration 3 \
  --schema-policy compatible
```

The option applies to all mappings, in simulation and live local/distributed
replay, for PCAPNG and MCAP. It requires no historical `.sdl` files or generated
bindings: the capture contains the source catalogue and the plan contains the
target catalogue. Omitting the option, or selecting `--schema-policy exact`,
keeps strict replay.

Compatibility is directional: the recorded catalogue is the publisher and the
declared external topic is the consumer. SDL checks the selected root with
`require_required=True`. The root name must stay the same. Common fields match by
SDL field ID, allowing field renames. Extra source fields are discarded; missing
target optional fields become absent and missing target repeated/packed fields
become empty. Missing required target fields, incompatible types, dimensions or
cardinality, and source enum values unknown to a retained consumer field are
rejected. The whole contract is checked, even when the capture happens not to use
an incompatible enum value. Unrelated message declarations need not match.
Business migrations such as unit conversion or renaming a root are not provided.

For example, a capture of `Measurement` with fields `1:sequence`, `2:old_value`
and `3:legacy_label` can feed a current `Measurement` contract containing the same
types at IDs 1 and 2, renamed to `sequence` and `value`, and an optional field at
ID 4. The legacy label is removed and the new optional field is absent.

Each selected payload is validated against its source catalogue, decoded into the
target types and reencoded with the target catalogue before injection. Both its
original and adapted envelope must fit the component frame limit. An identical
catalogue uses the original bytes. A different compatible catalogue can change
payload bytes, message IDs and representation, so replacing only the envelope
header would be incorrect. Values are transferred through SDL objects, without a
JSON round trip. Lossy target wire representations apply their normal SDL
quantization; compatibility does not promise identical numerical values across
different quantization settings.

Several catalogue versions for the same recorded topic are checked independently.
An LRU cache retains at most 64 prepared adapters and keys them by full catalogue
texts and root, rather than the short wire catalogue ID. All selected messages
are prepared before starting simulation components or opening a live publisher.
An incompatibility reports source/target topics, root, selected-message position,
source publication timestamp and the SDL diagnostic with its field path. Temporary adapted data
is removed on success or failure.

Scheduling timestamps, timestamp tie ordering, selection and loop behavior stay
the same. Simulation traces and fingerprints describe the adapted envelopes that
were actually injected. Payload timestamp and sequence fields are transferred as
ordinary retained SDL fields; source fields absent from the target are discarded.

The final `replay` summary adds `schema_policy` and `schema_adaptation`, containing
`unchanged_messages`, `adapted_messages` and a `catalogues` list. Each entry names
the source/target topics, root, full catalogue SHA-256 fingerprints, adaptation
(`exact` or `adapted`) and message count. These are selection counts for one pass,
including selected messages beyond the execution horizon; loops do not multiply
them. `unchanged_messages` counts identical catalogues, even if a conversion
between different catalogues could coincidentally keep the same payload bytes.
