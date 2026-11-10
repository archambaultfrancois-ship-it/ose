# Generated component containers

Runnable examples use application model v2, single-input reference callbacks,
passive Pull inboxes and explicit timer inputs. Start with
[model v2 migration](model-v2-migration.md), [C](c-inputs.md), [Rust](rust-inputs.md),
[Java](java-inputs.md) or [Ada](ada-inputs.md). Each callback receives its activating input reference. Other values are read
explicitly from Pull inboxes. Source components declare timer inputs.

A deployment component can select a generated container instead of supplying
an executable command:

```json
{
  "container": {
    "language": "python",
    "handler": "components/analyse/logic.py"
  }
}
```

The handler path is relative to the deployment document; existing substitutions
such as `{project}` also work. The component must specify exactly one of
`command` or `container`. `language` accepts `python`, `c`, `rust`, `java` and
`ada`, with a `.py`, `.c`, `.rs`, `.java` or `.adb` handler respectively. Java
requires the fully qualified `class`; Ada requires the package `unit` and its
`.ads` specification alongside the body. Existing command-based components
continue to use the same binary SDK protocol.

`build` generates the wrapper, component-specific SDL bindings, compilation
commands and executable command in the resolved plan:

| Language | Generated API and wrapper | Executable |
| --- | --- | --- |
| Python | `build/components/<name>/container.py`, `types/` | Python wrapper |
| C | `build/components/<name>/component.h`, `container.c`, `types/` | `build/components/<name>/component` |
| Rust | `build/components/<name>/src/container.rs`, `src/types/`, `Cargo.toml` | `build/rust-target/release/ose-component-<name>` |
| Java | `org.sdlose.generated.<name>.API`, `Main`, typed bindings | JVM with generated classes |
| Ada | `Ose_<name>`, typed bindings, generated main | `build/components/<name>/component` |

C uses the compiler from `CC` (default `cc`), C99 and the SDL C runtime.
Rust builds offline with local paths to the SDK and SDL runtime; components
share a build target directory while retaining distinct executable names.
Optional deployment `build` commands run before generated compilation commands.
Native SDK sources are included in installed packages as well as the checkout.

Only generated files are replaced on rebuild. A missing handler receives
an initial callback skeleton; an existing handler is never overwritten. Handlers
must be outside the build directory. `validate` checks and generates temporary
artefacts without creating or changing handler files. Implement the generated
callback placeholders before running OSE.

## Python callbacks

For an input named `input` and an output named `result`, declare
`activates.message: ["result"]` on the input. The callback receives a reference:

~~~python
from measurement import Measurement

def on_input(ctx, input):
    if input.timed_out:
        return
    message = input.message
    ctx.publish_result(
        Measurement(sequence=message.sequence, value=message.value * 2),
        id=input.app_id,
    )
~~~

References expose `port`, `event`, `message`, `handle`, `source`, `app_id`,
`elapsed` and `due_ns`. Timeout references have no message. A missing application
ID is distinct from zero; forwarding `id=input.app_id` preserves that distinction.
One callback handles both message and inactivity-timeout events for a Push input.
Each event has its own output permissions.

The Python SDK entry point `sdl_ose_component` exports `InputSession`,
`InputCallbackContext`, `ReceivedInput`, `RecordResult` and `run_input_callbacks`.
The old snapshot `ComponentSession` and `CallbackContext` exports are removed.
Generated wrappers use the same CH/2 implementation directly from the OSE package.
The old `ose.execution.component` and `ose.execution.callbacks` modules have been
removed. Rebuild generated wrappers after upgrading the SDK. All container
generators reject application-model v1 before generating sources.

Generated bindings are imported before the handler. Helper modules next to the
handler can be imported normally. Outputs have typed `ctx.publish_<port>`
methods; `ctx.publish(port, message, id=...)` is also available. Both verify the
declared message class. `ctx.name` identifies the component and `ctx.parameters`
contains static model parameters.

## Lifecycle and execution

