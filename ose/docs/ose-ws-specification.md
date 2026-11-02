# OSE Web Console: functional and technical specification

Status: implementation specification, version 0.3, 2 November 2026.

This document defines the independent `ose-ws` console. Version 0.3 incorporates
the reviewed decisions and requires English source, documentation and frontend.
The OSE protocol is unchanged. Concurrent parameter overwrites are accepted.
MUST, SHOULD and MAY indicate requirements, recommendations and optional behavior.
Implementation and operational instructions are in
[the console README](../../ose-ws/README.md),
[configuration](../../ose-ws/docs/configuration.md) and
[API reference](../../ose-ws/docs/api.md).

## 1. Purpose and scope

The console serves one already-running OSE application, local or distributed,
and multiple authenticated users. It provides four workspaces:

1. Components: diagnostics equivalent to `ose top`.
2. Measurements: scalar discovery, selection and observation history.
3. Recordings: point discovery, source filtering and independent captures.
4. Parameters: reading and replacing SDL blocks through a typed tree editor.

The server is an external OSE client, not a graph component or a distributed
startup-barrier participant. Stopping it MUST leave the application running.
Application launch, build, stop, redeployment, replay, SQL, exhaustive measurement
acquisition and multi-application supervision are outside this version's scope.
Message contracts and parameter declarations are not editable at runtime.

## 2. Decisions

| Subject | Decision |
| --- | --- |
| Distribution | Independent Python package/command `ose-ws`, namespace `ose_ws`. |
| Execution | One process, one asynchronous loop, one owner per stateful service. |
| Backend | FastAPI/Uvicorn, JSON HTTP and WebSocket. |
| Frontend | Vue 3, TypeScript, ECharts, compiled assets included in the package. |
| OSE access | Existing Python APIs and RPCs, never CLI-output parsing. |
| Diagnostics | One shared periodic `status` collector. |
| History | Bounded RAM for followed logical measurements only. |
| Recording | One Recorder per capture, disk writes outside HTTP processing. |
| Parameters | Catalogue-derived schema, server-side SDL validation/encoding. |
| Concurrency | Complete replacement; the last write processed by OSE wins. |
| Persistence | SQLite metadata/audit and capture files. |
| Access | Loopback/local token by default; authenticated HTTPS proxy for network access. |
| Language | English code, documentation, API messages and user interface. |

Only one Uvicorn worker is allowed. An exclusive state-directory lock refuses a
second owner without disturbing the first. Multiprocess ownership requires a
separate state service and is outside this version. The installation MUST NOT
modify an application's model or plan. Optional recording/filtering dependencies
must be prepared beforehand; HTTP requests MUST NOT compile or download them.
Parameter client identities and rights must already exist in OSE.

## 3. Reuse of existing OSE services

Diagnostics use the terminal-independent `ose.diagnostics.Observer`, also used
by `ose top`. Recording selection uses the shared name/type/glob resolver.
Captures reuse `ose.recording.recorder.Recorder`. Catalogue handling and values
reuse the existing SDL runtime, `CatalogueTypes` and parameter `Values` codec.
No runtime revision, CAS, conditional write or new parameter RPC is introduced.

Capabilities are identified using available versions, fields and existing RPCs.
A missing capability disables the affected function with an actionable cause;
it MUST NOT make unrelated workspaces unusable. Web permissions describe local
policy, not confirmed remote OSE rights. OSE remains authoritative for requests.
No test write may be sent to probe a permission.

## 4. Architecture

- ApplicationConnection resolves the current endpoint and TLS identities.
- StatusCollector performs one non-overlapping periodic `status` RPC and shares
  the same snapshot with DiagnosticsService and MeasurementStore.
- DiagnosticsService preserves the CLI's states, rate calculations and alerts.
- MeasurementStore owns logical watches and generation-specific bounded buffers.
- RecordingManager owns capture state machines and serializes each object's commands.
- ParameterManager owns separate command and long-poll connections, grouped by
  configured OSE identity, distinct from web users and capture connections.
