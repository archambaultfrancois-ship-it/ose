# Deterministic simulated execution

`simulated` is a functional execution engine for the same compiled graph and
Python, C, Rust, Java and Ada callback containers used by `realtime`. It runs real component
processes, selects their input activations centrally and advances a virtual clock without
waiting for wall time. The default engine remains `realtime`.

Application model v2 supports Python, Rust, C, Java and Ada containers with single-input
callbacks, passive Pull inboxes and explicit timer inputs. Its activation graph,
withdrawal commit and wave rules are described in the
[transition guide](cenv2-transition.md#activation-and-simulation) and
[CH/2 protocol](../sdk/input-protocol.md). There is no joint readiness or `on_tick`. Automatic timers first fire after
their declared delay.
Scenario arrivals at a timestamp are admitted before selecting activations and
can reset an inactivity deadline at that timestamp. A publication computed by
another callback becomes visible after its wave completes. It cannot cancel a
timeout already selected in the same wave; the timeout and subsequent message
callback may therefore both run at that timestamp. This ordering is independent
of worker count. Optional microsteps extend these wave rules to activating
feedback cycles without changing the model or protocols.
Ada v2 wrappers pass their [qualification suite](ada-inputs.md) with GNAT 12.
The full five-language example produces identical traces with 1/2/8 workers;
the all-Ada feedback example matches the other SDKs with 1/4 workers.

Given identical models, scenarios, executables, parameters and initial state,
successful simulations produce identical messages and byte-identical logical
traces for `--workers 1`, `--workers 2` and larger worker counts. This assumes
deterministic application code and a compatible execution environment. It does
not promise identical floating-point results across different platforms or builds.

## Run a simulation

Build normally, then select the execution engine at runtime. No model or
deployment field changes are required:

```sh
make build
PYTHONPATH=src python3 -m ose.cli run --plan build/plan.json \
  --mode simulated --duration 60 --workers 4 --trace build/simulation.jsonl
PYTHONPATH=src python3 -m ose.cli inspect --last
```

`--duration` is required and specifies a finite positive virtual horizon in
seconds. Events at the horizon are excluded: a duration of `1` executes events
in `[0, 1)`. When no immediate work or future event remains, execution ends
early with `stop_reason: "quiescent"`, listing empty activating message inputs.
An empty input without a timeout does not make the engine wait in real time.

`--workers` defaults to `1` and must be a positive integer. It limits concurrent
component calculations, using the separate Python, C, Rust, Java and Ada processes already
provided by the framework. It does not allocate CPU affinity or parallelize a
single callback. A sequential graph offers little parallel speedup.

`--scenario`, `--trace`, `--workers`, `--microsteps`, `--max-microsteps` and `--max-tick-output-bytes` are specific to
simulated execution. `--port` is rejected in simulated mode. No network broker,
adapter subprocesses or live external clients participate in this engine.
Only `inspect --last` is available after a simulation; live network inspection
continues to belong to the real-time engine.

## Virtual time and waves

The clock starts at zero after every component reports READY. Internal timestamps
are unsigned 64-bit nanoseconds. Decimal durations round to the nearest
nanosecond, with halves rounded upward and positive sub-nanosecond durations
clamped to one nanosecond. Nonfinite, negative and out-of-range times are rejected
before launching components. Automatic timers first expire after their declared positive delay.

At each virtual timestamp the coordinator:

1. Injects scheduled external messages and advances pending admissions.
2. Selects one eligible activation per ready component, in component-name order.
3. Freezes each selected origin and its eligible Pull handles before any callback.
4. Runs up to `workers` callbacks concurrently, collecting their staged outputs.
5. Commits each successful callback's withdrawals and collects its input-operation
   trace. Publications remain behind the wave barrier until every callback succeeds.
6. Makes outputs visible in component-name order, then emission order.
   Subscription admission uses subscription-ID order.
7. Repeats at the same timestamp while immediate work remains, or jumps to the
   next useful deadline or scenario event.

Wave membership and output visibility are independent of physical completion and
worker count. Connected components can be in the same wave if each already has
an eligible activation; they still cannot observe another member's staged outputs.

A callback has zero simulated execution cost. A slow computation or sleep can
increase physical runtime without advancing virtual time. Periodic timers retain
their phase; Push timeouts measure inactivity since reception. A timeout invokes
the input callback with an explicit expiration reference, even if a latest value
remains retained. Pull inputs never gate readiness.

## Microsteps and activating feedback

Enable the launch option for models with an activating cycle:

```sh
ose run --plan build/plan.json --mode simulated --microsteps \
  --duration .1 --workers 4 --max-microsteps 10000 --trace build/microsteps.jsonl
```

The boundary applies to all callback publications. Execution positions are
`(time_ns, microstep)`: callbacks selected in stage `k` see the messages admitted
before that stage; their publications become visible at boundary `k+1`. At most
one callback per component runs in each stage. Distinct ready components still
run concurrently, bounded by `--workers`; all calculations finish before outputs
are released in stable component/emission order. A component can activate again
at the same physical timestamp. A new stage never adds a physical delay.

Scenario messages and pending admissions settle before callback selection at
that timestamp. Receipt can cancel an inactivity timeout, while an independent
timer remains due. Inbox snapshots, FIFO/latest, IDs, deferred withdrawals and
per-callback failure cancellation retain their existing semantics. A successful
peer's committed consumption survives another callback's failure; a stage is
not a global transaction. Admission waits can advance to a future physical
virtual deadline when no callback is ready. Advancing time resets the step to
zero. The simulation horizon remains exclusive.

`--max-microsteps` requires `--microsteps` and must be a positive integer; its
default is 10,000 activation stages per timestamp. For a limit of `N`, stages
`0` through `N-1` may calculate. Their last publications can settle at boundary
`N`; failure occurs only if another callback is ready there. The bound also
applies to finite bursts of queued activations, not just feedback cycles. On
failure, the trace and `last-status.json` contain `stop_reason: "microstep_limit"`,
the position, a model activation cycle when present, recent activations, pending
callbacks and pending deliveries. The engine neither drops messages nor advances
time to escape the limit. Equal successive payloads do not imply convergence.

Models can be edited, validated and compiled with activation cycles. Without
`--microsteps`, simulated execution rejects these cycles before starting
components; real-time local and distributed execution also rejects them. The
option is available on `ose replay` in simulated mode and is rejected for
real-time execution/replay. There is no simulation field in the model or plan.

Only trace events and diagnostics expose `microstep`; CH/2 input references and
all SDKs remain unchanged. CENV/2 and dynamic per-message SDL contract exchange
are unchanged. Without the option, ordinary v2 trace bytes are preserved.
See the [activating feedback example](../examples/feedback-microsteps/README.md).

## Time in callbacks and protocol compatibility

Use the framework clock for time-dependent application logic:

| Language | Seconds | Exact nanoseconds | Component-local activation index |
| --- | --- | --- | --- |
| Python | `ctx.now()` | `ctx.time_ns` | `ctx.tick_index` |
| C | `ose_now(ctx)` | `ose_time_ns(ctx)` | `ose_tick_index(ctx)` |
| Rust | `ctx.now()` | `ctx.time_ns()` | `ctx.activation_index()` |
| Java | `ctx.now()` | `ctx.timeNs()` | `ctx.activationIndex()` |
| Ada | `Now (Ctx)` | `Time_Ns (Ctx)` | `Tick_Index (Ctx)` |

The timestamp is frozen throughout a callback and its publication calls. Tick
indices start at one. Context values are zero before the first tick and retain
the last tick's values during shutdown. In real-time execution, updated containers
receive elapsed monotonic time since their adapter was enabled. Activation origins
are local to each adapter; they are not a synchronized clock across components.

All five generated SDKs use [CH/2](../sdk/input-protocol.md), advertise READY
byte 0x02 and receive a single input reference per activation. Each reference
includes exact due time, elapsed deadlines, optional message metadata and the
message's own catalogue. Empty historical TICK frames are invalid for CH/2.
Regenerate containers when upgrading models and migrate handler signatures using
the [migration guide](model-v2-migration.md).

## External scenarios

To use a PCAPNG or MCAP capture instead of separate envelope files, see
[recording replay](replay.md) through `ose replay`.

A scenario is a version-1 JSON document:

```json
{
  "version": 1,
  "events": [
    {"at": 0.15, "topic": "external.offset", "envelope": "offset-1.s2ps"},
    {"at": 0.35, "topic": "external.offset", "envelope": "offset-2.s2ps"}
  ]
}
```

`at` is nonnegative virtual time in seconds. Topics must be declared in the model's
`external_topics`; scenarios cannot impersonate internal component outputs.
Envelope paths resolve relative to the scenario file. Each file contains a binary
S2PS/2 envelope with the declared topic's catalogue identity and root message.

A publication event may also specify `"app_id": 0` (any uint64 integer). Omission
means no application ID; null, booleans, strings and out-of-range values are
invalid. IDs may repeat and remain distinct from the unique `scenario:N` engine
identities. The trace includes `app_id` only when supplied.

This first version uses that exact declared catalogue, rather than loading
alternative publisher catalogues from scenario files.

The engine validates all inputs before starting components and processes events
by timestamp, then original file order. It retains file paths and SHA-256
fingerprints instead of keeping every future payload in RAM. Each envelope is
checked again when injected; changing an input file during a run fails explicitly.
Live network injection is unavailable because its arrival order would depend on
the physical environment.

## Queues, waits and transport semantics

Each message input owns a bounded reception buffer in RAM, shared by its
subscriptions. FIFO/latest, capacity and overflow policies apply as in real-time.
A selected Push FIFO record is protected during its callback. Pull reads do not
reserve records against overflow eviction.
There is no additional sender transport queue in functional simulation, so it
does not reproduce the real engine's physical backpressure behavior.

For `overflow: "wait"`, a full destination creates a pending admission with a
virtual `wait_timeout_ms` deadline. Other destinations of that publication and
other components continue to progress. A consumer can free capacity before the
deadline, or the pending admission returns `full` at expiry. Capacity available
at the deadline is tried before refusing admission. Subsequent publications from
the same component wait behind the unfinished publication. Withdrawals release capacity at successful FINISHED, before outbound publication
admissions. A component with unresolved outputs waits before its next activation.

TCP/UDP, ACK and retry settings remain in the common compiled plan but do not
create socket traffic, loss, latency or retransmissions in this engine. Network
frame/datagram limits do not model delivery here; CH/2's 1 MiB frame limit still
applies at the component boundary. Oversized ACTIVATE frames are refused
explicitly. Transport fault simulation can be added as another layer later.

## Bounds, failures and observations

`--max-tick-output-bytes` retains its CLI name and defaults to 16 MiB per
callback. Accounting includes SDL payloads, UTF-8 port names and twelve bytes
per publication for framing/ID overhead. Crossing this limit
fails the run instead of spilling to disk or waiting forever for FINISHED.
The same byte limit bounds aggregate pending external envelope bodies. Payloads
and queue records are volatile; there is no message persistence or recovery.

If a callback fails, the entire current wave's outputs are discarded and all
component processes are stopped. Earlier completed waves remain visible. Withdrawals already committed by other successful callbacks in that wave are
not rolled back. Parameter services, timer controls, recording, logs, measurements
and external side effects are outside that journal. This is not a global transaction.
Ctrl+C/SIGTERM cancels the run and uses the bounded STOP/terminate/kill cleanup.
Startup keeps infrastructure deadlines; callbacks have no wall-time deadline by
default. Physical infrastructure failures abort a run rather than producing a
different successful logical execution.

The optional JSONL trace records virtual timestamps, waves, selected input IDs,
publications with SHA-256 envelope fingerprints, terminal admission results,
logical completions and the stop reason. It contains no payload journal, random
epoch, PID, physical timings or stderr ordering. A streaming trace hash is also
computed when no trace file is requested. The final JSON summary and
`last-status.json` report this hash, virtual time, tick/publication counts, queue
metrics, waiting inputs and any pending ticks at the horizon.

For reproducibility, callbacks must not let wall clocks, unseeded randomness,
live I/O, shared mutable files or internal thread races affect their outputs.
Standard output remains reserved for CH/2 IPC. Application stderr logs and external
side effects are outside the deterministic trace contract.

## Runnable parallel example

The example has a C source, independent Python and Rust branches, and a Python
join with a passive Pull inbox for externally scripted offsets. Run from `ose`:

```sh
make build MODEL=examples/simulated/model.json DEPLOYMENT=examples/simulated/deployment.json
export PYTHONPATH="$PWD/src:$PWD/build/generated/shared/python:$PWD/../sdl/runtime/python"
python3 examples/simulated/make_scenario.py
python3 -m ose.cli run --mode simulated --duration 0.5 --workers 1 \
  --scenario build/simulated-scenario/scenario.json --trace build/sim-1.jsonl
python3 -m ose.cli run --mode simulated --duration 0.5 --workers 4 \
  --scenario build/simulated-scenario/scenario.json --trace build/sim-4.jsonl
cmp build/sim-1.jsonl build/sim-4.jsonl
```

For a separate SDL dependency checkout, replace the last PYTHONPATH entry with
`$SDL_ROOT/runtime/python`. Both branches can calculate concurrently, while the
join sees the same input pairs and offsets for every worker count.
This example now uses model v2: first source activation is at 100 ms. With an
exclusive 0.5-second duration there are four source sequences, 20 callbacks
(including two join callbacks per sequence), and 18 publications including the
two external offsets. The join correlates Push references by sequence and reads
its offset through Pull. See [migration details](model-v2-migration.md#explicit-joins).

`make test-simulated` runs the simulation regressions. `make check` includes this
suite after unit, Rust SDK and real-time integration checks.
