# Ada component input callbacks

Ada model v2 sources and generated CH/2 wrappers compile and execute with GNAT
12.2.0 in an isolated Debian 12 arm64 environment. All 23 strict CH/2 cases pass:
all-Ada and bidirectional Ada/Python feedback, local TCP/UDP, parallel simulation,
Pull consumption and failure cancellation. The complete five-language example
also passes with identical traces for 1/2/8 workers and live TCP/UDP execution.

On a machine with GNAT and the full Ada runtime, use `make test-ada-inputs-v2`
and the existing `make test-ada`. Missing GNAT fails the new target explicitly.
The compiler accepts v2 Ada declarations for local/distributed real-time or local
parallel simulated execution. Local execution and simulation are qualified here.
Ada-specific distributed worker runs have not been separately qualified; existing
distributed tests exercise the common worker using Python and C processes.

## Input references

For component `a`, the generated `Ose_a` package declares `Handler`, limited
private `Context`, and one `On_<port>` primitive per activating input. Derive a
handler as in [the example](../examples/feedback-pull-v2/a_handler.ads).
`On_Start`/`On_Stop` keep their lifecycle roles. Pull inputs have no callback.
The API has no `On_Tick` or combined `Inputs` snapshot. The Ada generator and
component SDK now support CH/2 only; v1 models are rejected before generation.
The old tick decoder, READY capabilities and CH/1 publisher have been removed
from `Ose_Component`; CH/2 readiness and publication use `Ose_Inputs`.

Message input `feedback` has an owned record:

```ada
type Input_feedback is record
   Meta : Ose_Inputs.Metadata;
   Present : Boolean := False;
   Message : SDL_value.T_Value;
end record;
```

Only inspect `Message` when `Present` is true. False represents absence, including
a timed input's expiration, and is not an initial message value. A timer input
uses `subtype Input_clock is Ose_Inputs.Metadata` without any SDL message field.
The same Push callback handles both message and inactivity-timeout events.

Metadata contains `Port`, `Handle`, `Source` as unbounded UTF-8 strings; `Kind`
as `Message_Event` or `Timeout_Event`; `App_Id`; and uint64 `Elapsed`/`Due_Ns`.
Application IDs use `Ose_Inputs.Optional_Id` with `Present` and `Value` fields.
`No_Id` has `Present=False`; `(True, 0)` is an explicit zero ID. Values, logical
time and activation indices use `Interfaces.Unsigned_64` without string conversion.
`Time_Ns`, `Activation_Index`/`Tick_Index` and `Now` expose logical callback time.

References own their decoded value and metadata. They may be retained in handler
state without keeping an inbox view or runtime-owned pointer alive. Access to the
inbox itself still requires the active callback and its owning Ada task.

## Pull, publication and timers

The generated context exposes:

```ada
function Get_feedback
  (Ctx : in out Context;
   Selection : Ose_Inputs.Selector := Ose_Inputs.Any_Message)
   return Input_feedback;
function Pull_feedback
  (Ctx : in out Context;
   Selection : Ose_Inputs.Selector := Ose_Inputs.Any_Message)
   return Input_feedback;
function Remove_feedback
  (Ctx : in out Context;
   Selection : Ose_Inputs.Selector := Ose_Inputs.Any_Message) return Boolean;
```

These operations are nonblocking. Absent reads return `Present=False`; removal
returns False when no message matches. `get` neither consumes nor reserves.
Selectors combine an optional uint64 application ID with optional handle/source
strings. Empty strings mean no selector. `Any_Message` selects the oldest eligible
record; duplicate application IDs select the oldest matching one. For example:

```ada
Selection : Ose_Inputs.Selector := Ose_Inputs.Any_Message;
-- Inside the callback:
Selection.App_Id := (True, Interfaces.Unsigned_64'Last);
Selection.Handle := Previous.Meta.Handle;
Selection.Source := Previous.Meta.Source;
```

`Publish_<output>(Ctx, Message)` publishes without an ID. Its third argument may
be an `Optional_Id` or an `Unsigned_64`, so forwarding `Input.Meta.App_Id` preserves
absence and zero. SDK and parent engine enforce this input event's output rights.
A callback with no output links may compute and update its state.

`Arm(Ctx, "clock")` uses the model delay. The overload with an unsigned uint64
`Delay_Ns` overrides the next expiration; zero is rejected. `Disarm` cancels future
expirations. Controls require a declared timer and the owning task during
`On_Start` or an active callback. Pull access requires an active callback and is
rejected during `On_Start`, `On_Stop` or from another task.

Each returned message keeps its dynamic catalogue. The wrapper verifies its
S2PS/2 catalogue identity, compares compatibility and updates its cached decoder
when the catalogue changes. SDL contracts and dynamic CENV catalogue exchange are
unchanged. FIFO and latest remain reception policies independent of Push/Pull.

## Completion and shared services

Normal procedure completion sends empty FINISHED. An escaping exception sends
none. The engine commits deferred withdrawals and Push FIFO consumption at
successful FINISHED, releasing capacity before outbound publication waits.
Pull/remove hide a handle immediately within the callback; failure cancels these
withdrawals and staged outputs. Independent overflow evictions remain effective.
There is no extra business-status field. A handler may catch a local guard error
and complete normally without emitting the forbidden operation.

Arrivals during a callback become accessible at its next activation; the visibility
cut does not reserve handles against overflow. Timers, shared parameter writes,
logs, measurements and recording remain outside the withdrawal journal. Existing
Ada context APIs for those services are reused. Regenerate plan and containers
together after input or activation-link changes.

Port names appear in prefixed Ada identifiers such as `Publish_out`. V2 therefore
allows Ada keywords as port names when their spelling is otherwise portable.
Case-insensitive collisions, reserved lifecycle callback names and invalid GNAT
unit/file names remain rejected. Port checks no longer retain the v1 snapshot's
bare-field naming restrictions.

## Qualification

`tests.managed.test_ada_inputs` passes for all-Ada and both directions of mixed
Ada/Python feedback, deterministic workers, TCP/UDP, failure cancellation,
dynamic catalogues, context services, malformed frames, zero/absent/max IDs,
timers and generated-handler preservation. Native external-client execution also
passes, including zero, absent, greater-than-2^53 and maximum uint64 application
IDs over TCP and UDP. The shared Rust C ABI builds with Rust 1.85.1, using explicit
null error values rather than requiring `Default` on raw pointers.

The all-Ada feedback example matches the Python/C/Rust/Java reference trace
exactly with 1 and 4 workers: six callbacks, six publications, quiescent stop,
SHA-256 `796b451975944f54c7b029e2f007806efae542fb6f4b029d39d099abd5e03002`.

The Android host itself has no GNAT. Qualification used Debian 12 under
PRoot-Distro, Python 3.11.2 and GNAT 12.2.0 with `-gnat2012 -gnata -O2` (later
builds use two parallel compiler jobs). These results do not qualify physical
multi-machine deployments, other Ada runtimes or real-time delivery deadlines.