- SchemaAdapter supplies structured types, exact web values and normalized previews.
- The live channel applies explicit subscriptions, permissions, framing and backpressure.
- StateStore serializes SQLite work outside the HTTP loop. Heavy conversion,
  offline validation and disk work are also dispatched outside the loop.

Parameter subscriptions use groups of at most 256 paths, one existing
`parameters_subscribe`/`parameters_next` connection per group. They do not poll
all blocks with repeated `get` calls. A browser rereads an invalidated visible
block through HTTP; the upstream notification remains coalescible latest state.

## 5. Installation and configuration

Target platforms are Linux and Termux with Python 3.10 or later and compatible
OSE/SDL runtimes. Node.js is a frontend build dependency, never an installed
runtime requirement. No CDN or browser plugin is required.

```sh
ose-ws init --config ./ose-ws.json
ose-ws check --config ./ose-ws.json
ose-ws --config ./ose-ws.json
ose-ws --config ./ose-ws.json --endpoint ./build/endpoint.json --port 8080
```

`init` creates configuration and a random local token exclusively. Secrets are
owner-only files or environment values, never URLs, public configuration or logs.
`check` validates local settings without contacting or mutating OSE. Configuration
precedence is CLI, documented environment, file, defaults. Config paths are
relative to the config directory; the application endpoint is relative to
application.root. CLI paths are relative to the caller.

Distributed mode requires explicit host/port or endpoint, CA, client certificate
and key. Browser TLS and OSE mTLS are distinct. Incompatible options are rejected.
OSE without TLS is restricted to loopback. The endpoint is reread on reconnection.
An unavailable application does not prevent server startup.

Exit codes: 0 normal, 2 invalid configuration, 3 initialization/lock/port/storage
failure. The exact OSE/SDL compatibility is documented for the distribution.

## 6. Common interface

All four workspaces share application name, local/distributed mode, OSE connection,
last successful observation age, signed-in user and active captures. Failed
connections MUST NOT look like a healthy application with an empty inventory.

Tables support search, appropriate filtering and sorting. States are readable
without color. Trees/forms/commands have labels, keyboard access and visible
focus. Technical SDL/OSE names are not translated. Navigation and frozen views
MUST NOT stop acquisition or captures. A parameter draft stays in browser memory
when navigating; discarding/reloading it requires an explicit user choice.
Drafts, parameter values and secrets MUST NOT be persisted in localStorage by
default. Reload/closing the page can lose a draft and should warn when dirty.

## 7. Components

The table displays component name/generation, state, admissions/s, publications/s,
ticks/s, busiest FIFO, tick p95 and report age. Details include input/delivery
queues, buffer policy, depth/capacity, counters and explicit alert reasons.

States follow the CLI: ACTIVE, ATTENTION, LOSS/ERROR, UNAVAILABLE, STALE,
WAIT INPUT, STARTING and IDLE. Rates use the configured sliding window. Initial
samples and counter resets show unknown, never an invented zero. Epoch/generation
changes reset baselines. Refusals are admission attempts, not necessarily final
loss. Normal latest-value replacement is not FIFO saturation. Tick p95 measures
wall-clock protocol/execution duration, not CPU percentage. Default rendering is
once per second, derived from the shared 500 ms collector. Freeze is visual only.

## 8. Measurements

Discovery groups measures by component and dotted name. It shows current value,
generation and availability. Units/descriptions are used only when provided,
never inferred. Different units SHOULD use separate axes/charts by default.

A logical watch identifies application/component/local measurement name. It
survives component replacement and OSE reconnection. A history segment additionally
includes epoch and generation. A new generation opens a distinct segment without
joining it to old points. Old segments consume history budgets, not active-watch
quota. Thus a restart MUST NOT silently stop the selection or exhaust the active
series quota merely by creating historical segments.

The source provides the latest finite fl64 value, possibly null before first
emission, without history or producer timestamp. Each recorded point is an
observation at a successful read. Intermediate emissions may be missed. Equal
values may yield separate observations; equality does not establish whether an
emission occurred. Report age is not the age of the measure's last emission.

