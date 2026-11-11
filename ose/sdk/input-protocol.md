# Component protocol CH/2

CH/2 runs on a [private Unix socketpair](../docs/component-transport.md).
Standard input, output and error are available to the component and its libraries.
Rebuild generated containers and native SDK binaries when migrating from stdio.

CH/2 runs one callback per activating input. It is selected by application model
version 2 and READY body `0x02`. Engines reject other READY bodies for v2 components.
There is no combined input snapshot and no `on_tick` callback. The initial
implementation generates Python, Rust, C, Java and Ada containers for local real-time or
simulated runs; see the [Rust callback API](../docs/rust-inputs.md) and
[C callback API](../docs/c-inputs.md) and [Java callback API](../docs/java-inputs.md).
The [Ada callback API](../docs/ada-inputs.md) is qualified with GNAT 12. Java's
default source/API/bytecode target is qualified using JDK 17; execution on an
actual JVM 7 is deferred and does not block removal of obsolete paths.

Framing remains `u32 length | u8 ASCII opcode | body`, big endian, with length
including the opcode and bounded to 1–1,048,576 bytes. INIT is still JSON.
`I`, `X`, parameter `U/S/A`, recording `N/E/C`, measurement `M/V` and log `L`
use the [shared service layouts](protocol.md). Parameter and input requests are
synchronous; the `A` reply belongs to the outstanding `S` or `Q` operation.
There is no unsolicited parameter UPDATE inside an active callback.

## Primitive fields

All integers are unsigned and big endian. Text is nonempty UTF-8, encoded as
`u16 byte_length | bytes`; catalogue text uses a u32 byte length instead.
An optional uint64 is `u8 present`, then a u64 only if present. The flag must
be 0 or 1. Missing and zero are distinct. Fields and complete frames must fit
their limits; unknown flags, truncated fields and trailing bytes are rejected.

A message record is:

```text
u8 present
if present == 1:
    text engine_handle
    text source_topic
    optional_u64 application_id
    u64 received_ns
    catalogue_text catalogue
    remaining bytes: complete, nonempty S2PS/2 envelope
```

A missing record has only byte zero. Each record carries its own catalogue;
decoding and compatibility checks stay dynamic per message. CH/2 does not change
SDL, dynamic network catalogue discovery or CENV behavior. The engine handle
identifies a publication; the optional application ID selects messages and never
replaces retry/deduplication identities.

## Activation and publication

| Opcode | Direction | Body |
| --- | --- | --- |
| `R` READY | Component → engine | Exactly byte `0x02` |
| `T` ACTIVATE | Engine → component | u64 time_ns, u64 activation_index, u64 due_ns, u64 elapsed, u8 event, text input_port, record |
| `P` PUBLISH | Component → engine | text output_port, optional_u64 application_id, remaining SDL payload |
| `F` FINISHED | Component → engine | Empty |

The index starts at one and increases by one. Time is monotonic uint64
nanoseconds; `due_ns <= time_ns`. Event 1 means message (record present,
`elapsed=1`), event 2 means timeout (record absent, `elapsed>=1`). One-shot
timers always report elapsed one. Periodic and inactivity expirations may
coalesce missed deadlines. A record cannot have a future receipt timestamp.

PUBLISH contains an ordinary SDL payload without S2PS/2. The engine
wraps and validates it with the declared output contract. Both SDK and engine
check that this particular input event can activate the requested output.

The engine stages publications until valid FINISHED. Normal Python return,
including `False`, sends FINISHED. Exceptions and process termination do not.
Rust callbacks send FINISHED only on `Ok(())`; an `Err` or panic fails execution
without committing withdrawals or publications.
C callbacks retain their existing nonzero-success/zero-error convention. An SDK
error or `ose_fail` marks failure even if the handler returns nonzero. Failed C
callbacks send no FINISHED; normal successful execution sends an empty FINISHED.
Java callbacks return void: normal return sends FINISHED; an escaping exception
or Error sends none and cancels withdrawals and staged publications.
Ada sends FINISHED on normal procedure completion; an escaping exception sends
none and cancels withdrawals and staged publications.
There is no application success/failure status in FINISHED. An invalid frame
fails processing rather than acting as a negative business result.

## Input requests

`Q` is a synchronous component → engine request:

```text
u8 operation
text input_port
operation 1 get / 2 pull / 3 remove:
    u8 selectors  # bits: application ID 1, engine handle 2, source topic 4
    u64 application_id   # if bit 1
    text engine_handle   # if bit 2
    text source_topic    # if bit 4
operation 4 arm:
    optional_u64 delay_ns  # absent: use model delay; present: strictly positive
operation 5 disarm:
    no further fields
```

The `A` result is byte 0 followed by UTF-8 error text, or byte 1 followed by:

| Request | Success result after byte 1 |
| --- | --- |
| get / pull | Message record, including absence byte 0 |
| remove | Byte 1 if selected, byte 0 if absent |
| arm / disarm | Empty |

Message access requires a Pull input and an active callback. It never blocks.
Selection defaults to the oldest eligible message; application ID, exact engine
handle and source selectors can be combined. An application ID may match several
FIFO records. Absence is a successful result, not a protocol error.

Eligible handles are frozen at callback selection. New arrivals are accessible
at the next activation; existing messages are not reserved against eviction.
`get` does not consume. `pull` and `remove` immediately hide the selected handle
from later reads in this callback and journal its removal. FINISHED commits these
withdrawals and successful Push FIFO consumption, releasing capacity before
outbound publication waits. Failure cancels the journal. Overflow eviction is
independent and is not undone. Saved inbox references expire with the callback.

Arming/disarming requires a declared timer and is allowed during initialization
or an active callback. Arming starts a delay, with no immediate activation.
Periodic cadence after its next deadline uses the model period. Timer controls
are applied when requested and are not rolled back with the withdrawal journal.
Parameters, measurements, logs and recording likewise retain their own semantics.

Each callback is limited to 65,536 input requests and 16 MiB total request/reply
bodies. The same bound applies to initialization input requests. Error text is
bounded to 4 KiB. An oversized record reply is rejected before withdrawal.
Engines also reject input records too large for a complete ACTIVATE frame.
Callback publications are bounded separately (16 MiB by default).

## Simulation

Before dispatching a wave, the simulator selects every reference and freezes each
component's eligible inbox handles. Components own distinct inboxes. Physical
callback completion cannot admit another component's outputs during the wave.
Withdrawals commit per successful callback; outputs cross a wave barrier in
component-name and emission order. Input-operation traces are emitted in that
same stable component order. A failing component does not roll back withdrawals
already committed by another successful callback; this is not a global transaction.
