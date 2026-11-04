# HTTP and live API

The API prefix is `/api/v1`. Except login and minimal `/health/live` /
`/health/ready`, business routes require a session cookie. Authenticate through
`POST /auth/login` with `{"token":"..."}` or `{}` behind the configured
identity proxy. `GET /session` returns actor, web policy, CSRF token and expiry.
Mutations require `X-CSRF-Token`. `POST /session/renew` explicitly renews idle
time within the eight-hour absolute lifetime; `POST /auth/logout` revokes the
session. `GET /openapi.json` is available to administrators.

The complete route inventory is generated from the installed server. Requests
use JSON objects; duplicated keys, non-finite numbers and excessive nesting
are rejected. Errors use:

```json
{"error":{"code":"PARAMETER_INVALID","message":"Invalid value.","request_id":"opaque-id","details":{"pointer":"/counter"}}}
```

Parameter `int64` and `uint64` values are always decimal strings on the web API,
including repeated elements and fixed arrays. Ordinary OSE import/export files
keep JSON integer literals. Upload/download those files as **text**, without
passing their contents through JavaScript `JSON.parse` / `Number`.
Other large counters are represented as decimal strings when they exceed the
JavaScript exact-integer range. Timestamps use UTC RFC 3339 except measurement
`time` (Unix milliseconds) and log `time_ns` (UTC nanoseconds as a decimal string).
Observation and log `sequence` fields are decimal order strings.

## Resources

| Resource | Operations |
| --- | --- |
| Application | `GET /application`: connection, epoch, versions, capabilities, freshness and service errors. |
| Components | `GET /components`, `GET /components/{id}`: shared diagnostics, never a fresh RPC per browser. The collection includes declared flow `topology`. |
| Link contract | `GET /flow/schema?subscription=...&epoch=...`: source SDL contract for a declared subscription; requires `components.read`. |
| Link Live | `POST /flow-watches`, `GET/DELETE /flow-watches/{id}`: leased read-only latest-publication previews; requires source-topic `flows.read`. |
| Logs | `GET /logs`: bounded in-memory component history with component, level, category and text filters; follow with `after`/`epoch`. Requires component-scoped `logs.read`. FATAL is distinct from CRITICAL. |
| Measures | `GET /measurements`, `POST /measurement-watches`, `DELETE /measurement-watches/{id}`. |
| History | `GET /series?ids=...&start=...&end=...&max_points=...`, `GET /series/export?ids=...`. |
| Capture discovery | `GET /recording-points`, `POST /recording-points/refresh`, `GET /recording-points/{id}/schema`, `POST /recording-filters/validate`. |
| Captures | `GET/POST /recordings`, `GET/PATCH/DELETE /recordings/{id}`, `POST /recordings/{id}/start`, `POST /recordings/{id}/stop`. |
| Artifacts | `GET /recordings/{id}/files`, `GET /files/{id}/download`, `POST /files/{id}/validate`. |
| Parameters | `GET /parameters`, `GET /parameters/{id}`, `GET /parameters/{id}/schema`, `POST /parameters/{id}/validate`, `PUT /parameters/{id}`. |
| Parameter files | `POST /parameter-imports/preview`, `POST /parameter-imports/{id}/apply`, `POST /parameter-exports`. |
| Operations | `GET /operations` (uncertain), `GET /operations/{id}`, administrative `POST /operations/{id}/close-unknown`. |
| Audit | `GET /audit`. |

`GET /components` includes `topology`, or `null` until compatible observation
metadata is available. Version 1 contains `components` keyed by component name
(trigger, input and output descriptors), `external_topics` keyed by topic name,
and `subscriptions` keyed by subscription ID (`from`, `to`, resolved `policy`,
optional `from_topic` and `routing`). Port descriptors include SDL `root` types
when supplied by the runtime. This metadata comes from the running application's
compiled plan through the shared status collector, without commands, environment
variables, schema catalogues or parameter values. Topology is retained on
connection loss; use the enclosing `error`, `observed_at` and `age_ms` to assess
freshness. Component health and rates remain in the enclosing `components`
array. Older version 1 runtimes can omit outputs, roots and external topic
metadata. Graph refreshes use the shared collector.

