# Binary network envelope CENV/2

CENV/2 replaces CENV/1 on TCP, UDP and authenticated distributed connections.
There is no CENV/1 negotiation or fallback. All participants must be upgraded
together, including native client libraries. Application model version and CH
component protocol version are independent of the network envelope version.

Only envelope metadata changes. DATA/PUBLISH bodies remain unchanged S2PS/2
envelopes. Shared parameter bodies remain ordinary SDL payloads. Existing JSON
control-result and metrics **bodies** remain JSON; catalogue responses remain
raw UTF-8. Contract registration, unknown-catalogue requests, per-message
compatibility checks, catalogue-ID collision checks, ACK/retry identities and
generation fencing keep their existing behavior. No connection or route fixes
the contract of future messages.

## Framing and limits

```text
TCP only: u32 packet_length
packet:
    bytes[2] magic = "CE"
    u8 version = 2
    u8 opcode
    u32 metadata_length
    bytes[metadata_length] binary_metadata
    remaining bytes: unchanged body
```

All integers and IEEE-754 numbers are big endian. The eight-byte packet prefix
and metadata count toward the complete packet limit; TCP's four-byte length is
outside it. Defaults remain 1 MiB per TCP packet and 1200 bytes per UDP datagram.
Metadata is limited to 65,536 bytes. UDP has no TCP length prefix or fragmentation.
Readers reject obsolete/unknown versions, opcodes, value tags, flag bits, invalid
UTF-8, truncated fields, duplicate object keys and trailing metadata. A malformed
TCP packet closes its connection; malformed UDP datagrams are counted and dropped.

Bodies are opaque to CENV and may contain any bytes. Returned Python payloads
own immutable storage independently of receive-buffer reuse. The SDL layer
continues to validate body contents and catalogue associations.

## Compact opcodes

Compact fields are positional. `text` is `u16 UTF-8_byte_length | bytes`; `u64`
preserves all 64 bits. There is no null or absent required field. Logical field
validation remains the responsibility of the operation handler.

For opcodes with optional fields, metadata starts with one flag byte. Required
fields follow in table order, then present optional fields in increasing bit
order. ACK has no optional fields and **no flag byte**.

| Opcode | Operation | Required fields in wire order | Optional fields |
| --- | --- | --- | --- |
| 1 | DATA | subscription:text, id:text, epoch:text, sequence:u64, topic:text | bit 0 app_id:u64 |
| 2 | ACK | subscription:text, id:text | None |
| 3 | PUBLISH | request:u64, topic:text | bit 0 app_id:u64 |
| 4 | RESULT | request:u64 | bit 0 content:text, bit 1 error:text, bit 2 error_code:text |
| 5 | DELIVER | request:u64, subscription:text, id:text, epoch:text, sequence:u64, source_token:text, target_token:text, source:text, topic:text | bit 0 app_id:u64 |

Application ID is optional; absence and zero are distinct. It is an unsigned
integer and cannot be a boolean, string, floating-point number or null. Engine
publication IDs and generation tokens retain their existing string values and
purposes. This transition does not replace them with application IDs.

An encoder uses a compact opcode when all required fields have its binary types,
optional fields match their types, and no additional metadata keys are present.
Otherwise it uses opcode 0. This keeps existing control metadata, distributed
credentials and future extensions expressible without silently dropping fields.
Application-ID validation applies to both forms for DATA/PUBLISH/DELIVER.

## Opcode 0: binary object metadata

Opcode 0 encodes the complete metadata object, including nonempty string `op`.
It is a binary alternative within CENV/2, never a CENV/1 or JSON fallback.
Operation names outside the compact table, such as `catalogue`, `hello`,
`parameters_get` and recording RPCs, use this form. Existing operation handlers
retain their field and authorization checks.

Each value starts with a u8 tag:

| Tag | Value | Following bytes |
| --- | --- | --- |
| 0 | null | None |
| 1 | false | None |
| 2 | true | None |
| 3 | uint64 | u64 |
| 4 | negative int64 | i64; zero/positive values are rejected |
| 5 | finite float64 | IEEE-754 f64; NaN/infinities are rejected |
| 6 | UTF-8 string | u32 byte length, then bytes |
| 7 | array | u32 item count, then tagged values |
| 8 | object | u32 field count, then key/value pairs |

Each object key is a `u32 byte length | UTF-8 bytes`, without a value tag.
Keys must be unique. Writers sort keys lexicographically by UTF-8 bytes;
readers accept either order. Empty strings and objects are representable.
Integers outside int64/uint64 are rejected, without rounding to float64.
Nesting beyond 64 levels is rejected (the top-level object has depth zero).
Container counts must fit the remaining metadata before allocation or iteration.
Metadata and body boundaries are enforced independently.

## Dynamic SDL contracts

The sender still supplies the per-message catalogue identity inside S2PS/2.
When the receiver has not cached that identity, it requests the catalogue over
its existing TCP control connection, checks the full identity/text association,
compares it with the input contract, and validates/decodes that message. Changing
catalogues on an established subscription requires no route reconfiguration.
Schema rejection, cache bounds and full-text collision checks are unchanged.

## Implementation and qualification

Python broker/adapter/client transports share `ose.transport.protocol`. Native
Rust clients use the same layout in `sdk/client/rust/src/cenv.rs`; C, Java and Ada
bindings share this Rust transport core. Rebuild all native libraries and generated
packages after upgrading. [External operation semantics](../sdk/client/protocol.md)
and component [CH/2 IPC layouts](../sdk/input-protocol.md) are separate from this envelope.

[Shared vectors](../sdk/client/rust/tests/fixtures/cenv2.json) fix bytes for compact DATA/ACK/
PUBLISH/RESULT/DELIVER and generic controls, Unicode, exact uint64 IDs and opaque
bodies. Both Python and Rust tests consume them, including metadata truncations.
Network integration covers retries, dynamic schemas, distributed direct/broker
routes, TLS and bounded backpressure. `make test-cenv2` runs the transport checks;
native/managed client integration targets require their respective toolchains.

`make bench-cenv` compares CENV/2 against a frozen test-only CENV/1 codec in the
same process. It records environment, fixture sizes, samples and medians. It
excludes sockets, SDL decoding, retries and end-to-end latency; its results are
measurements, not timing assertions. The runtime never imports the old codec.
