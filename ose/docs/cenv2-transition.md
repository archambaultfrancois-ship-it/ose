# Push/Pull and CENV/2 transition

Status: incremental development on `dev-cenv2`. This document separates agreed
semantics from proposed API syntax and work that is not yet implemented.

## Decisions

- Push preserves callback activation and consumption after successful processing.
- The target API uses one callback per activating input. A callback receives only
  its own input reference; it is not passed a combined multi-input snapshot and
  does not wait for other Push inputs. Other Pull inputs are accessed explicitly.
- `on_tick` disappears from the target API. Timer inputs use ordinary input
  callbacks. Existing repository models, examples and callbacks migrate directly;
  no legacy execution mode or `on_tick` compatibility adapter is required.
- Message arrival and input timeout invoke the same input callback. The input
  reference distinguishes the timeout event and carries no message for that event.
- Timer inputs support model-declared periodic and one-shot modes. Periodic timers
  rearm automatically; one-shot timers can be explicitly rearmed by the component.
- The model chooses initial timer arming: start the delay when the component
  starts, or wait for explicit arming by the component.
- Missed periodic deadlines are coalesced into one activation whose reference
  reports the number of elapsed deadlines. The timer keeps its deadline cadence.
- A Push input timeout measures inactivity since the last new message reception,
  including callback execution time. Expirations repeat while no message arrives.
  A new reception resets the inactivity deadline and cancels timeout activations
  for that input that have not started yet. It does not interrupt a running callback.
  A valid new publication counts even if a full inbox refuses it. Retries of an
  already observed publication do not reset inactivity within the receipt window.
- Pull is a passive inbox: arrival neither activates a callback nor gates another
  activation. Access occurs during an activation supplied by another input or a
  timer. FIFO and latest remain separate reception policies.
- `get` reads without consuming or reserving. Repeated reads may return the same
  message. Overflow can evict it after the read.
- `pull` reads and consumes; `remove` consumes without returning the value. Both
  immediately hide the selected handle from subsequent reads in that callback.
  Actual withdrawals and capacity release are deferred until normal callback
  completion (`FINISHED`). An exception, crash or missing `FINISHED` cancels them.
  This supersedes the initial choice of immediate capacity release.
- A normal Python callback return, including `False`, is successful. Rust uses
  `Ok(())` for successful execution; C retains its nonzero-success/zero-error
  convention. Java callbacks return void and fail on an escaping throwable.
  These indicate SDK execution errors, with no additional business
  status in `FINISHED`. Publications are staged until `FINISHED`, then
  withdrawals commit before outbound admission waits. Failed callbacks publish
  none of their staged outputs. Timer controls, measurements, logs and parameter
  services are not part of this withdrawal journal.
- Explicit Pull operations are nonblocking. If no message matches, `get` and
  `pull` return an absence indication and `remove` reports no consumption.
  They never wait for a future arrival or introduce a reception timeout.
- Application message IDs are optional unsigned 64-bit integers, distinct from
  engine publication identities used for deduplication, retries and generations.
  Zero is valid. Missing and zero are different. SDKs must preserve all 64 bits.
- When an application ID matches several FIFO messages, explicit operations
  select the oldest matching message.
- Pull latest supports all three operations: `get` retains the current value;
  `pull` and `remove` clear it until another message arrives.
- Messages arriving during a callback become accessible at the next activation.
  This visibility cut does not reserve inbox records against overflow eviction.
- CENV/2 changes the outer transport envelope, not SDL. S2PS/2 bodies, per-message
  catalogue association, discovery of unknown catalogues, compatibility checks
  and collision handling must remain dynamic. A route ID never fixes a contract.
- CENV/1 becomes obsolete in the target release. CENV/2 is the only supported
  network version; no CENV/1 fallback or coexistence layer is required. Engines,
  SDKs and external clients must migrate together. Unsupported wire versions must
  fail explicitly. This decision does not change dynamic contract behavior.
- No mandatory initial value, latest-only policy, periodic-only execution or
  artificial physical delay is introduced to handle feedback.
- Microsteps remain a possible later alternative; they are not the first solution.
  The retained principles and open design choices are tracked in [TODO.md](../../TODO.md).

## Implemented scope

Implemented in this branch:

- This transition specification and the ordered migration gates below.
- A reproducible CENV/1 versus CENV/2 and inbox microbenchmark (`make bench-cenv`).
- Internal, synchronous inbox `get`, `pull` and `remove` primitives, with optional
  application ID, source topic and exact engine handle selectors.
- Optional `app_id` validation on records admitted to an inbox. Existing records
  without this field preserve their behavior.

- Application model v2, generated Python/Rust/C/Java callbacks and binary CH/2 input services.
- Runnable acquisition, simulated, shared-parameter, multilingual and vibration-watch
  models/handlers migrated to v2. Sources use explicit timer inputs; periodic
  display uses Pull/latest; joins correlate individual Push callbacks by sequence.
  Editor source operations and contract validation handle the migrated models.
- Per-input scheduling in both the real-time local adapter and parallel simulator.
- Distributed workers share the v2 scheduler and CH/2 services. Broker/direct
  delivery preserves application IDs and deferred callback consumption. Python
  processes and a native C pair exercise the distributed paths on loopback,
  including mutual TLS, lost-ACK retries and generation fencing.
- Optional application IDs preserved by Python publication, broker TCP/UDP delivery
  and simulation, independently of engine identities.
- Deferred withdrawals on normal completion, cancellation on callback failure,
  event-specific output permissions enforced by both SDK and engine.
- [Binary CENV/2](cenv2.md) on Python local/distributed/client transports and the
  shared native Rust transport used by C, Rust, Java and Ada clients. CENV/1 is
  rejected. Compact frames and generic binary control objects have shared vectors.
- Public application-ID publication and callback metadata in external Python,
  C, Rust, Java and Ada clients. Native examples preserve IDs when replying.
  All five bindings are tested, including native Ada TCP/UDP with absent, zero,
  greater-than-2^53 and maximum uint64 IDs under GNAT 12.
- Rust component CH/2 callbacks, typed Pull access, output permissions and timer
  control, using the same local engines as Python. Native/mixed Rust/Python
  feedback, dynamic SDL decoding and shared context services are exercised.
- C component CH/2 callbacks with typed, callback-scoped input references, Pull
  selectors, publication IDs and timer control, sharing existing context services.
  All-C and bidirectional mixed C/Python feedback run through the local engines.
