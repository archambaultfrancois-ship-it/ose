# Component IPC framing and shared services

Application model v2 uses [CH/2 input callbacks](input-protocol.md) in Python,
C, Rust, Java and Ada. This page specifies common framing, lifecycle and context
services, independently of the network [CENV/2](../docs/cenv2.md) envelope.
Rebuild generated containers when upgrading the component protocol.

## Framing and lifecycle

stdin/stdout are binary protocol streams; diagnostics go to stderr. Each frame
is `u32 big-endian length | u8 ASCII opcode | body`. Length includes the opcode
and is between 1 and 1,048,576 bytes. Readers handle partial reads.

All lengths are unsigned and big endian. Port and local names are UTF-8,
encoded as `u16 byte_length | bytes`. Catalogue text uses a u32 byte length.
SDL payloads in parameter, recording and publication frames contain ordinary
SDL bytes without S2PS/2; CH/2 input records carry complete S2PS/2 envelopes.
Each message retains its catalogue association and dynamic contract checks.

| Opcode | Direction | Body |
| --- | --- | --- |
| `I` INIT | Engine → component | JSON component configuration, parameters and port catalogues |
| `R` READY | Component → engine | Exactly byte `0x02` |
| `T` ACTIVATE | Engine → component | One input reference; see [CH/2](input-protocol.md#activation-and-publication) |
| `P` PUBLISH | Component → engine | Output name, optional application ID and SDL payload; see CH/2 |
| `F` FINISHED | Component → engine | Empty |
| `X` STOP | Engine → component | Empty |
| `U` PARAMETER UPDATE | Engine → component | Name, catalogue text, remaining SDL payload |
| `S` PARAMETER SET | Component → engine | Name, remaining SDL payload |
| `A` RESULT | Engine → component | Byte 1 and operation-specific result, or byte 0 and UTF-8 error |
| `N` RECORDING DECLARE | Component → engine, before READY | Name, root, NUL, description, NUL, catalogue |
| `E` RECORDING ENABLE | Engine → component, between callbacks | NUL-separated active local names; empty disables all |
| `C` RECORDING SAMPLE | Component → engine, during a callback | Name, remaining SDL payload |
| `M` MEASUREMENT DECLARE | Component → engine, before READY | Name, no trailing bytes |
| `V` MEASUREMENT VALUE | Component → engine, during a callback | Name, exactly one big-endian IEEE-754 fl64 |
| `L` LOG | Component → engine, including initialization and shutdown | u8 level, category name, remaining UTF-8 message |
| `Q` INPUT REQUEST | Component → engine | [CH/2 inbox or timer operation](input-protocol.md#input-requests) |

The engine sends INIT followed by initial readable parameter blocks. The
component initializes codecs, invokes its optional start hook and sends READY.
Only one callback is in flight per component. Successful completion sends an
empty FINISHED; failure sends none. CH/2 defines deferred withdrawals and staged
publications. Parameter writes and input requests are synchronous: each RESULT
belongs to the outstanding SET or INPUT REQUEST. Parameter updates never
interrupt an active callback. See [shared parameters](../docs/parameters.md).

STOP requests clean exit, including while waiting for an activation. The
adapter's 0.5-second grace period includes both writing STOP and waiting for exit;
a full stdin pipe cannot postpone it. It then sends SIGTERM, waits up to one
second and kills/reaps the component if necessary.

## Logs

LOG levels are 10 DEBUG, 20 INFO, 30 WARNING, 40 ERROR, 50 CRITICAL and 60 FATAL.
Categories are at most 200 bytes; messages are nonempty and at most 8192 bytes.
SDKs truncate oversized text at UTF-8 boundaries and append `… [truncated]`
within that limit. Truncation does not fail the component. NUL is forbidden
in both fields.

LOG frames are buffered in bounded volatile memory and forwarded independently
of application queues. FATAL does not stop the component automatically. See
[logs](../docs/logs.md) for retention, loss reporting and console semantics.

## Measurements

Measurements retain one current finite fl64 per declared name, initially absent.
Names use dot-separated ASCII identifiers and a 200-byte limit, with up to 128
independent names per component. Duplicate or late declarations, undeclared
emissions, malformed bodies and nonfinite values fail the component.

These eight-byte scalars never enter application queues, recording points or
logical traces. They are volatile and appear in live status; see
[current measurements](../docs/measurements.md).

## Internal recording

Declarations are fixed before READY and scoped to one component generation.
Names are dot-separated ASCII identifiers, at most 200 bytes. Descriptions
contain at most 1024 UTF-8 bytes; NUL is forbidden in declaration fields. Up to
128 points may be declared. The engine validates the catalogue and root and
exposes `<instance>.record.<local-name>` through the recording service.

Samples never enter application subscriptions. ENABLE applies before the next
callback and stays fixed during it. Components start with all points disabled.
Typed contexts own prepared encoders and skip encoding disabled points.

Each callback may emit at most 256 KiB of SAMPLE bodies, including encoded
local names. Emission returns `queued`, `disabled` or `dropped`; queued means
handed to local IPC, not persisted. The local adapter drains samples into an
independent bounded queue (64 waiting samples, 1 MiB including its in-flight
sample) and transfers them to the broker separately from application processing.
Workers feed local recording taps directly. Pipe flow control still applies;
capture is best effort.

Simulation accepts declarations with points disabled. See
[component recording APIs](../docs/recording.md#component-owned-internal-points).
