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
JavaScript exact-integer range. Timestamps are UTC RFC 3339; measurement `time`
is Unix milliseconds and `sequence` is a decimal observation-order string.

## Resources

| Resource | Operations |
| --- | --- |
| Application | `GET /application`: connection, epoch, versions, capabilities, freshness and service errors. |
| Components | `GET /components`, `GET /components/{id}`: shared diagnostics, never a fresh RPC per browser. |
| Measures | `GET /measurements`, `POST /measurement-watches`, `DELETE /measurement-watches/{id}`. |
| History | `GET /series?ids=...&start=...&end=...&max_points=...`, `GET /series/export?ids=...`. |
| Capture discovery | `GET /recording-points`, `POST /recording-points/refresh`, `GET /recording-points/{id}/schema`, `POST /recording-filters/validate`. |
| Captures | `GET/POST /recordings`, `GET/PATCH /recordings/{id}`, `POST /recordings/{id}/start`, `POST /recordings/{id}/stop`. |
| Artifacts | `GET /recordings/{id}/files`, `GET /files/{id}/download`, `POST /files/{id}/validate`. |
| Parameters | `GET /parameters`, `GET /parameters/{id}`, `GET /parameters/{id}/schema`, `POST /parameters/{id}/validate`, `PUT /parameters/{id}`. |
| Parameter files | `POST /parameter-imports/preview`, `POST /parameter-imports/{id}/apply`, `POST /parameter-exports`. |
| Operations | `GET /operations` (uncertain), `GET /operations/{id}`, administrative `POST /operations/{id}/close-unknown`. |
| Audit | `GET /audit`. |

Lists use `items`, `next`, optional `cursor` and `limit` (1–200). Inventory
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

Commands with effects require an `Idempotency-Key` of 8–200 characters. Persist
the key when implementing an external client. Reuse it only for a retry of
that same actor/target/body; do not generate a new key to retry an uncertain
command. `GET /operations/{id}` can safely be polled. States are `pending`,
`complete`, `failed`, `uncertain`, and `closed_unknown`. A completed operation's
`result` carries the business outcome; inspect per-block import results too.

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