- Java component CH/2 callbacks, typed Pull selectors, optional BigInteger uint64
  application IDs and timer control, sharing the existing parameter, logging,
  measurement and recording services. All-Java and bidirectional mixed Java/Python
  feedback are exercised. The 25 CH/2 cases pass with JDK 17 using the default
  Java 7 source/API target; 98 generated class files have bytecode major version
  51. Execution on an actual Java 7 JVM still awaits CI qualification.
- Ada CH/2 runtime and generated APIs, owned typed references, Pull selectors,
  optional uint64 IDs and timer controls. All 23 strict GNAT cases pass, including
  local execution, parallel simulation and both directions of mixed Ada/Python
  feedback. The complete five-language example passes with GNAT 12 and JDK 17.
  See [Ada input callbacks](ada-inputs.md).

Network transport now uses CENV/2 exclusively. Rebuild native libraries and upgrade
all peers together; dynamic SDL contract behavior is preserved. All five component
SDKs are qualified for local execution and simulation. Repository-wide model
and handler migration is complete for the runnable examples and vibration-watch.
Retirement of v1 SDK/runtime paths is complete in source; final qualification is in progress. Recording/replay preserve
application IDs using optional per-message metadata; legacy recordings remain
readable. See [model v2 migration](model-v2-migration.md). The compiler accepts v2 applications with distributed execution, using the shared
input scheduler. Application model/plan v1 and component CH/1 execution are rejected;
deployment documents retain their independent version 1.

## Retirement cleanup

Actual JVM 7 execution is explicitly deferred at the user's request. The JDK 17
Java 7 source/API/bytecode qualification remains valid; running an actual JVM 7
is a later qualification task, not a prerequisite for removing obsolete code.

Completed in this cleanup batch:

- Removed v1 structured editor commands, component-cycle guidance, trigger/required
  field suggestions and old graph/table presentations. Capabilities advertise v2
  only. Legacy model sources remain editable for migration with an explicit error;
  ordinary save rejects them, while explicit Save draft preserves the text.
- Migrated the editor authoring workload and frontend fixtures to v2. Activation
  link editing, output rename propagation, source previews and undo/redo remain.
  Deployment, workspace and scenario versions are unchanged.
- Detached Python CH/2 sessions and contexts from the retained v1 classes. Shared
  framing/context services now live in `component_io.py` and `context.py`; v2 no
  longer inherits snapshot decoding, snapshot publication or the old tick iterator.
- Replaced the current CH/1 protocol reference with CH/2 shared-service framing,
  and rewrote callback, runtime, simulation and Java/Ada documentation for input
  references, Pull, explicit timers and deferred completion. Parameter, recording,
  logging, measurements, architecture and feature links follow those rules.

Checks: 309 engine unit tests, 30 Python callback/container integration tests,
73 editor Python/API tests and 70 frontend tests pass. The production
Vue/TypeScript build, asset hashes and edited documentation links are checked.
Logs are `ose/build/retirement-unit-final.log`,
`ose/build/retirement-python-inputs-final.log` and
`ose/build/editor-retirement-*-final.log` (build: `editor-retirement-build.log`).

All execution drivers, generators and component SDKs now require CH/2.
The completed retirement batches are described below.
Packaging/release qualification is recorded below.
Existing recording readability and dynamic SDL contract exchange remain required.

### Execution driver retirement

- Local, distributed and simulated execution share the input scheduler and CH/2
  services. Snapshot execution branches, the old scheduler and CH/1 startup
  negotiation are removed. A component must acknowledge READY version 2.
- A common reader rejects application plans other than version 2 and components
  other than execution model 2 before process startup or service creation.
  Compilation also requires application model version 2, including raw commands.
  Rebuild old plans and containers. Deployment/scenario/telemetry versions stay
  independent; dynamic per-message SDL contract exchange is unchanged.
- Diagnostic flow metadata describes input modes, timers and activation permissions.
  Aggregate callback duration no longer produces an alert against a component-wide
  period, since different inputs can activate unrelated callbacks.
- Application build commands can replace an obsolete plan. Commands that read an
  existing plan for execution or client configuration use the shared version gate.

Qualification for this batch:

- 309 unit cases and 73 editor Python/API cases pass.
- 112 simulation, distributed, fan-in, shutdown, launch-directory, backpressure,
  delivery-limit and recording/replay cases are qualified. One old rejection-message
  assertion was updated and passes in the subsequent targeted run.
- 65 Python/C/Rust callback and subprocess cases pass. Parameter coverage adds
  18 cases; its two Ada cases are not selected in the Android batch. The two Java
  cases fail with Android's JDK 21 because `--release 7` is unsupported, and both
  pass under Linux/JDK 17 with the default Java 7 API target.
- Four targeted Linux/GNAT 12 cases pass: the five-language application with
  identical traces for 1/2/8 workers and live TCP/UDP execution, Ada feedback with
  1/4 workers, Ada shared services and Ada recording activation.
- Wheel and source archives build. Archive contents include the CH/2 modules and
  C/Rust/Java/Ada SDK additions and exclude the three retired Python runtime
  modules. An offline wheel installation passes isolated imports, CLI parsing,
  plan-version checks and SDK-file checks. Full release/bootstrap qualification
  is recorded in the following batch.

Logs are `ose/build/retirement-runtime-{unit-final,integration,sdk,linux,editor}.log`,
`ose/build/retirement-runtime-java-{component,client}-parameters.log` and
`ose/build/retirement-runtime-{package,install}.log`. The SDK log also includes
the corrected distributed-plan rejection test. Actual JVM 7 execution and
microstep simulation remain deferred.

### Release and installed-package qualification

The installed-wheel check now compiles real timer handlers and executes one CH/2
callback per selected language, at exactly 10 ms, then reaches quiescence. It
checks installed SDK resources and builds the Java/Ada external clients as
selected. It passes for Python/C/Rust on Android and all five languages on Linux
with GNAT 12, Rust 1.85 and JDK 17 targeting Java 7 APIs. Actual JVM 7 execution
remains deferred.

The web console now presents input counts/modes instead of a global trigger,
with timer delays, arming modes, inactivity timeouts and event/output permissions
in node details. Valid data feedback remains visible. Its API fixtures use v2
plans; observation metadata retains its own version 1. The generation-change
diagnostic test changes the runtime epoch itself, so a concurrent status poll
cannot undo the simulated restart.

Checks for this batch:

- 32 release installer/private-Python/self-extraction tests pass. The resource
  exclusion check permits only the CENV/2 JSON vector needed by the installed
  Rust SDK tests, while still rejecting application tests and compiler caches.
