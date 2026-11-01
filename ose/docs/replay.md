# Recording replay

`ose-ctl replay` feeds selected PCAPNG or MCAP messages into a compiled model's
external topics, either in deterministic simulation or through a running local
broker or distributed coordinator. Use it to reproduce an incident or test a
component change with captured inputs. Simulation is the default mode.

Build a model with [external inputs](clients.md#feeding-a-model-input), then run:

```sh
ose-ctl replay signals.pcapng --plan build/plan.json \
  --map sensor_1.signal=external.signal_1 \
  --map sensor_2.signal=external.signal_2 \
  --duration 3 --workers 4 --trace build/replay.jsonl
```

Each `--map RECORDED_TOPIC=EXTERNAL_TOPIC` selects one recorded topic and assigns
it to a declared external topic. Other recorded topics are ignored. Internal
component outputs cannot be targets: use a model without the original producers
and connect the retained consumers to external topics. A source or destination
may appear only once in the mappings. Each mapped source must have at least one
message in the selected range.

## Live replay and speed control

Start the application with external inputs before replaying in real time:

```sh
ose-ctl run --plan build/plan.json
# In another terminal:
ose-ctl replay signals.pcapng --mode realtime --plan build/plan.json \
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

With `--interactive`, enter `speed N`, `pause`, `resume`, `wait SECONDS`, `status`
or `stop`. Commands can also be supplied through stdin with a pipe or file.
Changing speed reanchors the clock at the current playback position. Pause
freezes playback time; an in-flight publication may still complete. `speed 0`
means maximum speed; use `pause` to suspend. EOF stops playback. Ctrl+C/SIGTERM
closes the publisher with exit status 130; the application continues running.

Repeat `--command` to run an ordered script without reading stdin:

```sh
ose-ctl replay signals.pcapng --mode realtime --plan build/plan.json \
  --map sensor_1.signal=external.signal_1 \
  --command pause --command 'wait 1' --command 'speed 2' \
  --command resume --command 'wait 3' --command status --command stop
```

`wait` uses wall-clock seconds, independently of playback speed and pause state.
Leading commands such as `pause` and `speed` are applied before the first
publication. The end of the script stops replay; it does not switch to stdin,
including when `--interactive` is also specified. Include a `wait` command to
allow messages to play between controls. If the capture finishes earlier, the
remaining script is cancelled. Invalid scripted commands stop replay with exit
status 1; interactive command errors are reported without closing the console.

For distributed execution, use the same compiled application plan and connect
to its coordinator with a CA-signed client certificate whose common name is
`replay`:

```sh
ose-ctl replay signals.pcapng --mode realtime --plan build/plan.json \
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
spans the earliest to latest selected log timestamp; the next pass starts one
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

Messages are ordered by recorded **log time**, with file order breaking ties.
The earliest selected message becomes playback time zero; intervals retain their
exact integer nanoseconds. Payload bytes, including application timestamps and
sequence numbers, remain unchanged. OSE recorder log times are recorder arrival
times, rather than synchronized production times.

`--start-ns N` and `--end-ns N` restrict absolute recorded log timestamps to
`[start, end)`. The origin is recalculated from the earliest remaining message.
`--duration` is the global recorded-time horizon in seconds, starting at that origin
and applying across all passes.
It is required in simulation and optional in real time, where omission replays
all requested passes. Events at or after the horizon are excluded. Simulation
with `--loops 0` repeats inputs until this finite horizon; it does not start an
unbounded simulation.
Choose a horizon long enough to include all intended passes and any input
timeouts you want to exercise in simulation. Finite live playback finishes after
the final pass's last selected publication; its wall-clock duration depends on
speed, pauses and backpressure.

## Validation and results

Before starting simulation components or opening a live publisher, replay scans the complete container for integrity,
validates selected payloads, and checks their root types and catalogues
against the external topics. The default `--schema-policy exact` requires the
declared catalogue. Use `--schema-policy compatible` for compatible schema
evolution, as described below. Missing topics, empty
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
It also covers live pacing, interactive and scripted controls, local and distributed
publications, TLS identities, cancellation, refusals and generation fencing.

## Compatible recorded schemas

Use an older capture with a compatible current contract by selecting:

```sh
ose-ctl replay old-signals.pcapng --plan build/plan.json \
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
log timestamp and the SDL diagnostic with its field path. Temporary adapted data
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