Link contracts are fetched on demand with `GET /flow/schema`, using a subscription
ID and the epoch from `GET /components`. The response uses the same schema
description as parameter and recording contracts (`root`, `schema_digest`,
`catalogue`, `types`, `presentation_version`, `presentation`). The server reads
the compiled source port contract through the OSE `subscription_schema` RPC;
external topics and direct routes use the same mechanism. This requires
`components.read`, independently of recording permissions and external-client
enablement. An unknown subscription returns 404, an obsolete epoch returns 409
and a disconnected source returns 503. Contracts are not included in periodic
status messages. A compatible OSE runtime is required.

`POST /flow-watches` accepts `subscription`, `epoch`, optional `interval_ms`
(250–1,000; default 500) and `array_offset` (0–1,000,000; default 0). It returns
`id` and `lease_s`; supplying the existing `id` renews that session-owned watch
and updates its cadence/array offset. Creation/renewal/deletion require CSRF.
No durable operation or idempotency key is needed for these ephemeral reads.
`GET /flow-watches/{id}` returns the latest snapshot for recovery or HTTP clients;
`DELETE` releases it, and deleting an already closed watch is idempotent.

Snapshot fields include `watch_id`, `topic`, `epoch`, `status` (`waiting`, `live`,
`unavailable`), `error`, `age_ms`, decimal `version`/`sampled_out` and `decode_ms`.
`sample` is null before a decoded publication or while projecting a new array
offset. Otherwise it contains `epoch`, `generation`, `schema_digest`, decimal
`source_sequence`, console `observed_at` and a bounded `value` tree. Nodes have
`name`, `kind` (`message`, `array`, `complex`, `value`, `absent`, `truncated`),
`type`, optional `children`/`value`, and explicit array `length`/`offset` and
`truncated` markers. All nodes belong to one publication. `sampled_out` counts
intentional tap replacements, not business delivery losses. An unavailable
snapshot can retain its last sample; clients must show its state and age.

Subscribe on `/live` using `stream:"flow"`, `selection:[watch_id]` and a cadence.
`flow.sample` events carry snapshots when their version/state changes, with no
history backlog. Update displayed age locally between events. Browser watches
share a source tap and one decode per observation; endpoint addresses remain
backend-controlled. Watch authorization is rechecked during delivery and lease
maintenance. Logout, rights/session expiry and the final watch's deletion close
the source; WebSocket disconnect releases its subscribed flow watch. Source
changes/failures stop the observer and require a fresh watch. Native source
`recording_state` checks detect idle source changes without retransmitting schemas.

Inventory lists use `items`, `next`, optional `cursor` and `limit` (1–200). Inventory
changes invalidate a cursor with HTTP 409. Measurement watches accept
`measurement_ids` and an optional existing watch `id`; renewal returns
`lease_s: 60`. IDs identify logical component/name pairs, not one generation.

History pages use `segments` and `next`; pass `cursor` to retrieve the next
generation segments. Each page contains at most 200 segments and 50,000 rendered
points. `max_points` accepts 8–5,000 points per segment. Responses announce raw
counts, reduction, available time bounds and retention/budget truncation.

Capture creation accepts `selection` (exact topics, types, globs), `filters`
mapped by exact topic, `label`, `format`, `capacity`, `byte_budget`, `max_size`
and `cycle`. Creation returns a durable operation whose result is a DRAFT with
its concrete selection. PATCH replaces the draft configuration and requires
its current local `revision`. Start and stop return asynchronous operations.

`DELETE /recordings/{id}` requires an explicit `{"delete_files":false}` to keep
files or `{"delete_files":true}` to remove them. It returns a durable operation.
Only `DRAFT`, `STOPPED`, `FAILED` and `INTERRUPTED` captures can be deleted;
an active capture returns `CAPTURE_ACTIVE` in the failed operation. List/detail
responses include `permissions.delete` for the current user. Deletion requires
the owner with `recordings.delete` access or an administrator; sharing read or
stop access never shares deletion rights.

Deletion removes capture and file metadata, while preserving operations and
audit entries. Kept files remain under the returned `retained_directory`,
relative to the state directory, with no remaining web access. File deletion
never follows symbolic links or removes unexpected directory entries. Such
entries produce `CAPTURE_FILES_CHANGED` and leave the capture listed. Disk
and SQLite updates cannot form one transaction: a storage failure may leave
missing files with the capture still listed. Inspect the state before retrying.
The same actor/key/body retrieves the original deletion operation even after
the capture disappears or the console restarts, within the key retention period.
A different body with that key is rejected; a new key for a missing capture
returns HTTP 404. Downloads already holding an open file descriptor may finish.