The server follows the union of active selections and configured names/globs.
Each browser watch leases selection for 60 seconds. Renewal/reconnection may
extend it. When its last lease expires, new history stops; old data remains until
eviction. Preconfigured selections remain active within quota. Data before the
watch began MUST NOT be reconstructed. The upstream `status` RPC still contains
all current values: selection limits console history, not upstream bandwidth.

Read errors, stale reports, nulls and absent producers make quality-marked gaps.
No zero substitution, artificial fresh repetition or automatic interpolation is
allowed. UTC serves display; monotonic time controls scheduling, durations and
expiry. UTC reversal marks a discontinuity. Observation order is independent
of wall time.

Charts support a rolling window, zoom/pan, legend, tooltip, visual pause and return
to live. Responses preserve extrema/endpoints and represent gaps when reducing
data. They announce available window, raw count, reduction and truncation.
Large histories are paginated by segments. CSV exports available raw observations
with UTC time, observation order, segment, logical name, value and quality.
This history is volatile and is not a PCAPNG/MCAP capture.

## 9. Recordings

Discovery shows topic, component/name when available, output/internal nature,
root type, description, availability and generation. List filtering and source
message predicates are separate concepts. Selection accepts exact names, SDL
types and shared shell globs. The concrete selection is reviewed before start;
newly discovered points MUST NOT be added silently.

Schema exploration exposes catalogue types/fields without generated application
classes. Each selected point may have a SDL predicate, validated server-side with
the existing filter engine. No Python/JavaScript expression execution is allowed.
Missing native filtering support is actionable. Filters execute at the source,
before queue admission and transfer.

A capture config includes label, PCAPNG/MCAP format, points/filters, source queue
capacity/byte budget, rotation threshold and retained-file count. Cyclic retention
requires rotation and announces deletion of older capture files. The threshold
is not a strict size maximum; one indivisible message and closing metadata can
exceed it.

Each opaque recording_id owns a one-shot capture. Labels are nonunique display
text, never file paths or authorization. State machine:

```text
DRAFT -> STARTING -> RECORDING -> STOPPING -> STOPPED
             |           |           |
             +-----------+-----------+------> FAILED
Active at console crash -------------------> INTERRUPTED on recovery
```

Only DRAFT is editable, with a local web revision. Duplicate configuration creates
a new object. Start validates selection/contracts/availability/quotas/destination,
then opens files and source sessions. Partial startup failure closes all resources,
retains diagnostics/artifacts and terminates FAILED. Commands on one capture
are serialized. Browser closure and session expiry do not stop it. Reconnection
MUST NOT restart an interrupted capture.

Counters distinguish received/written/filtered/indeterminate/lost/oversize,
per-point source counts and rotations/files. They MUST NOT claim to account for
SDK/adapter losses not exposed by taps. In distributed mode the coordinator
serves discovery while producers send directly to the console, verifying OSE TLS
identities. Existing timestamps remain recorder-arrival Unix time, not synchronized
producer emission time or a global causal order. Original business bytes are kept.

Files are exclusively created below state_dir/captures/recording_id. The browser
uses opaque file IDs. Paths and symbolic links MUST NOT escape the owned capture
directory. Rotation preserves foreign files. Only terminated, closed artifacts
can be downloaded. Interrupted files advertise unknown integrity until validated.
Cyclically removed files are expired, not available. Manual capture deletion and
downloading a live file are outside this version.

Lost source, changed generation/catalogue or disk error terminates that capture,
without stopping business components. Stop ceases admission, drains admitted
queues and attempts finalization using the existing Recorder. No power-loss
persistence or loss-free capture guarantee is made.

## 10. Parameters and typed editing

Authorized exact paths form the block navigation. Branches do not grant inherited
rights. Show root type, observation time, read-only/write policy and a recursive
field editor in SDL field-ID order. Large arrays/sequences are rendered in bounded
pages rather than expanding the entire structure.

