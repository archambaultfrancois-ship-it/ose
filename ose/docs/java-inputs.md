# Java component input callbacks

Application model v2 generates Java, Python, C, Rust and Ada components for the local
real-time engine, distributed workers and local parallel simulator. Java uses [CH/2](../sdk/input-protocol.md),
with one typed reference per activating input, explicit timers and passive Pull
inboxes. Ada [component callbacks](ada-inputs.md) are qualified locally with GNAT
12. See [distributed execution](distributed.md) for the worker lifecycle
and transport qualification scope.

For component `a`, extend `org.sdlose.generated.a.API.Handler`. The builder
creates missing handlers and preserves existing code. `onStart` and `onStop`
retain their lifecycle roles. Each Push or timer input declares `on_<input>`;
Pull inputs have no callback. The generated v2 API has no `onTick` or multi-input
`API.Inputs` snapshot. The Java generator and component SDK now support CH/2
only. V1 application models are rejected before Java source generation; the
old `Component.run` snapshot runner and CH/1 publication format are removed.

```java
import org.sdlose.generated.a.API;
import org.sdlose.InputProtocol.Reference;
import sdl.globaltypes.value.Value;

public final class A extends API.Handler {
    @Override public void on_clock(API.Context ctx, Reference<Void> input)
            throws Exception {
        Reference<Value> feedback = ctx.pull_feedback();
        if (feedback != null) ctx.publish_out(feedback.message, feedback.appId);
    }
}
```

Message inputs receive `Reference<MessageType>`; timer inputs receive
`Reference<Void>`. Fields are `port`, `event` (`MESSAGE` or `TIMEOUT`), `message`,
optional `handle`, optional `source`, optional `appId`, `elapsed` and `dueNs`.
`timedOut()` identifies an expiration, whose message is null. A timed Push input
handles both message and inactivity-timeout events through the same callback.

`appId`, `elapsed`, `dueNs`, `ctx.timeNs()` and `ctx.activationIndex()` use
`BigInteger`, preserving the full uint64 range. `ctx.tickIndex()` remains an
alias for the activation index; `ctx.now()` returns seconds. Missing application
IDs are null; explicit zero is `BigInteger.ZERO`. Generated SDL uint64 *message
fields* retain the SDL codec's existing `long` bit representation.

## Pull operations and selectors

For a Pull input `feedback` with SDL type `Value`, the generated context offers:

| Method | Result |
| --- | --- |
| `get_feedback()` / `get_feedback(selector)` | `Reference<Value>` or null |
| `pull_feedback()` / `pull_feedback(selector)` | `Reference<Value>` or null |
| `remove_feedback()` / `remove_feedback(selector)` | boolean |

Operations are nonblocking. Absence is successful, and `get` creates no
reservation. A default or null selector chooses the oldest eligible record.
`InputProtocol.Selector` is immutable; its methods combine selectors:

```java
import org.sdlose.InputProtocol.Selector;
import java.math.BigInteger;

Selector exact = new Selector()
    .id(new BigInteger("18446744073709551615"))
    .handle(previous.handle)
    .source(previous.source);
Reference<Value> selected = ctx.get_feedback(exact);
```

IDs must be between zero and 2**64-1. Duplicate IDs select the oldest matching
record. FIFO and latest remain separate model reception policies.
Each record retains its own dynamic SDL catalogue. Decoding verifies its S2PS/2
identity, checks compatibility and updates the decoder when the catalogue changes.

Operations require the active callback thread. The context rejects access from
`onStart`, `onStop`, a background thread or outside execution. References and
decoded messages are ordinary Java objects that may be retained by the handler;
they grant no inbox access outside the callback. New arrivals become accessible
at the next activation, while existing handles remain subject to overflow eviction.

`pull` and `remove` immediately hide a handle within the current callback. Physical
removal and capacity release occur at successful FINISHED, before outbound
publication waits. An escaping exception, `Error` or process termination cancels
deferred withdrawals and staged publications. Overflow evictions are independent.

## Publication, timers and services

`ctx.publish_<output>(message)` sends no application ID.
`ctx.publish_<output>(message, id)` sets an optional `BigInteger` ID; null preserves
absence when forwarding. Both SDK and engine enforce the actual input event's
output permissions. A callback may compute without authorizing any output.

`ctx.arm("clock")` uses the model delay. `ctx.arm("clock", delayNs)` overrides the
next expiration with a positive uint64 `BigInteger` nanosecond delay. Null uses
the model delay; zero, negative and oversized values are rejected.
`ctx.disarm("clock")` cancels future expirations. Controls require a declared
timer and the callback thread, during `onStart` or active input processing.
Timers carry no SDL message or contract.

Callbacks return void. A normal return sends empty FINISHED; an escaping throwable
sends none. There is no additional business-status field. Java exceptions follow
the existing SDK convention: application code may catch a local validation error,
such as an unauthorized publication, and finish normally without emitting it.
The engine remains authoritative even if raw IPC bypasses SDK checks.

Shared parameters, logs, measurements and recording keep their context APIs.
These services and timer controls are outside the withdrawal journal. Regenerate
containers together with the plan after changing input declarations or activation
links: Java wrappers compile those declarations.

## Examples and qualification

The [feedback example](../examples/feedback-pull-v2/README.md) includes all-Java
and Java/Python deployments. `make test-java-inputs-v2` tests both directions,
parallel simulation, actual TCP/UDP delivery, dynamic catalogues and context scope.

The Java 7 source/API target remains the repository default. Use JDK 7 or a JDK
that supports `--release 7`. The 25 CH/2 integration cases now pass with OpenJDK
17.0.21 compiling against `--release 7` and executing on JDK 17 in an isolated
Debian arm64 environment. Generated bytecode was separately checked for major
version 51. This qualifies the Java 7 source/API/bytecode target, not execution
on a Java 7 JVM. Both CI workflows explicitly run `test-java-inputs-v2` in their
actual Java 7 jobs. Execution on an actual JVM 7 is deferred and does not gate
obsolete-code retirement.

After CH/1 retirement, 35 Java boundary/service/integration cases pass with
JDK 17 and the default target. A rebuilt feedback example contains 96 class
files, all bytecode major version 51; obsolete callback classes are no longer
included. CH/2 rejects empty or timestamp-only legacy TICKs and INPUT frames.

The Android host retains JDK 21. Its explicitly selected
`OSE_JAVA_LOCAL_RELEASE=8` smoke builds do not qualify the default target. No
default toolchain requirement has been relaxed.
