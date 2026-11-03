# Component logs

The component SDKs deposit structured logs into the CH/1 stream. The adapter
buffers them in memory and forwards bounded batches to the local broker or
distributed coordinator. The runtime retains recent entries independently of
application subscriptions. Logs are available even when no web console is open.
There are no log files, database writes, or recording-point declarations.

Each entry contains `sequence`, `time_ns`, `component`, `generation`, `level`,
`logger` (an optional category), and `message`. The runtime supplies component
identity and generation from the established component session. `time_ns` is the
adapter's wall-clock timestamp when reading the deposit, expressed as a decimal
string of UTC nanoseconds. It is separate from simulated time. Sequence numbers
are also decimal strings, ordered within a runtime epoch.

Levels are `DEBUG` (10), `INFO` (20), `WARNING` (30), `ERROR` (40), `CRITICAL` (50),
and **`FATAL` (60)**. FATAL is distinct from CRITICAL. Logging a FATAL entry does
not terminate the component: the handler remains responsible for its return
value or exception.

## Deposit from an SDK

Logs can be emitted during initialization, processing and clean shutdown.
Messages must be nonempty UTF-8 text. The SDK truncates messages exceeding 8,192
bytes at a complete UTF-8 character boundary and appends `… [truncated]`; the limit
includes this marker. Truncation is a successful deposit and does not fail the
component. Category names are at most 200 bytes. Neither field may contain NUL.
No SDL schema is needed.

```python
ctx.log('INFO', 'Analyzer started', 'lifecycle')
ctx.log('FATAL', 'Unable to process the signal', 'detection')
# Raw Python components use the same method on ComponentSession.
```

```c
ose_logf(ctx, OSE_LOG_FATAL, "detection", "Unable to process the signal");
```

```rust
ctx.logf(sdl_ose_component::LogLevel::Fatal, "detection",
         format_args!("Unable to process the signal"))?;
// Raw SDK: sdl_ose_component::logf(level, category, format_args!(...))?;
```

```java
ctx.logf(org.sdlose.Component.LogLevel.FATAL, "detection", "Unable to process the signal");
```

```ada
Ose_source.Log (Ctx, Ose_Component.Fatal, "detection", "Unable to process the signal");
-- Raw SDK: Ose_Component.Log (Level, Logger, Message);
```

## Formatted log deposits

C exposes a single log deposit function, using printf-style formatting:

```c
int ose_logf(OseContext *ctx, OseLogLevel level,
             const char *logger, const char *format, ...)
    OSE_PRINTF_FORMAT(4, 5);

ose_logf(ctx, OSE_LOG_WARNING, "detection",
         "motor=%u rms=%.3f threshold=%.3f", motor, rms, threshold);
/* Preformatted text (including literal percent signs) uses the same service. */
ose_logf(ctx, OSE_LOG_INFO, "lifecycle", "%s", message);
```

On GCC and Clang, `OSE_PRINTF_FORMAT` expands to the compiler's
`__attribute__((format(printf, ...)))`. Enable `-Wformat`, or
`-Werror=format` to reject mismatches between literal formats and argument types.
On other compilers the annotation is empty; the same function remains available.
Include `ose_callbacks.h`, or the generated `component.h`, to use this API.

The C formatter uses an 8,193-byte buffer including its terminating NUL. Oversized
results are truncated at a UTF-8 boundary with `… [truncated]` inside the byte limit.
Empty, failed or embedded-NUL results return zero without emitting a frame and
mark the context failed. Truncation returns one and does not mark the context
failed. Success still means local IPC acceptance.

Rust and Java also expose only `logf` for log deposits, on both the raw SDK and
the callback context. Formatting follows each language's conventions:

```python
ctx.logf('WARNING', 'motor=%d rms=%.3f', motor, rms, logger='detection')
# Percent-style formatting is checked at runtime. Existing log accepts f-strings:
ctx.log('WARNING', f'motor={motor} rms={rms:.3f}', 'detection')
```

```rust
ctx.logf(sdl_ose_component::LogLevel::Warning, "detection",
         format_args!("motor={} rms={:.3}", motor, rms))?;
// format_args! checks literal formats and argument traits at compile time.
// The raw SDK also exposes logf(level, category, format_args!(...)).
// Preformatted text uses the same service:
ctx.logf(sdl_ose_component::LogLevel::Info, "lifecycle", format_args!("{}", message))?;
```