| SDL structure | Editor |
| --- | --- |
| bool | Checkbox. |
| Enum | Named choices with numeric value visible. |
| Integer | Exact decimal input respecting logical/wire bounds. |
| fl32/fl64 | Numeric/scientific input, finite values only. |
| string | Text; optional multiline metadata hint. |
| Nested message | Expandable recursive fields. |
| Fixed multidimensional array | Fixed dimension sizes and indexed elements. |
| repeated/packed | Add/remove/reorder elements. |
| optional | Presence distinct from zero/empty values. |
| c32/c64 | Real and imaginary fields. |

The catalogue is sufficient: source SDL and generated classes are not required.
Unknown types remain visibly unsupported, never silently omitted. The web schema
includes root, full authorized catalogue, SHA-256 root/catalogue digest, referenced
types, field IDs/names, cardinalities, dimensions, enums and wire constraints.
References are preserved instead of infinitely expanding recursive structures.
The runtime codec remains authoritative.

Optional presentation metadata associates root and JSON Pointer with label/help,
unit, preferred widget, choices and business constraints. A wildcard sequence
segment is documented. Mandatory constraints are enforced server-side. Metadata
MUST NOT change permissions or the SDL contract. Source comments are absent from
the wire catalogue; displaying them requires separately supplied metadata.

The browser distinguishes editing base, local draft and latest remote value.
It displays field differences and provides Reload, Discard, Validate, Apply,
Export and Import. Reload MUST NOT silently destroy a dirty draft. No field edit
sends a write. Server validation checks fields/types/bounds/dimensions/cardinality,
enums, configured business constraints and encoded size. It round-trips through
encoding/decoding and previews normalized fl32/quantized values. Applying uses
that exact validated preview; editing invalidates it. Rights/contracts/limits
are rechecked at write time.

A positive acknowledgement means stored by the OSE parameter service, not already
used by all components. Without a draft, notifications refresh the displayed value.
With a draft, they update the separate remote value and preserve input. There is
no automatic merge. The user may reload, manually rebase, or apply the complete
draft accepting overwrites. Notifications are not exhaustive and are not a
concurrency precondition.

The last write processed by OSE replaces the complete block, even when based on
an older value. Different-field changes in the same block can overwrite each
other. CLI/component writers have the same semantics. Explain this near Apply,
without a routine extra confirmation. There is no distributed lock, OSE revision,
CAS, If-Match or comparison against the editing base. Serialization of one command
connection is execution management, not concurrency protection.

Exports use OSE's existing {path: block} JSON. Imports are bounded text, validate
all blocks before any write, preview differences and apply sequentially. Stop
at the first refused/uncertain write; report stored/not-attempted/refused/uncertain
per block. There is no cross-block transaction. Web int64/uint64 values are decimal
strings. OSE export integer literals are generated/parsed server-side as text,
never rounded by browser JSON.parse/Number.

OSE parameter updates remain volatile. Export does not modify initial plan values.
Starting/restarting the console MUST NOT restore old values. Filesystem parameter
sync remains a separate explicit workflow.

## 11. Existing parameter RPCs

Use parameters_open, parameters_list, parameters_describe, parameters_get,
parameters_subscribe, parameters_next, parameters_unsubscribe and parameters_set.
Open authenticates the configured OSE client. List exposes accessible paths;
describe supplies root/catalogue; get/set read/replace the complete current block.
Subscriptions deliver coalescible latest updates, not every intermediate change.

The catalogue does not expose detailed client rights. Web policy is displayed as
such; OSE can still refuse. Write-only web clients may describe and validate
supplied values without reading/exporting existing values or opening a read-based
browser editor. Operation IDs, observation timestamps and schema digests are web
metadata, not added RPC fields or concurrency revisions.

After lost acknowledgement, the outcome remains uncertain. Read and show current
state without automatically replaying the write. Equal current values alone do
not establish that the particular operation was acknowledged.

## 12. HTTP API and operation lifecycle

