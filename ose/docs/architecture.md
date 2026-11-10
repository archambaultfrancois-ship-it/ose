# Architecture and model

The dependency direction is:

```
CLI -> modeling/compiler -> modeling/model + codec/SDL2
supervisor -> broker + adapter processes -> delivery + transport
adapter -> scheduler + component IPC
component -> language SDK + generated SDL codec
```

SDL has no dependency on this project. The codec facade uses the public SDL
generator CLI and runtime operations: catalogue description and identity,
prepare, compare_catalogues, payload validation, dynamic decode and envelope pack/unpack. Generated
classes are enumerated using the public SdlMessage base class. No PoC internals
are imported. The dependency revision is recorded in `sdl-dependency.json`.
Validation reuses up to 64 prepared SDL contexts per codec instance in a bounded
LRU cache keyed by the full catalogue text. Every message still undergoes envelope
identity, payload structure/content and expected-root checks; decoded values are not cached.
Broker and adapter admission use `Runtime.validate_only`, backed by SDL's
`validate_payload`, to check the complete payload without constructing decoded
messages or numeric lists. The same path serves distributed routing, simulation
and internal recording admission. `Runtime.validate` still returns decoded
values for callers that need them. Deploy this facade with the updated SDL
Python runtime providing `validate_payload`; no native extension is required.

## Documents

Application models use integer `version: 2`; deployments and scenarios retain
`version: 1`. Duplicate and unsupported keys are rejected. Model `schemas`
names a source file or directory relative to the model. Components declare
`inputs`, `outputs` and optional static `parameters`, shared parameter bindings
and recording types.

Message ports have a logical `root` and optional overriding `schema`. Inputs
choose Push or passive Pull independently of FIFO/latest reception policies.
Push inputs may declare a positive inactivity `timeout`. Explicit periodic or
one-shot timer inputs have no SDL contract. There is no component trigger or
joint-readiness `required` flag. See [model v2 migration](model-v2-migration.md).

Names cannot contain dots; an endpoint is `component.port` and each output
endpoint is a separate topic. Subscriptions are `{id, from, to, qos?}` entries,
with unique IDs, or use `from_topic` for a declared external topic. Multiple
producers can share an input; subscriptions to that input share their
[reception settings](delivery.md#shared-input-reception). Timers cannot receive
subscriptions.

Connected roots have the same logical name. Dynamic publisher-to-consumer SDL
compatibility uses `require_required=True` for schema fields, independently of
input activation. A compatible message may carry a different catalogue from
earlier messages on the same route.

Each Push message or expiration selects one callback. Its event-specific
`activates` links enumerate the outputs it may publish. Ordinary and real-time
execution reject cycles in event-to-output-to-Push-message dependencies; data
cycles through Pull inputs or silent Push receipts can be valid. Models with
activation cycles can be edited and compiled, then launched with simulated
[`--microsteps`](simulation.md#microsteps-and-activating-feedback). No initial feedback message or latest-only
restriction is imposed.

The shared scheduler drives real-time, distributed and simulated components.
Successful FINISHED commits deferred withdrawals before outbound admission waits;
callback failures cancel withdrawals and staged publications. Input inactivity
counts reception time, and periodic timers retain their cadence and coalesce
missed deadlines. Processing wakes on admissions and useful deadlines rather
than a fixed polling delay. See [runtime execution](runtime.md).

Deployment `components` covers exactly the model components. Each entry has an
argv `command` or a `container: {language: "python" | "c" | "rust" | "java" | "ada", handler: "path/to/logic"}`
definition, optional `build` list of argv commands, `cwd`, `env`, and
`placement` (`local`, or Kubernetes constraints in distributed deployments). No shell interprets commands. `cwd` is
relative to the deployment file. Supported substitutions in commands and env:
`{python}`, `{project}` (model directory), `{sdl_root}`, `{build}`, `{generated}`.
The latter names the shared generated directory; schema overrides have separate
generated directories with hashes of their source paths.

Generated containers bridge the same component protocol to typed user
callbacks. The generated code is under `build/components/<name>`; user handlers
remain outside build artefacts and are never overwritten. See
[callback containers](callbacks.md) for callback selection and lifecycle rules.
Native containers compile with the chosen SDL checkout and bundled stdio SDKs.

Deployment `subscriptions` maps IDs to `{transport: "tcp" | "udp"}`. Model QoS
specifies the delivery requirements. Compilation resolves both into the plan,
never silently weakening an unsupported profile.

## Process lifecycle

The runtime also provides a [functional simulated engine](simulation.md), selected
by `run --mode simulated`. It uses the same compiled plan and component protocol,
with central virtual-time coordination and direct bounded RAM admission instead
of network adapters and broker processes. Parallel calculations cross deterministic
publication barriers, so worker count does not change the logical execution.

See [runtime execution, ticks and message flow](runtime.md) for a detailed
walkthrough of startup, input selection, callbacks and delivery confirmations.

The supervisor hosts the broker and launches a Python adapter process per
component. Each adapter starts its component process. All components initialize
and send READY before the supervisor enables publication and delivery. Startup
has a 20-second readiness limit. A process failure stops OSE execution.
The local supervisor waits on READY notifications, child-exit futures and the
stop event; it does not periodically scan child return codes during execution.
SIGINT/SIGTERM stop adapters, their children, then the broker. Startup and run
failures also clean up children. Infinite execution is the default; `--duration`
supports demonstrations and integration tests.

The generated plan contains absolute paths and is rebuilt for another machine.
It is not intended as a portable deployment bundle. `endpoint.json` enables
live inspection and `last-status.json` stores the final available counters.
Messages, catalogues, retry state and deduplication history exist only in RAM.
No runtime state directory or database is created; restarting begins with empty
queues and the model's catalogues. Diagnostic files do not restore delivery state.
There is no automatic restart.

Control requests remain independent of data admission. Local recording activation
uses versioned change watches; adapter parameter readers use bounded ten-second
event waits. Status reads enable detailed metrics for three seconds, renewed by
further reads; the first read after idle waits at most 750 ms for a fresh report
without blocking other control traffic. Observed changes coalesce over 500 ms.
Without readers, one-second presence reports avoid building detailed snapshots.
Full report age and presence age remain separate. Logs wake their sender
independently. These paths retain the same
process isolation, transport profiles, queue bounds and component protocols.

## Public boundary

The CLI and the CH/2 SDK protocol are the supported interfaces. Python internal
classes are implementation details. The resolved plan is versioned but intended
for the matching CLI version. Future transports implement byte movement;
delivery policy stays in the common delivery engine. The
[distributed real-time engine](distributed.md) provides remote workers, direct
TCP routes and Kubernetes export; the ordinary supervisor remains local.

The optional [interactive recorder](recording.md) discovers live publication
points and component-owned internal points through read-only RPCs. Components
declare typed, namespaced diagnostic points before READY; their contexts own
hash registries and receive activation updates between ticks. Internal samples
use a dedicated CH/2 service path and never enter application subscriptions. Bounded
taps observe broker/worker publications and diagnostics;
the external console selects filters and writes SDL recordings on its own disk.
It has no model component, input reservation or role in the startup barrier.
