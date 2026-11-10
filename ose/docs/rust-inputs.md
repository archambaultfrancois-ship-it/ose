# Rust component input callbacks

Application model v2 supports generated Rust, Python, C, Java and Ada components in the local
real-time engine, distributed workers and local parallel simulator. Rust components
use [CH/2](../sdk/input-protocol.md). See [distributed execution](distributed.md)
for the worker lifecycle and transport qualification scope.
The generated `Callbacks` trait has one `on_<input>` method per activating input,
plus `on_start`/`on_stop`. It has no `on_tick` or multi-input `Inputs` structure.
Pull inputs have no callback.
Generation and scaffolding require v2; snapshot and CH/1 generator branches are
removed. The Rust SDK no longer exposes CH/1 `initialize`, `tick`, `publish` or
`TICK_CONTEXT`. Use `inputs::activation`, `inputs::publish` and `inputs::READY`
for raw CH/2 processing. The shared `input` decoder is only for parameter `U`
frames and rejects old `D` message frames; the old `Input::is_null` helper is
removed. Timeout absence is represented by an input reference without a message.

`TickContext` remains a time/counter value populated from a CH/2 activation; it
does not decode ticks or activate callbacks. Rebuild generated wrappers and
executables after upgrading the SDK.

The [feedback example](../examples/feedback-pull-v2/README.md) includes an all-Rust
deployment. Its timer input is SDL-free and its initially empty Pull feedback
breaks the return activation path. It needs no initial feedback value.

```rust,ignore
use crate::container::{Callbacks, Context, InputReference, Result, Selector};
use crate::types::value::Value;

#[derive(Default)]
pub struct Handler;

impl Callbacks for Handler {
    fn on_clock(&mut self, ctx: &mut Context, input: &InputReference<()>) -> Result {
        assert!(input.timed_out());
        if let Some(feedback) = ctx.pull_feedback(Selector::default())? {
            let value = feedback.message.as_ref().ok_or("missing message")?;
            match feedback.app_id {
                Some(id) => ctx.publish_out_with_id(value, id)?,
                None => ctx.publish_out(value)?,
            }
        }
        Ok(())
    }
}
```

Message callbacks receive `&InputReference<MessageType>`; timer callbacks receive
`&InputReference<()>`. References expose `port`, `event: EventKind`,
`message: Option<T>`, optional `handle`, `app_id: Option<u64>`, optional `source`,
`elapsed`, and `due_ns`. `timed_out()` distinguishes timeout from message events.
A timeout has no message. One callback handles both events for a timed Push input.
The context provides `time_ns()`, `now()` and `activation_index()`.

Generated output helpers are `publish_<port>(&message)` and
`publish_<port>_with_id(&message, id: u64)`. The first sends no application ID;
zero is a valid explicit ID. Preserve absence when forwarding with a `match` on
`input.app_id`, as shown in the example's `b.rs`. SDK and engine both restrict
publication to outputs authorized by the actual activating event.

## Pull operations and lifetime

For a Pull input named `feedback`, the context generates:

| Method | Return type |
| --- | --- |
| `get_feedback(selector)` | `Result<Option<InputReference<Value>>>` |
| `pull_feedback(selector)` | `Result<Option<InputReference<Value>>>` |
| `remove_feedback(selector)` | `Result<bool>` |

`Selector::default()` selects the oldest eligible message. Chain `.id(42)`,
`.handle("engine-handle")` and `.source("component.output")` to narrow selection.
Repeated application IDs select the oldest matching record. All operations are
nonblocking; absence is a successful `None`/`false`. No reservation is created by
`get`. Decoding follows the full catalogue carried by each returned record,
including changing catalogues on the same input.

Operations require an active callback. Each callback sees only surviving handles
present when it started; later arrivals wait for the next activation. Returned
values and metadata are owned and may be retained by the handler. Actual queue
operations go through the active context, so retained values do not grant access
after callback completion.

Pull/remove hide the selected handle immediately within the callback. Physical
withdrawals commit at successful `FINISHED`, releasing capacity before outbound
publication waits. An `Err` return or panic sends no `FINISHED`: the engine cancels
withdrawals and staged publications. Independent overflow eviction is not undone.
This journal does not roll back timer controls, logs, measurements, recording or
parameter-service operations. A Rust `Result` reports callback execution errors;
there is no extra business-status field in `FINISHED`.

## Timers and shared services

`ctx.arm("clock", None)` uses the model delay. An override uses
`Some(std::time::Duration::from_millis(5))`; zero or delays beyond uint64
nanoseconds are rejected. `ctx.disarm("clock")` cancels future expirations.
Control is allowed during `on_start` and active callbacks. Timers are explicit
model inputs; arming does not immediately invoke a callback.

Parameters, logs, scalar measurements and component recording retain their Rust
context APIs. Recording activation and readable parameter updates arrive between
callbacks. Regenerate containers together with the plan after changing input
kinds, modes, activation links or callback declarations; those declarations are
compiled into the Rust wrapper. Callback bodies remain in user-owned handler files.

`make test-rust-inputs-v2` exercises native and mixed Rust/Python feedback,
deterministic worker traces, failure cancellation, dynamic catalogues and shared
services. See also [C input callbacks](c-inputs.md) and
[Java input callbacks](java-inputs.md). [Ada CH/2 sources](ada-inputs.md) still need
GNAT compilation/execution qualification.