Business routes are authenticated under /api/v1. Opaque block IDs map to exact
paths to avoid slash/encoding ambiguities. OpenAPI is available to administrators.
The route inventory and request/response contracts are documented in the
[API reference](../../ose-ws/docs/api.md).

Lists have cursor/limit pagination (maximum 200); changed inventory invalidates
the cursor. Default filters are literals/documented globs, not arbitrary regexes.
Import plans are private, volatile, epoch-bound and expire after ten minutes.
JSON rejects duplicate keys, non-finite numbers and excessive nesting/elements.
Ordinary bodies are at most 2 MiB; explicit imports at most 16 MiB; depth 64.
Errors identify request and field JSON Pointer where possible. HTTP differentiates
syntax, authentication/permission, missing/expired resources, incompatible state,
size/value/quota failures and source unavailability.

Effectful commands use Idempotency-Key scoped to actor/target/body. A changed body
with the same key is rejected. Persist the intent before the first effect. A retry
returns the same operation, without starting another capture or replaying an
uncertain write. Long work returns an operation for polling. Terminal keys are
retained for at least 24 hours; pending/uncertain operations do not expire. At
quota, block new mutations instead of forgetting unresolved intentions.

Operation states: pending, complete, failed, uncertain, closed_unknown. An
administrator can close a non-running uncertain operation with a required audit
note. Closure records an undetermined result, sends no OSE command, does not infer
success from equality, and retains key protection for at least 24 hours. This
provides a bounded recovery path without silently losing ambiguous intentions.

When local persistence is unavailable or its operation quota is exhausted, an
explicit capture stop MAY bypass durable intent creation. It MUST be visibly
non-durable and attempt drain/finalization. No parameter write, capture start or
other new mutation gets this exception. This resolves the persist-before-effect
rule without preventing an active capture from being stopped during disk failure.

## 13. Live channel

The authenticated same-origin WebSocket /api/v1/live has explicit subscriptions
to application, components, measurements, recordings, parameters and operations.
Selections and requested maximum cadence are authorized server-side. Parameter
values are not broadcast to all users. Events identify protocol version, stream,
sequence, subscription, observed_at, type and data. Parameter invalidations ask
for an authorized HTTP reread; there is no revision/ETag concurrency contract.

Send an initial snapshot or an instruction to load it. Ordering is per connection,
not global causal order with OSE. A reconnect creates a new stream_id; reload
snapshots, renew watches and refill curves from available history. No durable web
event replay is promised. Measurements send observed points since the last send,
with explicit framing. Coalescible latest-state invalidations may replace one
another. Overflow produces resync_required or disconnects, never a hidden gap.
Commands use HTTP. Ping every 15 seconds, bounded response deadline, randomized
exponential reconnect 1–30 seconds. Session expiry/revocation closes the channel.
A slow client MUST NOT slow collection or another capture.

## 14. Authentication and authorization

Local mode uses a random token with at least 256 bits of entropy in an owner-only
file. Login exchanges it for an HttpOnly, SameSite strict cookie; proxy HTTPS mode
sets Secure. Tokens never appear in URLs or browser persistent storage. Sessions
expire after 30 minutes idle and eight hours absolute. Mutations require CSRF.
WebSocket upgrades check session and Origin. CORS is closed by default. Names,
errors, metadata and values render as text, never active HTML.

Network access requires per-user authentication over HTTPS. A trusted proxy may
provide it, but only exact configured peer addresses are trusted and incoming
identity headers must be overwritten. The application still enforces web policy.
Effective rights are its intersection with configured technical OSE rights.
Checks apply to commands, subscriptions, exports and downloads.

Permission families: components.read, measurements.read, recordings.create,
recordings.read/stop/download, parameters.read/write and audit.read; administrators
can also reconcile uncertain operations. Parameter scopes are exact paths or an
explicit all-path administrative selection. Capture ownership is separate from
labels and the technical OSE identity.

## 15. Budgets