- 80 web Python/API tests and 57 frontend cases pass (50 Vue/domain cases and
  seven Node cases). The production Vue/TypeScript build and static asset targets
  are checked. The WebSocket test dependency was supplied from the local wheel
  repository; no dependency download was needed.
- The complete Android ARM64 / CPython 3.13 bundle builds offline from cached
  wheels and vendored Cargo sources. Its manifest matches current runtime/SDK
  sources and the rebuilt web assets; retired Python runtime modules are absent.
  The companion SHA-256 check passes.
- The final bundle passes `tools/release/check.py` outside the checkout, with
  no usable system Python and an unavailable package index. Its private Python
  and native libraries, independent v2 C/Rust/Python applications, simulated and
  real-time execution, installed editor authoring, console login and filtered
  capture retaining application ID zero are exercised. Offline installed Rust
  tests pass five component cases plus one compile-fail doctest and six client
  cases. No release was uploaded or published.

The final archive is
`build/cenv2-release-final/ose-android-30-arm64_v8a-cp313.sh`, relative to the
repository root. Its file checks and digest are recorded in
`ose/build/cenv2-release-artifact.json`. These are local, ignored build outputs;
the bundle is not published. Compatibility and upgrade instructions are in
[the release guide](../../docs/release.md#application-and-protocol-compatibility).

Logs: `ose/build/cenv2-release-installer.log`,
`ose/build/cenv2-release-{build,qualification}-final.log`,
`ose/build/cenv2-installed-sdk-{host,linux-final}.log` and
`ose/build/cenv2-web-{python-final,frontend,build}.log`.

### Managed SDK retirement and fixture migration

- The deferred microstep alternative is recorded in repository [TODO.md](../../TODO.md),
  including stage visibility, deterministic execution and unresolved model choices.
  It remains outside the current retirement work.
- Java no longer has a CH/1 runner, snapshot callback interface, legacy publication
  branch or required-input port flag. Generation and scaffolding require v2 and
  produce only input-reference callbacks.
- Ada generation no longer contains the snapshot API/runtime or `On_Tick` scaffolds.
  `Ose_Component` no longer exposes the old tick decoder, READY negotiation or CH/1
  publisher. Common framing and context services remain for CH/2.
- Model validation rejects Java/Ada containers in v1 applications before generating
  sources. Shared name checks use prefixed Ada port identifiers without the old
  snapshot-field restriction. Python/C/Rust generator retirement is described
  in the subsequent batch below.
- Five-language log, measurement, diagnostic recording, compatible-schema replay and
  component-parameter fixtures now use explicit timers or Push references. A small
  test driver uses the actual CH/2 scheduler and services; no legacy callback adapter
  is introduced. Existing error, lifetime, schema and rebuild checks are retained.
- Java/Ada boundary tests now check reference metadata, exact uint64 IDs, timeout
  references, independent callbacks with component state, failure without FINISHED
  and explicit rejection of old INPUT/TICK frames. Ada retains the GNAT regression
  for rebuilding generated APIs with identical source timestamps.
- Network-parameter and process-identity fixtures also use v2. The simulation
  parameter case first activates at 20 ms, with 60 ms excluded; 1/3 workers produce
  identical observations and traces. Direct TLS-test imports use their package path.

Validation records for this batch are in `ose/build/retirement-managed-unit-final.log`,
`retirement-java-linux.log`, `retirement-ada-linux.log`, `retirement-services-local.log`,
`retirement-parameters-identity.log`, `retirement-parameters-tls-final.log` and
`retirement-diagnostic-raw-final.log`. The Java feedback bytecode audit is in
`retirement-java-bytecode.log`: 96 classes, all major version 51, built with JDK 17
`--release 7`. Actual JVM 7 execution remains deferred.

The final managed checks pass 35 Java cases (JDK 17, default `--release 7`)
and 31 Ada cases (GNAT 12.2.0), including Python interoperation and deterministic
feedback. The five-language application again produces identical simulation
traces for 1/2/8 workers and passes real-time TCP/UDP; the migrated external Ada
parameter case also passes (`retirement-mixed-linux.log`, whose combined command
additionally recorded a test-selection error for an incorrect recording class
name). The recording case was rerun with the correct selector and passes with
READY 2 asserted (`retirement-ada-recording-final.log`). Three process-identity
integration cases remain Linux-only and were skipped on the Android host.

### Python entry point and runtime fixture retirement

- The public Python SDK exports CH/2 `InputSession`, `InputCallbackContext`,
  `ReceivedInput`, `RecordResult` and `run_input_callbacks`. Snapshot session and
  context exports are removed. Common framing/recording helpers are imported
  directly from their shared modules instead of the retained v1 session.
- Python generated-container boundary tests now use explicit timer/Push
  references, including zero application IDs and the public SDK reference type.
  Independent callbacks coordinate owned component state; a later invalid
  activation does not invalidate an earlier completed callback. Signature,
  initialization, output type, scaffold preservation and dynamic schema checks
  remain covered. Old READY rejection uses a minimal peer emitting a legacy READY
  frame rather than importing the legacy SDK.
- Recording, measurement, log, lifecycle and timestamp unit fixtures use CH/2.
  Time reversal, nonsequential activation indexes and old tick frames are
  rejected; distinct activations at the same timestamp remain valid.
- Offline/live replay fixtures now use v2 in local and distributed execution,
  including TLS, PCAPNG/MCAP and compatible schema projection. Their exact
  nanosecond timing, repeated playback, payload hashes, stable ties and worker
  independence remain checked. Live fixture teardown stops and reaps components
  before closing their brokers.
- Admission/control isolation and shutdown regressions use v2. Only the selected
  Push record is reserved before IPC; another inbox remains subject to its normal
  overflow policy. Long admission waits, a nonreading child and TCP frame sizing
  remain covered.
- Python/C/Rust timeout fixtures now receive the same reference callback for
  messages and inactivity. Multi-input coordination is explicit handler state;
  unconnected passive inputs do not gate activation. Old frames, invalid indexes,
  Pull activation, unknown ports and truncated payloads fail without FINISHED.

Validation: 310 unit cases (`retirement-services-unit-final.log`), 20 Python
container/input-process cases, 34 replay cases, 13 admission/shutdown cases and
five timeout cases across Python/C/Rust. Logs are
`retirement-python-boundaries-final.log`, `retirement-replay-final.log`,
`retirement-live-replay-final.log`, `retirement-backpressure-final.log` and
`retirement-timeouts-final.log`, all under `ose/build`. The 19 live replay cases
were rerun after teardown cleanup without uncollected task/process exceptions;
they are part of the 34 replay cases, not additional coverage.

### Generator and Python runtime retirement

- All five container languages reject application-v1 models. Python/C/Rust direct
  generation and scaffolding also reject execution-model 1 before creating or
  replacing files. Validation covers those boundaries without requiring native
  toolchains.
- Python generation no longer selects snapshot callbacks or scaffolds `on_tick`.
  The unused `types` scaffold argument is removed. The obsolete Python session
  and callback modules (`execution/component.py` and `execution/callbacks.py`)
  are deleted; CH/2 uses the shared framing/context services directly.
- C generation only emits typed input references and the CH/2 callback runner.
  Rust generation no longer emits `Inputs`, `on_tick`, CH/1 dispatch, legacy
  publication code or the unreachable snapshot lifecycle. Both native scaffolds
  only declare activating inputs and skip Pull inputs.
- Native container boundaries use CH/2, including dynamic catalogue projection,
  absent/zero/max uint64 IDs, owned state, user failures, Rust panic cleanup,
  rebuild preservation, nested schemas and Unicode paths.
- Distributed, fan-in, shutdown and application-directory fixtures use v2. Fan-in
  tests explicitly pause activation selection while keeping admission live;
  they no longer manipulate the old periodic scheduler. RAM/control/contract
  validation fixtures also use v2.
- The simulation fixture now uses explicit timers and input references. Its
  Python/C/Rust diamond joins through one Push callback and a Pull withdrawal.
  Independent inactivity, passive latest sampling, FIFO overflow, shared fan-in,
  pending budgets, failure cleanup and cancellation remain covered. Sampling
  wait tests explicitly arm the first timer and then retain its declared cadence.
- Scenario arrivals are admitted before activation selection; computed
  publications become visible after their wave. A computed message cannot
  retroactively cancel an already selected timeout at the same timestamp.
  Exact event ordering and identical 1/4-worker traces are checked, alongside
  the existing 1/2/8-worker diamond checks. No engine timing rule or microstep
  implementation was introduced by this fixture migration.

Qualification records are `ose/build/retirement-native-boundaries.log` (eight
cases), `retirement-transport-fixtures.log` (29),
`retirement-simulation-v2-final.log` (23), `retirement-generators-unit.log` (310)
and `retirement-generators-sdk.log` (80). The latter exercises generated Python/C/Rust
callbacks, interoperation, deferred operations, dynamic contracts and external
clients after the generator branches and obsolete Python modules were removed.
The migrated RAM, broker, control and CLI/contract cases also pass 22 tests
(`retirement-model-fixtures.log`).

### C/Rust SDK retirement

- The C callback runner only handles CH/2 activation references and advertises
  READY 2. Snapshot admission, required-input checks, legacy dispatch and CH/1
  publication paths are removed. Output permissions always apply to the
  activating event, including publications without an application ID.
- Removed `ose_run_callbacks`, `ose_run_callbacks_parameters`, `ose_input_value`,
  `ose_ready`, `ose_tick` and the old `ose_publish`. `OsePortSpec` no longer has
  `required`; its four fields are `name`, `root`, `catalogue`, `timeout`.
  Generation uses that layout. Rebuild native wrappers and executables.
- C input decoder caches are separate from parameter-value storage. Decoded
  message values remain callback-owned through `InputRead`; no selected
  multi-input snapshot is kept. Pull selection, deferred consumption, sticky
  callback errors and lifecycle cleanup retain their CH/2 behavior.
- Rust no longer exposes `initialize`, `tick`, `TICK_CONTEXT`, the CH/1 `publish`
  helper or `Input::is_null`. `TickContext` remains time/counter storage used by
  CH/2 wrappers. The `inputs` module owns activation decoding and publication.
- The C/Rust common parameter parser now accepts `U` only. Its field layout and
  shared-parameter services are unchanged; old `D` business-input frames are
  rejected. Direct native parser checks cover valid updates and truncations.
  Business inputs continue to carry their complete dynamic catalogue and SDL
  envelope through CH/2, with no transport or contract-exchange change.

Qualification logs are `ose/build/retirement-native-sdk-unit.log`,
`retirement-sdk-c-parser.log`, `retirement-sdk-rust-unit.log`,
`retirement-native-sdk-integration.log` and `retirement-native-sdk-linux.log`.
The Linux run uses Rust 1.85.1, GNAT 12.2.0 and JDK 17 with `--release 7`;
actual JVM 7 execution remains deferred. It rebuilds the five-language
application and checks Ada's shared C framing/parameter services as well.

Results: 311 Python/engine unit cases and 59 C/Rust integration cases pass.
The C shared-parser probe also passes with the three existing format/log probes
(four cases, already included in the 311). Rust component SDK tests pass five
cases plus one compile-fail doctest on both Rust 1.96.0/Android and
Rust 1.85.1/Linux. The four targeted Linux integration cases pass: the
five-language application (identical traces for 1/2/8 workers and real-time
TCP/UDP), Ada feedback (1/4 workers), Ada shared parameter/log/measurement/
recording services, and Ada recording activation.

Execution drivers and the old scheduler are retired in the subsequent execution
batch described above. Application-model v1 rejection is global in compilation
and application plan loading.
Deployment/scenario versions, dynamic SDL contract exchange and old recording
readability remain unchanged. Packaging and release qualification are described
in the release batch above.

## Reference execution core

The shared per-input scheduling core uses activation-scoped inbox views. The
compiler requires application version 2 for raw commands and generated containers. Deployment and scenario loaders
continue to require their own existing versions. Generated v2 Python/Rust/C/Java/Ada
containers negotiate [CH/2](../sdk/input-protocol.md), with a single input reference
and synchronous inbox/timer requests serviced by the parent engine.

The runnable [feedback reference](../examples/feedback-pull-v2/README.md) executes
typed SDL callbacks with a real data cycle and no initial feedback message. Its
three timer activations and three response callbacks end in quiescence. Unit and
integration tests cover the model, output permissions, timer behavior, changing
message catalogues and both orders of reference component dispatch.

The in-process reference host remains a smaller executable specification without
shared parameters or recording. Generated subprocess containers reuse the common
context APIs for parameters, measurements, logs and internal recording, and run
through the real-time adapter, distributed worker or local parallel simulator. Integration tests exercise
actual subprocess feedback, timers, measurements, dynamic catalogues and exact IDs.

### Input declarations

Message inputs have `root`, optional `schema`, and `mode` (`push` by default or
`pull`). A Push input may declare an inactivity `timeout` in seconds. Pull cannot
declare an activating timeout or event-to-output links. Version 2 removes the
joint-readiness `required` field and component `trigger`.

Timer inputs declare `kind: timer` and a `timer` object: `mode` is `periodic` or
`one_shot`, `delay` is a positive duration in seconds, and `start` is `automatic`
(the default) or `manual`. They have no root/schema/catalogue and cannot receive a
subscription. Arming starts the delay; it does not invoke the callback immediately.
Explicit `arm(delay=...)` overrides the next delay, while subsequent periodic
firings retain the period declared in the model. `disarm` stops future expirations.

`activates` maps an input event to the output names it may publish. Supported
events are `message` for Push message inputs and `timeout` for inputs with a
timeout or for timers. Missing links authorize no output: the callback may still
compute and update state. For example:

```json
{
  "inputs": {
    "command": {
      "root": "Value",
      "timeout": 0.5,
      "activates": {"message": ["result"], "timeout": ["alarm"]}
    },
    "feedback": {"root": "Value", "mode": "pull"},
    "clock": {
      "kind": "timer",
      "timer": {"mode": "periodic", "delay": 0.1, "start": "automatic"},
      "activates": {"timeout": ["result"]}
    }
  },
  "outputs": {"result": {"root": "Value"}, "alarm": {"root": "Value"}}
}
```

Validation builds event-to-output and output-to-Push-message dependencies. It
rejects cycles in that graph and shows the input event/output path. Pull inputs
have no activating edge. A Push receipt with no allowed output is a leaf, even if
its timeout event independently authorizes an output. Runtime publication rights
follow the selected event, not all inputs present in the component.

### Python callback API

`on_<input>(ctx, input)` receives one reference with `port`, `event`, `message`,
`handle`, `app_id`, `source`, `elapsed` and `due_ns`. A message is decoded from its
own catalogue; an expiration has `message=None`, `event="timeout"`, and an elapsed
deadline count. One-shot timers always count one deadline, even when handled late.
`on_tick` and callbacks attached to Pull inputs are rejected by the v2 host.

The context exposes `ctx.inbox(port).get/pull/remove`, with optional
`id`, exact `handle` and `source` selectors, and `ctx.publish(port, message, id=...)`.
Read operations return a reference with the typed value and message metadata;
`remove` returns a boolean. `ctx.arm`/`ctx.disarm` are available during initialization
and callbacks. Generated output wrappers also accept `ctx.publish_<output>(value,
id=...)`. `ctx.activation_index`, `ctx.time_ns` and `ctx.now()` expose callback
index and engine time.

Only handles present at callback start are eligible for explicit Pull operations.
No reservation is created. Eviction can therefore make an earlier message absent;
the replacement stays invisible until the next activation. Pull/remove hide the
selected handle locally and journal the withdrawal until `FINISHED`. Failure
cancels the journal; it cannot restore a message independently evicted by overflow.
Captured inbox views are invalidated on callback completion or failure.

The core uses integer nanoseconds and selects ready events by scheduled/received
time, then input name and event kind. Callbacks of one component are sequential.
Completion never rearms independent timers or inactivity deadlines. Receipt history
is bounded to 4096 publication handles per component, separately from admission
deduplication, so a previously refused message can later be admitted on retry.

## Inbox semantics

| Operation | Result on match | Result if absent | Frees capacity |
| --- | --- | --- | --- |
| `get` | Record view | `None` | No |
| `pull` | Record view | `None` | At successful `FINISHED` |
| `remove` | `True` | `False` | At successful `FINISHED` |

The table describes callback-scoped SDK operations. Direct low-level `Buffer`
operations still consume synchronously; engines use deferred views instead.
Both APIs are nonblocking. Blocking
access is outside this transition; any future extension would require separate
waiting, cancellation and virtual-deadline semantics.

Selection defaults to admission order. An application ID selects the oldest
matching message; an optional source topic narrows the selection. Equal
application IDs do not deduplicate publications. An exact handle selects one
engine publication, and can be combined with the other selectors as checks.
Selective consumption preserves the order of remaining messages.
Exact handle lookup uses the existing direct index. Application ID and source
selection currently scan the bounded inbox without building a second record list;
an additional index should be justified by measurements before it is introduced.

Latest uses the same explicit consumption operations and retains its anti-stale
source history after clearing. Existing Push latest processing still retains its
value as before. Records reserved for a Push activation are excluded from explicit
operations. Validation and both SDK/engine checks prevent an input mixing modes.

Returned record dictionaries are shallow copies. Changing their metadata does not
change queue metadata. Immutable body bytes are shared. A handle can become absent
after a read, an eviction, another consumption or a restart; that is a normal
absence result, not permission to remove a different message with the same app ID.

## Activation and simulation

A cycle of data connections is different from a cycle of output activations.
For example, `A.out -> B.push -> B.out -> A.pull` has feedback data but no return
activation at A. It can terminate without a starting feedback message.

Removing an edge from cycle validation alone is insufficient:

1. Pull inputs must be excluded from callback readiness, required-input waits,
   timeout activation and automatic FIFO consumption.
2. The activation context must identify the actual triggering event. Computation
   may update state, but publishing must follow the declared event-to-output links.
   Presence of a retained latest value does not create a new event.
3. Each callback receives its activating input reference, without a combined
   multi-input snapshot. Pull access excludes arrivals after activation start.
   Local pulls/removes become visible immediately to subsequent calls in that
   activation. Simulation must define deterministic admission order and capacity
   release across workers. Queue withdrawals commit on each component's successful
   `FINISHED`; publications become visible only after the entire wave succeeds,
   in stable component and emission order.
4. Timer inputs have independent deadlines and no SDL message. The engine ACTIVATE (`T`)
   command remains an execution command, not an additional timer event. Processing
   feedback must not postpone an unrelated timer or input deadline. Push inactivity
   deadlines follow reception time rather than callback completion. Bookkeeping
   must reset those deadlines and cancel pending expirations on reception even
   when the message itself becomes accessible only at the next activation.
5. Delivery waits remain separate from activation dependencies. A full inbox may
   still block publication until its admission timeout. Pull does not prove that
   a message will be available or that every queued message will be consumed.
6. Require an acyclic activation graph, with a diagnostic showing the actual
   activating path. Component data graphs may contain cycles through passive
   Pull inputs or Push events that cannot activate an output.

The reference host, local Python/Rust/C/Java/Ada execution paths and distributed workers implement this visibility cut.
In v2 simulation, all ready components may share a wave: selections and eligible
handles are frozen before dispatch, and no publication arrives during calculation.
Each component owns its inboxes. Stable ordering after the wave makes traces
independent of worker count and physical callback completion order. A failure of
another component does not undo withdrawals already committed by a successful
callback; there is no global wave transaction.

## Ordered migration gates

| Step | Work | Acceptance gate |
| --- | --- | --- |
| 1 | Specify Push/Pull, IDs, consumption, events and activation visibility | Explicit decisions and remaining questions documented |
| 2 | Record existing correctness and performance | Reproducible bytes/encode/decode/inbox measurements; dynamic SDL regressions |
| 3 | Normalize optional application IDs independently of wire encoding | Exact uint64 boundaries; missing IDs; retry identity remains separate |
| 4 | Version the application model and migrate repository models | Push by default; direct migration without a legacy execution mode; reject unknown versions/fields; do not silently version deployment/scenario documents |
| 5 | Add queue primitives and selection | Nonblocking absence, deferred callback withdrawals, stable remaining order, overflow and stale handles |
| 6 | Extend versioned CH and reference Python SDK | `send(id)`, single-input callback references and explicit Pull access; explicit capability negotiation; remove `on_tick` and migrate callbacks |
| 7 | Integrate readiness, timers and simulation validation | Feedback example terminates; identical traces with 1/4 workers; timeout and backpressure regressions |
| 8 | Specify and implement binary CENV/2 | Opcode/field widths and bounds fixed; explicit rejection of obsolete versions; byte-preserving SDL and dynamic contract exchange; equivalent outcomes; measured gains |
| 9 | Port SDKs and external clients | C/Rust/Java/Ada and Python parity; local/distributed/direct routes; generations/ACK/retry tests |
| 10 | Update editor, diagnostics and replay | Show activation dependencies and modes; preserve IDs where relevant; existing recordings remain readable |
| 11 | Examples, packaging and release | Offline builds, compatibility matrix, all supported languages and migration instructions |
| 12 | Reconsider microsteps separately | Only if activation cycles are needed; deterministic ordering and no-progress diagnostics |

Steps 1–3 and 5 are implemented. Steps 4, 6 and 7 support all five languages in
local execution and parallel simulation, plus the shared distributed runtime.
Runnable repository models and handlers
are migrated. The retained v1 runtime/SDK paths are removed; their qualification
does not wait for actual JVM 7 execution, which is explicitly deferred.
Step 8 is implemented and measured. The network transport portion
of step 9 is implemented in the common native core. External-client application-ID
APIs are implemented and exercised in all five bindings.
Rust, C and Java component CH/2 callbacks are implemented. The Java 7 source/API/
bytecode target is verified with JDK 17; actual Java 7 JVM execution is deferred.
Ada's CH/2 API/runtime/generator compile and execute with GNAT 12. V2 distributed
scheduling is implemented and exercised with Python and C processes; transport
tests cover broker/direct routes, mutual TLS, retries and generation fencing.
Editor source validation/refactoring and visual activation/Push/Pull/timer controls
are adapted to v2. Step 10 also preserves application IDs through recording and
simulated/live replay, including repeated loops and both recording containers.
Full desktop-browser qualification of the editor remains outside the automated
checks. Step 11 has local package and installed-SDK qualification as documented
above; it does not qualify every OS/ABI or physical multi-machine deployment.
Step 12 is reserved in [TODO.md](../../TODO.md).

## Settled migration gates

- Python selectors are `id`, `handle` and `source`; port names and timer syntax
  are specified above and implemented in all five SDKs.
- Repository models and callbacks are migrated directly. `on_tick` and the
  snapshot execution paths are removed from all supported languages.
- CENV/2 opcode/field tables, limits and binary control encoding are fixed in
  [the wire specification](cenv2.md). Deployment requires upgrading all peers.
  Dynamic contract exchange remains unchanged.

## Validation and measurements

Initial validation of the foundations: 255 unit tests, 23 simulation tests,
10 local/distributed multi-producer integration tests, and three external-client
regressions passed. The latter include switching contracts on the same
subscription, UDP retry deduplication, and TCP/UDP FIFO/latest delivery.

Subprocess integration adds binary CH/2 bounds, callback failure cancellation,
inactivity timeouts, manual timer arming, engine-side output enforcement, old SDK
rejection and changing per-message catalogues over Pull requests. Parallel v2
feedback traces match byte for byte with one/four workers and reversed callback
delays. Real local TCP/UDP feedback preserves uint64 IDs and stays passive at Pull
inputs. See `make test-inputs-v2`. The existing simulation regressions have since
been migrated to v2 as part of runtime retirement.

Python callback qualification: 289 unit tests, 23 existing simulation tests and 41
integration tests passed: ten reference callback tests, nine CH/2 subprocess
tests, ten multi-producer tests and twelve external-client tests. The CH/2
subprocess tests include shared parameters, active internal recording, logs and
measurements. The feedback example also passed CLI build and both execution modes.

CENV/2 qualification: 296 unit tests, 23 simulation tests, 72 selected integration
tests and four Rust tests passed. The latter also pass after staging the SDK in
an independent application build directory. `make test-cenv2` passed in full.
The integration count includes the Java 8 local smoke test described below.

Coverage adds shared Python/Rust wire vectors, every metadata
truncation, malformed packet mutation checks, explicit CENV/1 rejection over
TCP/UDP, native C/Rust exchanges and evolved catalogues. Distributed routes,
generation replacement, TLS, parameters and backpressure retain their integration
coverage. SDK asset manifests include the reference fixtures.
Java client exchange passed using the repository's `OSE_JAVA_LOCAL_RELEASE=8`
option on JDK 21; this does not verify Java 7 compatibility. Ada client compilation
could not be verified because `gnatmake` is unavailable in this environment.

External application-ID API qualification: 296 unit tests, four Rust tests and
32 selected integration tests passed (14 Python external-client tests, nine CH/2
subprocess tests, eight C/Rust round trips and one Java client test). Coverage
includes missing/zero/max uint64 IDs, values above 2**53, duplicate application
IDs, latest replay, lost DATA/ACK retries and evolved native SDL catalogues.
The Ada C bridge passes strict C syntax checks; its typed Ada API and probe still
require execution with GNAT. Java qualification remains the local Java 8 target.

Rust component CH/2 qualification: 297 Python unit tests, six Rust SDK unit tests,
one Rust documentation test and 50 selected integration tests passed. The
integration selection covers 23 Rust/native and bidirectional mixed Python/Rust
checks, 19 Python reference/subprocess checks and eight existing C/Rust container
checks. It exercises typed Pull operations, changing SDL catalogues, uint64 IDs,
timers, scoped output permissions, shared context services and cancellation after
errors or panics. Parallel simulation traces match with one and four workers.
The all-Rust feedback example also runs through the CLI in simulation and real
time; its simulation trace matches the Python example.

C component CH/2 qualification: 298 unit tests and 50 selected integration tests
passed (23 native and bidirectional mixed C/Python checks, 19 Python
reference/subprocess checks and eight existing C/Rust container checks).
`make test-c-inputs-v2` passes with C99 and strict compiler warnings. Coverage
includes deferred withdrawal and staged-publication cancellation, SDK and parent
output permissions, explicit zero/max uint64 IDs, combined selectors, timer
control, dynamic per-message catalogues, shared services and malformed activations
or replies. The all-C CLI example runs in simulation and real time; its trace is
identical to the Python and Rust examples.

Java component CH/2 qualification: 298 unit tests and 54 selected integration
tests passed (25 all-Java and bidirectional mixed Java/Python checks, 19 Python
reference/subprocess checks and ten existing Java component/client checks).
Coverage includes normal returns, escaping Exception/Error cancellation, scoped
Pull and timer access, foreign-thread rejection, output permissions, changing SDL
catalogues, zero/absent/max uint64 IDs, full-range timer fields, malformed input
frames/replies and preserved parameter/log/measurement/recording services.
The Java CLI example runs in simulation and real time. Its simulation trace is
identical to the Python, C and Rust traces, and worker counts one/four match.
Java execution was tested with `OSE_JAVA_LOCAL_RELEASE=8` on JDK 21; the default
Java 7 target remains unchanged and unverified here.

Ada CH/2 source preparation: 302 unit tests and 19 Python reference/subprocess
integration tests passed, including new Ada generation/scaffold and port-name
validation checks. Both Ada example deployments generate; the all-Ada example
passes CLI model validation. Eight shared C/SDL bridge objects compile, and the
component/SDL Ada C bridges pass strict C syntax checks. These results do not
establish Ada source compilability or execution behavior. The prepared GNAT
qualification target contains 23 execution cases and fails explicitly because
`gnatmake` is unavailable: no Ada
execution test passed or was counted as a success. Ada traces, SDK parity and
callback behavior must still be qualified with GNAT before completing this gate.
Repository-wide migration was still pending at that stage. The subsequent
distributed and model migration qualifications are recorded below.

V2 distributed qualification: 302 unit tests, 31 selected regression integration
tests and 13 new distributed integration cases passed (346 distinct tests).
`make test-distributed-inputs-v2` covers the new distributed cases.
Real Python processes exercise broker/direct
and mixed routes, passive Pull feedback, optional/zero/max/duplicate application
IDs, callback failure cancellation, Push inactivity, lost direct responses and
broker ACKs, mutual TLS and cold replacement with generation fencing. A native C
pair exercises CH/2 through direct/broker workers. Remote shared parameters,
logs, measurements and internal recording also pass through a Python CH/2 worker.
Invalid external publication IDs are rejected before sequence/admission effects.
The existing distributed, parameters, logging, measurement and fan-in regressions
pass. These are loopback process tests, not multi-machine or Kubernetes execution
qualification. Rust/Java CH/2 local qualification remains as recorded above;
Ada/GNAT and default Java 7 execution qualification remain pending. The distributed
feedback deployment builds, with direct forward and broker feedback routing.
Its coordinator and both workers also pass a CLI smoke run: `cluster-status`
reports an enabled graph and three publications per worker.

Repository model migration qualification: all runnable application documents now
use version 2, while deployment/scenario/configuration documents retain their
versions. Model/API generation validates all five migrated applications, including
both vibration-watch deployment profiles and Ada source generation. The full
unit suite passes 307 cases. The editor's 66-case suite passes, and its subsequent
new-project v2 default and capabilities checks pass separately (67 distinct editor
cases). The 25 vibration-watch unit tests pass. Six OSE example integration cases
exercise real acquisition/restart, scripted simulation, shared parameters and a
Python/C/Rust/Java multilingual subset; simulation traces agree for 1/4 workers.
The two vibration application cases cover normal/increasing profiles, real-time
execution and broker/direct cold replacement. Signal capture/replay reproduces
alarms in simulation with 1/4 workers, repeated replay and local/distributed live
replay. A regression fixes compiled external Push topics being mistaken for
component outputs during activation-graph validation. See
[migration behavior and timer timing](model-v2-migration.md).
The two vibration parameter integration cases also pass: live observer/sensor/
threshold updates and replacement in local/distributed execution, plus identical
parameter-scenario traces with 1/4 workers. This migration stage passes 410
distinct tests across the suites above. The new example target groups the OSE
checks; they were executed separately during this qualification.
Java used the explicit local Java 8 target on JDK 21; this does not qualify Java 7.
The full five-language execution still requires GNAT. These checks do not retire
the retained v1 SDK/runtime paths or qualify replay ID evolution. Subsequent
editor UI qualification is recorded below.

V2 editor authoring qualification: 71 Python/source/API tests and 68 frontend
domain/component tests passed (139 distinct cases), together with the production
Vue/TypeScript build and a rebuilt-asset HTTP smoke check. Push/Pull and timer
controls, independent per-event output lists, optional internal activation arrows,
an activation table and v2 connection guidance are implemented. The compound
input command validates with OSE, rejects connected timer conversions and
activation cycles, preserves source context and supports undo/redo. Zero links
still means no output authorization; no implicit permissions were introduced.
The interface and its [qualification record](../../ose-editor/docs/validation.md)
retain explicit real-browser/performance limits. Recording/replay application-ID
qualification is recorded below.

Recording/replay application IDs now pass through local broker taps, distributed
external publications, direct/broker worker outputs and observed inputs. Capture
stores `ose.app_id` as optional per-message metadata in PCAPNG and MCAP. Zero,
uint64 maximum, absent and duplicate IDs survive recording, rotation, timestamp
sorting, compatible schema projection and repeated simulated/live replay.
Repeated publications receive distinct engine identities while retaining the
same application IDs. Legacy recordings remain readable without synthesizing IDs
from their uint32 sequence. Selected invalid IDs fail preflight.

SDL's Python recording service adds optional string maps to individual records,
without changing catalogues or SDL payloads. Reserved, ordinal-indexed container
metadata handles MCAP chunk buffering; conversion and filtered/split transforms
regenerate these associations. The inspector exposes them. Native readers can
still read the unchanged payloads while ignoring the extension; native recording
binding APIs do not yet expose per-message maps. ID-aware capture/replay requires
the updated Python service. Dynamic network contract exchange remains unchanged.

This stage passes 309 OSE unit tests and all 84 OSE recording/replay cases.
The SDL recording suite passes 83 tests
with three installed-Wireshark-plugin tests skipped; a separate new native-reader
case also passes for both containers (84 distinct passed SDL cases). The OSE
recording/replay qualification includes real local and distributed CH/2 processes,
broker/direct routing, legacy replay, native Python/C/Rust schema projection,
rotation and independently identical simulation traces with 1/4 workers.

SDK execution qualification now uses an isolated Debian 12 arm64 environment
under PRoot-Distro on the Android host: Python 3.11.2, GNAT 12.2.0, GCC 12,
OpenJDK 17.0.21 with the default Java 7 target, and Rust 1.85.1. GNAT uses
`-gnat2012 -gnata -O2`; later builds use two parallel compiler jobs. No local Java
release override is set. Results:

- All 23 Ada CH/2 cases and 25 Java CH/2 cases pass, including all-native and
  both directions of mixed Python feedback, 1/4-worker traces, actual TCP/UDP,
  dynamic catalogues, timers, deferred cancellation, context services and
  malformed frames.
- The existing managed SDK suites cover 18 distinct cases. Sixteen pass in the
  initial combined run; the two external-client cases pass after the Rust/Ada
  fixes below. No unrelated managed cases were repeated.
- Both Java and Ada external shared-parameter cases pass. Ada was explicitly
  selected with `TEST_LANGUAGES=ada` for its final run; the initial selection
  had skipped it and is not counted as a passed case.
- The full five-language application passes with identical traces for 1/2/8
  workers and real-time TCP/UDP. The all-Ada feedback example matches the previous
  Python/C/Rust/Java reference with 1/4 workers: six callbacks, six publications,
  quiescence and SHA-256
  `796b451975944f54c7b029e2f007806efae542fb6f4b029d39d099abd5e03002`.
- A persistent all-Java feedback build contains 98 class files, all major version
  51. This verifies the default source/API/bytecode target. Actual JVM 7 execution
  remains pending; both CI workflows now explicitly include the CH/2 suite in
  their Java 7 jobs. The CI jobs were not executed from this local environment.
- The common native client unit suite passes seven cases on Linux/Rust 1.85.1
  and six on Android/Rust 1.96.0. The additional Linux-only case checks native
  thread names. Two new FFI cases check owned UTF-8 strings, null/zero error
  results and panic containment. Repeated common cases are not additional coverage.

Qualification found that raw-pointer FFI results relied on `Default`, unavailable
for pointers on Rust 1.85. The guard now uses explicit null failure values without
changing the C ABI. The Ada external ID probe also needed unsigned-64 operator
visibility. A Linux thread-name test now reads `PR_GET_NAME` directly: PRoot's
`/proc/thread-self/comm` was observed to describe the tracer (`linker64`) instead
of the calling thread. All name assertions remain enforced; no test was skipped.

The isolated source copy avoids replacing the Android build artifacts. Logs are
in `ose/build/sdk-linux-*.log`; those files are generated and ignored by Git.
These checks do not qualify physical multi-machine execution or Ada-specific
distributed worker runs. Actual JVM 7 execution is explicitly deferred; it no
longer gates v1/CH1 code and documentation retirement.

Run from the OSE project directory:

```sh
make test-unit
make test-inputs-v2
make test-rust-inputs-v2
make test-c-inputs-v2
make test-java-inputs-v2
make test-ada-inputs-v2  # Requires GNAT; qualified with GNAT 12.2.0 on Linux.
make test-distributed-inputs-v2
make test-example-inputs-v2  # Set OSE_JAVA_LOCAL_RELEASE=8 explicitly on JDK 21.
make test-cenv2
make bench-cenv
# For a longer sample and a saved comparison artifact:
PYTHONPATH=src python3 tests/bench_cenv.py --iterations 20000 --repeats 5 \
  --output build/cenv2-comparison.json
```

The microbenchmark measures Python envelope encode/decode and queue operations in
process. It reports fixture body size, envelope overhead, TCP prefix, individual
samples and medians. Opaque fixture bodies deliberately isolate CENV cost; SDL
validation, network/TLS, dispatch, retries and end-to-end latency are excluded.
No timing threshold is a correctness test. Record environment and parameters when
comparing versions. CENV/1 is a frozen test-only baseline, with no runtime import
or version fallback. Both codecs are measured in the same benchmark process.

Initial sample, 2026-11-09, CPython 3.13.13 / Android 16 / aarch64, 10,000
iterations and five repeats (microseconds per operation, median):

| Fixture | Body bytes | Envelope bytes excluding body | Encode us | Decode bytes us |
| --- | ---: | ---: | ---: | ---: |
| DATA small | 16 | 172 | 12.66 | 12.08 |
| DATA 8 KiB | 8192 | 172 | 13.51 | 12.90 |
| ACK | 0 | 88 | 10.75 | 10.00 |
| Catalogue result | 27 | 35 | 10.27 | 9.98 |

The TCP length prefix adds four bytes. These numbers describe the supplied
fixtures, not a fixed overhead for every publication; metadata names and lengths
vary. Other test processes were active during sampling, so use a quiet machine
for performance comparisons. The complete local report is
`ose/build/cenv1-baseline-after.json` relative to the repository root; generated
reports are ignored by Git and can be regenerated with the command above.

CENV/2 comparison on the same environment, 10,000 iterations and five repeats,
with no other test suite running (microseconds per operation, median):

| Fixture | CENV/1 envelope bytes | CENV/2 envelope bytes | Encode v1 / v2 us | Decode v1 / v2 us |
| --- | ---: | ---: | ---: | ---: |
| DATA small | 172 | 115 | 12.96 / 11.98 | 12.33 / 10.62 |
| DATA 8 KiB | 172 | 115 | 13.57 / 12.76 | 13.14 / 11.37 |
| ACK | 88 | 55 | 11.14 / 6.44 | 10.60 / 6.86 |
| Catalogue result | 35 | 17 | 10.44 / 7.99 | 9.50 / 6.14 |

These fixtures save 57 bytes on DATA, 33 on ACK and 18 on catalogue results,
excluding the unchanged TCP length prefix and body. DATA encode/decode improved
by approximately 6–14%; ACK and catalogue-result operations improved more.
This measures the compact forms only. Generic binary control objects may have
different costs. It does not measure end-to-end network throughput or latency.
The complete local comparison is `ose/build/cenv2-comparison.json` from the
repository root and includes the mutable-buffer decode samples and inbox costs.
