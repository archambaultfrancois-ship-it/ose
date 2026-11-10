# C component input callbacks

Application model v2 generates C, Rust, Python, Java and Ada containers for the local
real-time engine, distributed workers and local parallel simulator. C containers use [CH/2](../sdk/input-protocol.md)
and compile as C99 with warnings treated as errors. [Ada CH/2 callbacks](ada-inputs.md)
are qualified locally with GNAT 12. See [distributed execution](distributed.md) for
the worker lifecycle and transport qualification scope.

`component.h` declares one typed `Input_<port>` reference per input and one
`ComponentCallbacks.on_<port>` slot per activating input. There is no `on_tick`
or combined `ComponentInputs` structure. Pull inputs have no callback.
The builder creates missing handler files and preserves existing user code.
Generation and scaffolding require v2; CH/1 and snapshot generator branches are
removed. The C callback SDK also accepts CH/2 only and always advertises READY 2.
The old `ose_run_callbacks`, `ose_run_callbacks_parameters`, `ose_input_value`,
`ose_ready`, `ose_tick` and CH/1 `ose_publish` functions are removed. Raw message
references and publications use `ose_inputs.h`. `ose_input` remains solely for
shared-parameter `U` frames; it rejects the old `D` message frames.

`OsePortSpec` no longer contains `required`; its fields are `name`, `root`,
`catalogue` and `timeout`. Rebuild wrappers and native executables after upgrading
the SDK. Decoders are cached per input, while decoded references are owned only
for the active callback. No combined selection or joint-readiness path remains.

```c
#include "component.h"

static int on_clock(OseContext *ctx, const Input_clock *input) {
    Input_feedback feedback;
    (void)input;
    if (!pull_feedback(ctx, NULL, &feedback)) return 0;
    if (feedback.message) {
        return feedback.meta.has_app_id
            ? publish_out_with_id(ctx, feedback.message, feedback.meta.app_id)
            : publish_out(ctx, feedback.message);
    }
    return 1;
}

const ComponentCallbacks component_callbacks = {.on_clock = on_clock};
```

`input->message` points to the decoded SDL type, or is NULL for a timeout. Timer
inputs use a `const void *message` which is always NULL. `input->meta` provides
`port`, `event` (`OSE_INPUT_MESSAGE` or `OSE_INPUT_TIMEOUT`), optional `handle` and
`source`, `has_app_id`, `app_id`, `elapsed` and `due_ns`. Missing IDs have
`has_app_id == 0`; an explicit zero ID has `has_app_id == 1`. IDs preserve all
64 bits. The same callback handles message and inactivity-timeout events.
`ose_time_ns(ctx)`, `ose_now(ctx)` and `ose_tick_index(ctx)` expose the current
activation's logical time and index.

## Pull access and storage lifetime

For a Pull input named `feedback` of SDL type `Value`, generated methods are:

```c
int get_feedback(OseContext *, const OseSelector *, Input_feedback *out);
int pull_feedback(OseContext *, const OseSelector *, Input_feedback *out);
int remove_feedback(OseContext *, const OseSelector *, int *removed);
```

Return 1 means the operation succeeded. Absence is successful: `out->message`
is NULL or `*removed` is zero. Return 0 fails callback execution. Operations are
nonblocking and require an active callback. A NULL selector chooses the oldest
eligible message. For example, combine exact handle, source and application ID:

```c
OseSelector selector = {
    .has_app_id = 1, .app_id = UINT64_MAX,
    .handle = previous.meta.handle, .source = previous.meta.source
};
Input_feedback selected;
if (!get_feedback(ctx, &selector, &selected)) return 0;
```

Selectors use NUL-terminated UTF-8 strings. Duplicate application IDs select the
oldest matching record. FIFO and latest retain their model reception policies.
Each message keeps its dynamic SDL catalogue: the runtime verifies the S2PS/2
identity, checks compatibility and updates its decoder when the catalogue changes.

The caller owns the small reference structure. Message pointers and metadata
strings borrow runtime storage until the current callback returns. Repeated reads
have independent storage, so a subsequent read does not invalidate an earlier
reference. Copy any data needed later into component-owned storage; do not free
borrowed pointers or retain them across callbacks. `ctx->user_data` can hold
component-owned state, released in `on_stop`.

An activation sees surviving handles present when it started. New arrivals become
accessible at the next activation. Reads do not reserve records against overflow
eviction. `get` does not consume; `pull` and `remove` immediately hide the selected
handle within the callback. Actual removals release capacity at successful
FINISHED, before outbound publication waits.

## Publication, timers and failure

`publish_<output>(ctx, &message)` publishes without an application ID.
`publish_<output>_with_id(ctx, &message, id)` sets an explicit uint64 ID. Both the
SDK and parent engine enforce the actual activating event's output permissions.
A Push callback with no output links may compute and update its state.

`ose_arm(ctx, "clock", NULL)` uses the model delay. Pass a pointer to a positive
uint64 nanosecond delay to override the next expiration. `ose_disarm(ctx, "clock")`
cancels future expirations. Controls require a declared timer and are allowed
during `on_start` or an active input callback. Timers do not carry SDL contracts.

Callbacks retain the existing C convention: return nonzero for successful
execution, zero for execution failure. `ose_fail(ctx, message)` marks failure and
returns zero. An SDK operation error also marks failure, even if user code ignores
its return and later returns nonzero. Successful execution sends empty FINISHED;
zero return, marked failure or process termination sends no FINISHED. There is
no extra business-status field. The engine cancels deferred withdrawals and
staged publications on failure. Independent overflow evictions remain effective.

Shared parameter access, logging, measurements and component recording keep their
existing context APIs. Timer controls and these services are outside the inbox
withdrawal journal. Regenerate the plan and containers together after changing
input declarations, modes or activation links: C wrappers compile those declarations.

The [feedback example](../examples/feedback-pull-v2/README.md) provides all-C and
mixed C/Python deployments. Run `make test-c-inputs-v2` for native and bidirectional
mixed feedback, real TCP/UDP delivery, parallel traces, shared services and invalid
frame checks.