```java
ctx.logf(org.sdlose.Component.LogLevel.WARNING, "detection",
         "motor=%d rms=%.3f", motor, rms);
// String.format with Locale.ROOT; mismatches raise IllegalFormatException.
// Preformatted text (including literal percent signs):
ctx.logf(org.sdlose.Component.LogLevel.INFO, "lifecycle", "%s", message);
// Raw SDK: org.sdlose.Component.logf(output, level, category, format, arguments...).
```

```ada
Ose_source.Log (Ctx, Ose_Component.Warning, "detection",
                "motor=" & Integer'Image (Motor) & " rms=" & Float'Image (Rms));
```

Ada uses typed string composition and `'Image` rather than a C-style variadic
formatter. Python and Java format errors are detected at runtime, while Rust's
format macro checks types during compilation. Every language retains the final
8,192-byte UTF-8 message limit, truncating with the same marker when necessary.
Python and Ada's plain `log` APIs never interpret percent signs. In C, use
`ose_logf(ctx, level, category, "%s", message)` for such text.

Callbacks retain their existing threading rules. These APIs use normal CH/1
pipe flow control; a successful call means the local stream accepted the frame,
not that a remote reader received it. They are best effort, not a hard real-time
or durable audit facility. Existing stderr output stays on stderr and is not
automatically converted into log entries.

## Retention and streaming

Each adapter buffers at most 128 waiting entries within 256 KiB; new entries
are dropped when full. The cumulative `logs_dropped` count appears in component
metrics. A batch contains at most 32 entries within roughly 64 KiB of raw text.
Batches wake the report sender immediately and can travel in log-only reports,
independently of the 500 ms metric coalescing interval and application deliveries.
Queued bursts drain in bounded batches with normal network backpressure.
Initialization and clean-shutdown deposits are flushed best effort as well.

The runtime retains at most 10,000 entries within 8 MiB. Oldest entries expire
when either bound is reached. Entries survive a component disconnect or
replacement, retaining their original generation. They disappear when the
broker/coordinator exits. Simulation accepts SDK deposits into a bounded local
buffer but does not expose a live log service or add logs to deterministic traces.

The `logs` control RPC accepts a `filters` object: `components` (list of globs),
`levels` (list of exact level names), `logger` (glob), `search` (case-insensitive
message substring), `limit` (1–200), and optional `after`/`epoch`. Without a
cursor it returns the most recent matching entries, chronologically ordered.
Subsequent requests use the returned `cursor` and `epoch` to follow new entries.
`more` requests immediate continuation. Replies are also bounded by a 192 KiB
entry budget. `truncated` signals retention loss or an epoch change; `evicted`
counts expired records. Filtering advances the cursor across nonmatching entries.

## Read through ose

```sh
ose logs
ose logs 'analyzer_*' --level ERROR --level CRITICAL --level FATAL --follow
ose logs --level FATAL --format jsonl
ose logs 'sensor_*' --logger 'acquisition.*' --search timeout -f
ose app logs -f
```

`--level` selects exact levels and can be repeated. Default output is readable
text; `--format jsonl` emits one JSON record per line. Terminal control characters
are escaped in text output. Follow mode reconnects after transport loss and
reports history gaps. Reading does not consume or delete logs for other clients.

Local clients use `build/endpoint.json` by default. Explicit `--endpoint` or
`--host`/`--port` select another runtime. Distributed clients use `--distributed`
with `--tls-ca`, `--tls-cert`, and `--tls-key`, or `--insecure` on loopback.

## Web console

**Logs** is the second navigation item, immediately after **Components**. It
filters by component names/globs, minimum severity (including FATAL), category,
and message text. The view follows the in-memory stream, can pause/resume or
refresh manually, and exports displayed entries as JSONL. Pause affects the
display only. The view keeps up to 1,000 entries within 2 MiB and reports gaps.

`GET /api/v1/logs` takes `components` and `levels` as comma-separated strings,
plus `logger`, `search`, `limit`, `after`, and `epoch`. It uses the existing
authenticated session. Web policy requires `logs.read`, scoped to component
names or globs, or administrator access. Filters cannot expand those rights.
Log text is rendered as text, never interpreted as HTML.
