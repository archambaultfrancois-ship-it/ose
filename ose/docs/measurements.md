# Current application measurements

Components may declare named scalar measurements and replace their current values
during callbacks. `ose-ctl measures` reads those values through the same read-only
status RPC as `top`, locally or through the distributed coordinator. No graph
ports, subscriptions, SDL application types or optional recording dependencies
are required.

Each component generation owns at most 128 measurements. Names follow the internal
recording-point rules: dot-separated ASCII identifiers
`[A-Za-z_][A-Za-z0-9_]*`, no empty segments, at most 200 bytes in the local name.
Declare each name once during `on_start`, before READY. Different components can
declare the same local names. A declared measurement has no value until its first
publication; text displays `-` and JSON uses `null`, distinct from `0.0`.

All values are finite `fl64` scalars. Every emission replaces the previous value.
There are no retained samples, measurement timestamps, queues for measurement
history, or measurement files. Declaration metadata and only the current value
live in RAM. A component replacement starts with freshly declared, empty values;
disconnecting a producer removes its current measurements from live status.
The local supervisor also omits measurements from `last-status.json`.

## Component APIs

Python:

```python
def on_start(ctx):
    ctx.declare_measurement("moteur.temperature")
    ctx.declare_measurement("moteur.vibration.rms")

def on_input(ctx, message):
    ctx.measure("moteur.temperature", message.temperature)
    ctx.measure("moteur.vibration.rms", message.rms)
```

C (`component.h`):

```c
/* on_start: return 0 to report initialization failure. */
if (!ose_declare_measurement(ctx, "moteur.temperature")) return 0;
/* tick callback: return 0 to report failure. */
if (!ose_measure(ctx, "moteur.temperature", temperature)) return 0;
```

Rust:

```rust
// on_start
ctx.declare_measurement("moteur.temperature")?;
// tick callback
ctx.measure("moteur.temperature", temperature)?;
```

Java:

```java
// onStart
ctx.declareMeasurement("moteur.temperature");
// tick callback
ctx.measure("moteur.temperature", temperature);
```

Ada, through the generated component package (for example `Ose_analyse`):

```ada
-- On_Start
Declare_Measurement (Ctx, "moteur.temperature");
-- tick callback, Value is SDL_Runtime.Float64
Measure (Ctx, "moteur.temperature", Value);
```

Declarations after initialization, duplicate/invalid names, exceeding the name
limit, undeclared emissions and calls outside tick callbacks are programming
errors. NaN and infinities are rejected. The C callback API returns 1 on success,
0 on failure and marks the callback runtime as failed; Python/Java/Ada raise and
Rust returns an error. No activation or declaration type argument is needed.

These calls use ordinary CH/1 IPC flow control; they do not contact a dashboard
or wait for a CLI reader. An emission means the update was sent to the adapter,
not that a particular reader has observed it. Local status reports normally
refresh every 100 ms; distributed worker metrics refresh every 500 ms.
Multiple emissions can therefore be coalesced into one observed latest value.
Old component binaries remain valid without measurements. Rebuild generated
containers and update the application runtime before using the new APIs.

Simulation accepts the same declarations and emissions in volatile process
registries. It has no live status endpoint; measurements are excluded from the
logical trace and saved simulation summary.

## CLI readings

```sh
ose-ctl measures                            # one snapshot
ose-ctl measures 'analyse.*'                # select a component's measures
ose-ctl measures 'moteur.*'                 # select local names across components
ose-ctl measures --watch                    # continuous readings, stop with Ctrl+C
ose-ctl measures --interval 1 --count 60 --format jsonl
```

Patterns are case-sensitive globs and match either `component.local_name` or the
local name. Multiple patterns select their union. Quote globs to avoid shell
expansion. `--count N` produces N snapshots, including when combined with `--watch`.
Without either option, one snapshot is produced. `--interval` defaults to one
second and must be finite and at least 0.1 seconds. It does not change the rate at
which components emit or OSE reports status.

```text
2026-11-01T14:30:00+00:00
COMPONENT  MEASUREMENT           VALUE
analyse    moteur.temperature    42.5
analyse    moteur.vibration.rms  2.37
```

JSONL contains one independently sampled object per line:

```json
{"timestamp":"2026-11-01T14:30:00+00:00","epoch":"application-run","measurements":{"analyse":{"moteur.temperature":42.5,"moteur.vibration.rms":2.37}}}
```

`timestamp` is the CLI's UTC reading time, not a producer or synchronized event
timestamp. `epoch` identifies the local broker or distributed coordinator run.
There is no interpolation or recovery of publications between readings. Writing
JSONL to a file is an explicit action by the caller, not storage inside OSE.

Connection options mirror `top`:

```sh
ose-ctl measures --endpoint /app/build/endpoint.json
ose-ctl measures --host 127.0.0.1 --port 7000
ose-ctl measures --distributed --host coordinator --port 7000 \
  --tls-ca ca.crt --tls-cert observer.crt --tls-key observer.key
ose-ctl measures --insecure --host 127.0.0.1 --port 7000
```

The local default is `build/endpoint.json`. Explicit `--host`/`--port` cannot be
combined with `--endpoint`. Distributed TLS uses the same identities and status
access rules as `top`; `--insecure` is restricted to loopback. Repeated readings
reconnect after transport failures and reread endpoint files when reconnecting.
A failed reading contains an error and an empty measurements object, never the
previous successful values. Finite collection exits with status 1 when its final
reading failed. Authentication, invalid configuration and outdated-runtime errors
fail immediately. Ctrl+C exits with status 130.

## Status representation

Updated status replies advertise `measurements_version: 1`. Current values are
included under `components[component].measurements` locally, or under
`components[component].metrics.measurements` in distributed status. Each registry
is a dictionary `{local_name: number_or_null}`. The CLI normalizes both layouts to
the JSONL representation above. Runtime receivers validate names, registry limits
and finite numbers before replacing the current component report.
