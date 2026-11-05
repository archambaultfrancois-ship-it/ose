# Live diagnostics

`ose top` observes a running local application or distributed graph. It uses
only the read-only status RPC: no model changes, subscriptions or message files.
Restart applications with the updated OSE runtime before using this command.

```sh
ose top                              # build/endpoint.json
ose top --endpoint /app/build/endpoint.json
ose top --host 127.0.0.1 --port 7000  # local broker
ose top --distributed --host coordinator --port 7000 \
  --tls-ca ca.crt --tls-cert observer.crt --tls-key observer.key
ose top --distributed --insecure --host 127.0.0.1 --port 7000
```

Distributed TLS uses a CA-signed client certificate and verifies the server's
`coordinator` identity. Status access does not require a worker identity.
`--insecure` is explicitly restricted to loopback, as for other distributed tools.
The local default endpoint is reread when reconnecting after a connection error.

The display refreshes every second. Up/Down selects a component, Enter toggles
its details, Space freezes/resumes the display, and `q` quits. Collection continues
while the display is frozen. Ctrl+C also exits and restores the terminal.
Redirecting stdout produces successive plain-text reports without reading stdin;
`--count N` limits their number and uses persistent reports even on a terminal.
`--interval SECONDS` changes the refresh rate
(minimum 0.1 seconds).

## States and colors

| Color | State | Evidence |
| --- | --- | --- |
| Green | `ACTIVE` | Completed ticks in the recent window or a tick in progress. |
| Amber | `ATTENTION` | FIFO at least 80% full, admission refusal attempts, retries, or a periodic tick duration exceeding its configured period. |
| Red | `LOSS/ERROR` | Recent FIFO drops, exhausted/failed deliveries, invalid data, network admission loss or unavailable destinations. |
| Red | `UNAVAILABLE` / `STALE` | Disconnected component in an activated graph, lost status connection, or running worker metrics older than three seconds. |
| Cyan | `WAIT INPUT` | Missing required input whose timeout has not expired. |
| Dim | `STARTING` / `IDLE` | Startup barrier, or no recent processing activity. |

Every state remains readable without color. The default `--color auto` uses ANSI
colors only on a terminal, respects `NO_COLOR` and avoids `TERM=dumb`.
Use `--no-color` (or `--color never`) to disable colors, or `--color always` to
force them. JSONL never contains ANSI sequences.

## Reading the report

Local adapters and distributed workers send an initial diagnostic snapshot. Each
status RPC then enables detailed reports for three seconds, renewed by further
status reads (including `top`, `measure`, `inspect` and the web console). The first
read after a quiet period requests a fresh snapshot and waits at most 750 ms for
ready runtimes. This wait leaves ACKs and other control RPCs usable. An unavailable
reporter leaves its cached values and their actual `report_age_ms` visible.

While observed, processing, queue and measurement changes coalesce over 500 ms;
idle runtimes report details every second. Without readers, runtimes retain the
latest values locally and send only a small presence report every second, avoiding
queue, timing and measurement snapshots. `presence_age_ms` tracks these lightweight
reports separately. Logs wake their sender immediately in either mode. Older
runtimes without demand negotiation retain their original reporting cadence.

The table shows recent input admissions/s, publications/s and completed ticks/s,
the fullest FIFO's depth/capacity, recent delivery losses, and tick p95 in ms.
Details show each input and outgoing delivery queue, its capacity, FIFO/latest
policy, age, counters and the evidence for alerts. Direct delivery queues come
from their producer worker; broker delivery queues come from the broker or
coordinator. Pending messages remain in RAM; this command does not consume them.

Rates and counter alerts use a rolling window, five seconds by default
(`--window`, between 0.1 and 60 seconds). Sampling includes the observation just
before the window boundary; the actual interval is available as
`rate_span_seconds` in JSONL. The first sample, stale reports and restarts show
unknown rates (`-`). Epoch/generation changes and counter resets start a new
baseline. Cumulative counters remain available in queue details; they are not
repeatedly reported as new incidents. Queues can change between runtime reports.

`LOSS` counts dropped FIFO records and failed delivery records across the queues
shown for that component. These are queue events, not a count of unique source
messages across all subscribers. Refusals count admission **attempts** and are
amber: a wait or retry may subsequently succeed. Replacing the retained `latest`
value is normal operation and does not trigger saturation/loss alerts. Different
input/output rates alone do not trigger an alert.

Tick p95 measures wall time driving the component protocol, including IPC and
component execution, excluding time the adapter spends publishing and waiting for
downstream admission. It is **not CPU time**. It covers at most 256 completed
ticks in the last five seconds; no recent samples means `-`. The detailed `busy`
duration measures the entire tick currently in progress, including delivery waits.
Exceeding a configured period is a diagnostic hint; OSE arms the next period
after tick completion, so it is not a missed-deadline counter.

## Flow metadata and SDL contracts

The `status` response includes observation metadata derived from the compiled
plan: component triggers, input/output roots, external topics and declared
subscription endpoints and delivery policies. This lets the Web Console draw
the application flow without a local model checkout.

The read-only `subscription_schema` RPC accepts a `subscription` ID and optional
`side` (`source`, the default, or `destination`). It returns the selected port's
`root`, `from`, `to`, `side` and application `epoch` in the response header,
with its SDL catalogue as the binary body. The broker and distributed
coordinator serve this from the compiled plan, including direct routes and
external topics, without contacting a worker or opening a recording. Clients
should match the returned epoch to their displayed graph before showing the
contract. Schema catalogues are fetched on demand and stay out of periodic
status responses.

The Web Console's **Diagnose compatibility** action reads both contracts and
uses SDL's directional comparison with required destination fields enforced,
as at application build time. The report explains field-ID matches, nested
differences, skipped/defaulted fields, arrays, enums and wire representations.
The check concerns compiled contracts; it does not validate live message values
or confirm downstream processing.

The Web Console's **Live** link inspector samples publications through the same
bounded read-only taps as recording, keeping one queued message and writing no
files. `recording_state` returns recording-point metadata without a catalogue;
the console uses it to detect source/generation changes between observations.
Local outputs/external topics are observed at the broker, distributed component
outputs at their producer worker, and distributed external topics at the
coordinator. These are source observations; they do not prove downstream
admission or processing. Multiple web watches share one source observer.
The inspector may skip publications and truncate large values explicitly;
application delivery queues and policies stay independent. Its tap still adds
per-publication accounting/frame-fit work and uses the source's recording quotas.

## Scripted collection

```sh
ose top --format jsonl --count 60 > diagnostics.jsonl
ose top --no-color --count 1
```

Each JSON line contains the timestamp, epoch, component states, recent rates,
queue policies/counters and alerts with their severity. Connection errors produce
`UNAVAILABLE` reports and trigger reconnection. A finite collection exits with
status 1 if its last status request failed; component alerts remain report data.
Authentication/configuration errors fail immediately.