The container initializes codecs, loads the handler, checks callback signatures
and invokes optional `on_start(ctx)` before READY. `on_stop(ctx)` runs on normal
shutdown or an execution error after initialization enters the start hook.
Callbacks are synchronous; publication requires an active input callback.

A Push message or timeout activates its own `on_<port>(ctx, input)`. Other inputs
do not gate it. A Pull input has no callback. Sources use an explicit timer
input and its callback, such as `on_clock`; there is no component `on_tick`.

The engine owns input queues and deadlines. A callback runs once selected by
the scheduler, separately from network reception. While it runs, later messages
can be admitted, but they become accessible at its next activation. Inbox views
expire on completion. Decoded values and metadata have language-specific ownership;
C references borrow callback storage, while the other SDK guides describe owned
values that can be retained in application state.

## Pull access and timers

Read a passive input during another activation:

~~~python
def on_clock(ctx, input):
    latest = ctx.inbox("setpoint").get()
    if latest is not None:
        display(latest.message)
~~~

`get` reads without consuming or reserving. `pull` reads and consumes; `remove`
consumes without returning a value. Each accepts optional `id`, `handle` and
`source` selectors. Absence returns `None` or `False`, without blocking.
Withdrawals are hidden from subsequent reads immediately, then commit and release
capacity on successful completion. Failure cancels them. Overflow eviction
remains independent. FIFO/latest is a reception policy, separate from Push/Pull.

Timer inputs have no message contract. Declare periodic or one-shot mode,
a positive delay, and automatic or manual start. `ctx.arm("clock", delay=...)`
and `ctx.disarm("clock")` control declared timers during initialization or a
callback. Automatic timers first fire after their delay; periodic deadlines keep
their cadence and coalesce missed expirations.

## Completion, outputs and time

Python normal return, including `False`, completes successfully. An escaping
exception sends no FINISHED. C uses nonzero success, Rust uses `Ok(())`, Java
normal void return and Ada normal procedure completion.

Outputs remain staged until successful FINISHED. The engine commits withdrawals
before attempting outbound admissions, so a feedback publication does not hold
its own consumed slot. A failing callback exposes none of its staged outputs.
Timer controls, parameters, logs, measurements and internal recording have
their own service semantics and are outside the withdrawal journal.

Use `ctx.now()`, `ctx.time_ns` and `ctx.activation_index` for the callback's
logical time and index. Simulation starts at zero; real-time adapters use elapsed
monotonic time since activation. Some SDKs retain the name `tick_index` for this
counter; it does not imply a separate tick callback. See [simulation](simulation.md)
and the [shared context protocol](../sdk/protocol.md).

Generated component and port names must be identifiers. C/Rust ports additionally
use portable, non-reserved ASCII identifiers. Input names `start`, `stop` and
`tick` remain reserved. Regenerate containers after changing declarations;
existing handler code is never rewritten.

## Native and managed callbacks

| Language | Callback reference and guide |
| --- | --- |
| C | `const Input_<port> *`; [callbacks, selectors and borrowed lifetimes](c-inputs.md) |
| Rust | `&InputReference<T>`; [callbacks, selectors and owned values](rust-inputs.md) |
| Java | `Reference<T>`; [callbacks and BigInteger application IDs](java-inputs.md) |
| Ada | `Input_<port>`; [callbacks and controlled ownership](ada-inputs.md) |

See [Java and Ada toolchains](java-ada.md) for deployment, build settings and
external clients. All five languages use CH/2 and the same activation rules.

## Catalogue compatibility

Typed input decoding uses the catalogue attached to the received message, with
the consumer's generated type. Compatible publisher schema evolution, including
different wire IDs, therefore preserves typed callbacks. Decoder caches are
bounded; no delivery state is persisted.

The build combines the schema modules needed by a component into its own binding
directory. Distinct schema overrides and SDL packages are supported, provided
they do not define conflicting module files or logical type bindings
within one component. Such conflicts are refused during compilation; give the
schemas distinct module/type names when a component needs both. Different
components have separate binding directories and processes, so they can use
different revisions of the same module.
Python schema modules must not shadow standard library or SDK modules; C and
Rust do not have this Python import restriction (for example, `types.sdl` works).