| Resource | Default |
| --- | --- |
| Browser sessions | 20. |
| Shared status cadence | 500 ms; minimum 100 ms. |
| Control read deadline | 3 seconds. |
| History | 900 seconds; 128 logical watches; 250,000 points. |
| Accounted history buffers | 32 MiB, not total process RSS. |
| History response | 5,000 points/segment, 50,000 total per page. |
| WebSocket frame | 256 KiB, explicitly split point batches. |
| Slow WebSocket send | Bounded independently; resynchronize/disconnect on overflow. |
| Active captures | 4 total, 2 per user. |
| Drafts/import plans | 100 each; imports expire after 10 minutes. |
| OSE capture sources | 32 endpoints; 128 points per source session. |
| Default source queue | 256 messages / 8 MiB per endpoint. |
| Ordinary/import JSON | 2 MiB / 16 MiB, bounded parsing/preview. |
| Free disk before capture | 1 GiB, monitored during capture. |
| Graceful stop | 60 seconds, configurable. |

First-hit limits determine actual retention and are reported. A new selection at
quota is refused without removing another user's active series. Expired/unfollowed
history is evictable. Source quotas are shared with other OSE recorders; remote
admission may still fail. Disk thresholds refuse starts and attempt explicit
stops; ENOSPC may occur between checks.

## 16. Persistence, audit and recovery

SQLite stores capture/configuration/owner/operation/file/integrity/audit metadata;
files store recordings. Measurement history and browser sessions remain volatile.
Audit identifies time, actor, action/target and outcome, without full parameter
values or secrets by default. It is bounded by 30 days/100,000 events. Audit
expiry does not remove capture files. Metadata quotas prevent indefinite growth.

Local metadata is not the parameter-value authority. SQLite and OSE cannot form
an atomic transaction. Crash-time unresolved intentions become uncertain and
require explicit reconciliation. Previously STARTING/RECORDING/STOPPING captures
become INTERRUPTED. Existing files are never deemed complete merely because they
exist; integrity remains unknown until validation. No automatic capture restart
or parameter write is permitted. An audit/storage failure blocks new mutations
while preserving the degraded capture-stop exception from section 12.

## 17. Failures and shutdown

Browser closure leaves captures and configured watches running; ordinary leases
expire. Failed status yields unavailable diagnostics and curve gaps. Lost parameter
subscriptions make values stale until fresh observations. OSE restart resets
rate/history generations and marks old drafts for reread. Source generation loss,
invalid/runtime filter failure and disk errors terminate the affected capture.
A lost write reply produces an uncertain operation with no automatic replay.

SIGINT/SIGTERM stops admissions, drains captures where possible and closes sockets
and storage. Deadline expiry marks unknown/incomplete artifacts. SIGKILL/power
loss uses INTERRUPTED recovery next time. Authentication/contract errors are
reported with corrective action; only transient failures receive bounded retries.

## 18. Code and distribution

The independent ose-ws/ project contains pyproject.toml, README, src/ose_ws CLI,
configuration/auth/server, services, schema, storage, compiled static assets,
frontend TypeScript/Vue sources and tests. The frontend tree widget supports typed
parameter editing; recording contracts use the same schema description in a
read-only explorer. Arbitrary message fields do not become scalar measurements.

The package depends on a compatible OSE Python package and available SDL runtimes.
The optional recording extra supplies Python capture dependencies; the native
filter service remains a separate preparation. Frontend dependencies are locked
at build time. OSE engine users do not acquire FastAPI/Vue dependencies merely
by installing OSE.

## 19. Server observability

Structured request logs carry request ID, method, path, result and duration without
parameter values/secrets. Public authenticated application status exposes versions,
capabilities, connection age and shared RPC counters/latency. Authorized diagnostics
and audit identify storage, source and capture failures. Observing HTTP diagnostics
MUST NOT trigger a fresh status read per user. SQLite schema versions are explicit;
a schema newer than the server fails startup.

## 20. Acceptance criteria