Commands with effects require an `Idempotency-Key` of 8–200 characters. Persist
the key when implementing an external client. Reuse it only for a retry of
that same actor/target/body; do not generate a new key to retry an uncertain
command. `GET /operations/{id}` can safely be polled. States are `pending`,
`complete`, `failed`, `uncertain`, and `closed_unknown`. A completed operation's
`result` carries the business outcome; inspect per-block import results too.

## Follow component logs

`GET /logs` accepts `components` (comma-separated globs), `levels`
(comma-separated exact names), `logger` (category glob), `search`
(case-insensitive message substring), `limit` (1–200), and optional `after` /
`epoch`. Levels are DEBUG, INFO, WARNING, ERROR, CRITICAL and FATAL. The browser's
minimum-level selector sends that level and all higher severities.

The response contains `epoch`, `items`, `cursor`, `more`, `truncated`, `evicted`
and `retained`. The initial page contains recent matching entries in
chronological order. Continue with the returned `cursor` as `after` and its
`epoch`; `more` requests immediate continuation. These are follow cursors, not
inventory pagination tokens. `truncated` reports expired history or an epoch
change. Reading does not consume logs for other clients.

Each item contains `sequence`, `time_ns`, `component`, `generation`, `level`,
`logger` and `message`. Sequence and UTC-nanosecond timestamps are decimal
strings. The web table formats local time to milliseconds and exposes the
original nanoseconds in its tooltip. Effective web scope is the intersection
of the requested component patterns and `logs.read`; administrators can read
all components. A missing scope is refused; matching filters cannot grant it.
Log follow uses HTTP and is not a WebSocket stream.

## Validate and apply a parameter

1. Read the block and its schema.
2. `POST /parameters/{id}/validate` with `value` and `schema_digest`.
3. Inspect the returned normalized `value`, `encoded_bytes`, `preview_hash`
   and `schema_digest`. Changing the draft invalidates the preview.
4. `PUT /parameters/{id}` with those returned fields and a new explicit
   `Idempotency-Key`.

The preview binds the wire payload, root/catalogue and presentation constraints.
It is **not** a comparison against the old parameter value. Other writers can
still replace the block between steps, and applying can overwrite their edits.
No revision or `If-Match` exists. A successful operation returns `result: stored`.

Imports preview all authorized blocks before writing any. Plans are private to
their creator, volatile, epoch-bound and expire after 10 minutes. Apply is
sequential, stops on the first refused/uncertain result and reports not-attempted
blocks. There is no atomic transaction across blocks. A repeated apply with
the same key returns the durable operation even if its volatile plan expired.

Administrative closure accepts `{"note":"..."}` and only applies to uncertain,
non-running operations. The final result remains `undetermined`; there is no
replay, inferred acknowledgement or success conversion.

## WebSocket

Open `/api/v1/live` using the session cookie and an allowed Origin. Subscribe:

```json
{"action":"subscribe","subscription_id":"my-measures","stream":"measurements","selection":["measurement-opaque-id"],"interval_ms":1000}
```

Streams: `application`, `components`, `measurements`, `recordings`, `parameters`,
`operations`. Parameter selections are authorized block IDs; measure selections
are authorized logical IDs. `interval_ms` is between 250 and 60000. At most 32
subscriptions are admitted per connection. Unsubscribe using `action:
unsubscribe` and the `subscription_id`.

Messages carry `protocol: 1`, a connection-local `stream_id`, ordered decimal
`sequence`, `subscription_id`, `type`, `observed_at` and `data`. An initial
`snapshot_required` asks the client to load HTTP resources. Components,
application, recordings and operations send coalescible invalidations;
`parameters.changed` contains a block ID and asks for an authorized reread.
These notifications are not an exhaustive write journal.

`measurements.points` includes a segment ID and observed points since the last
send, split into batches of 128 points. If backlog exceeds the live budget,
`resync_required` requests HTTP history. The server never waits on a browser
to perform collection or capture. There is no durable event replay.

Answer application `ping` with `{"action":"pong"}`. On reconnection, use the
new stream ID, reload HTTP snapshots, renew watches and fill curves from the
remaining history. Reconnect with randomized exponential backoff (1–30 s).
Session expiry/revocation or permission loss closes the channel. Commands use
HTTP, never WebSocket.