| ID | Verifiable result |
| --- | --- |
| A01 | Installed wheel serves its frontend without Node.js/CDN. |
| A02 | OSE absent: web UI starts disconnected and reconnects later. |
| A03 | One or twenty browsers share the same status cadence. |
| A04 | Identical snapshots yield CLI/web-equivalent diagnostics. |
| A05 | Changed epoch/generation resets rates and segments curves while logical watches survive. |
| A06 | Missing/null/stale values are gaps, not fresh zero/held points. |
| A07 | Sustained history obeys sample/allocation budgets. |
| A08 | Reduced curves retain extrema and indicate gaps/reduction. |
| A09 | Slow clients do not block other users, collection or captures. |
| A10 | Local/distributed discovery exposes output/internal points and schemas without generated models. |
| A11 | Filter validation/admission agrees with ose record and executes at the source. |
| A12 | Two captures of one point have independent selection/filter/count/stop state. |
| A13 | Browser closure leaves server capture recoverable after login. |
| A14 | Rotation/cycle obeys existing Recorder ownership and retention. |
| A15 | Source/disk failure gives terminal capture state without stopping business components. |
| A16 | Supported SDL types preserve optional presence and array dimensions. |
| A17 | Integer bounds survive web editing and OSE file import/export exactly. |
| A18 | Previewed fl32/quantized values equal runtime decoding. |
| A19 | Two valid concurrent writes can succeed; OSE's last complete block wins. |
| A20 | CLI/component writes can be overwritten; no concurrency protection is claimed. |
| A21 | Existing parameter RPCs suffice without runtime CAS/revision changes. |
| A22 | External notifications preserve drafts and show the latest remote value. |
| A23 | Partial import reports each outcome without claiming a cross-block transaction. |
| A24 | Retried operation keys do not repeat uncertain effects. |
| A25 | HTTP/WebSocket/export/download enforce rights without value leakage. |
| A26 | Malicious paths/symlinks cannot access artifacts outside owned capture storage. |
| A27 | Graceful stop drains when possible; crash restart yields INTERRUPTED. |
| A28 | Console restart neither writes old parameters nor resumes captures. |
| A29 | A second state-directory owner is rejected safely. |
| A30 | Trees, editing and apply are keyboard accessible. |
| A31 | Administrative uncertain closure is audited, remains undetermined and never replays a command. |
| A32 | Storage failure still permits a visibly non-durable explicit capture stop. |

Tests combine calculations/contract checks, real local/distributed OSE integrations,
validated PCAPNG/MCAP files and frontend interaction checks. Load qualification
must publish hardware, versions, component/status sizes, followed measures, capture
rates/losses and client count. Initial target: 100 components, 128 measures at 2 Hz,
20 browsers; p95 read-to-render under 1 second for components and 1.5 seconds for
curves outside application saturation. These are measurement targets, not real-time
or universal capture-throughput guarantees.

## 21. Delivery sequence

1. Independent package/config/auth/connection/storage and shared Components view.
2. Logical leases, bounded measurement history, charts, export and live recovery.
3. Recording discovery/schema/filters, capture lifecycle, files, quotas and recovery.
4. Existing parameter client integration, policy, typed editor, drafts, normalized
   previews, external updates, import/export and audit.
5. Qualification of local/distributed use, concurrency, saturation, permissions,
   bundled packaging and documented compatibility.

Each feature includes explicit errors and limits. The full version includes all
four workspaces and applicable acceptance criteria, while accepting parameter
overwrites and requiring no OSE wire-protocol extension.

## 22. Repository references

- [OSE diagnostics](monitoring.md), [measurements](measurements.md),
  [recording](recording.md), [parameters](parameters.md).
- [Shared diagnostics](../src/ose/diagnostics.py),
  [shared recording selection](../src/ose/recording/selection.py).
- [Parameter client](../src/ose/parameters/client.py),
  [service](../src/ose/parameters/service.py),
  [values](../src/ose/parameters/values.py).
- [Recorder](../src/ose/recording/recorder.py),
  [catalogue types](../src/ose/codec/catalogue.py).
- [SDL wire descriptor](../../sdl/docs/wire_descriptor.md),
  [SDL type/representation rules](../../sdl/docs/schema.md).
