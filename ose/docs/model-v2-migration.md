# Migrating application models to v2

Runnable models use application version 2. Deployment, scenario, configuration
and SDL schema versions retain their existing values. Rebuild generated
containers after changing models and handlers. Upgrade all CENV/2 peers together;
dynamic SDL contract exchange and S2PS/2 message bodies retain their behavior.

| V1 construct | V2 replacement |
| --- | --- |
| Periodic component trigger | Explicit timer input with its own delay |
| Source on_tick(ctx) | Timer on_clock(ctx, input) |
| Callback receiving a message | Callback receiving a reference; use input.message |
| Required inputs and joint readiness | Independent Push callbacks; explicit business coordination |
| Multi-input snapshot | Individual callbacks, component state or Pull access |
| Periodic sampling of latest data | Timer callback and get on a passive Pull inbox |
| Component data DAG | Input-event-to-output activation graph; cycles require simulated `--microsteps` |

## Timers and output permissions

A source publishing measure every 20 ms declares:

~~~json
{
  "inputs": {
    "clock": {
      "kind": "timer",
      "timer": {"mode": "periodic", "delay": 0.02},
      "activates": {"timeout": ["measure"]}
    }
  },
  "outputs": {"measure": {"root": "Measurement"}}
}
~~~

A transforming input declares its possible output under activates.message.
A sink needs no output activation links. A calculation may run in any callback;
publication still requires permission for that callback's activating event.

Timers first expire after their positive delay. A 100 ms timer fires at 0.1,
0.2, … rather than zero. Simulation excludes its duration endpoint: a two-second
run has 19 activations per timer, ending at 1.9 seconds. Periodic phase is
preserved and missed expirations coalesce.

Push inactivity timeouts invoke the same callback with input.timed_out true
and input.message absent. New valid receipts reset inactivity; callback
completion does not. Declare message and timeout output permissions separately.
Pull inputs cannot declare an activating timeout.

## Message references and Pull access

Python callbacks receive one origin reference. Read its message, source,
technical handle and optional uint64 application ID; passing id=input.app_id
preserves absence separately from zero when publishing. C uses Input_input
references, Rust InputReference, Java Reference and Ada an owned Input_input
record. See the [C](c-inputs.md), [Rust](rust-inputs.md), [Java](java-inputs.md)
and [Ada](ada-inputs.md) guides.

For periodic display, use a Pull data input, a latest subscription and an
independent timer callback:

~~~python
def on_clock(ctx, input):
    latest = ctx.inbox("input").get()
    if latest is not None:
        display(latest.message)
~~~

Get reads without consumption or reservation. Pull reads and consumes; remove
consumes without returning a message. Withdrawals are hidden within the callback,
then commit on successful completion and release capacity. Failure cancels them
and discards staged publications. Arrivals during a callback become accessible
on the next activation. Pull never guarantees that a message is available.

## Explicit joins

The simulated and multilingual joins have one Push callback per branch. They
collect messages by payload sequence and publish when all branches for that
sequence have arrived. Any branch can complete the group. There is no assumed
arrival order or engine snapshot.

Pending state is bounded to 1024 incomplete sequences. Duplicate branches for
one pending sequence and exceeding this bound raise explicit errors. These are
correlation examples, without durable state, completed-sequence deduplication
or recovery after cold restart. The multilingual sum uses stable port order
to avoid arrival-order changes in floating-point addition.

The simulated join consumes one optional FIFO Pull offset per completed pair.
An offset alone never activates it. Its external scenario stays version 1.

## Migrated examples and qualification

- Acquisition: C timer source, Rust Push transform, Python Push observer and
  timer/Pull latest display. IDs pass through the transform.
- Simulated: C/Python/Rust branches and an explicit two-way Python join.
- Shared parameters: two Python timer callbacks retain their parameter APIs.
- Multilingual: Python timer/join and C/Rust/Java/Ada reference callbacks.
  The complete five-language example passes with GNAT 12 and JDK 17 targeting
  Java 7 APIs, with identical traces for 1/2/8 workers and live TCP/UDP execution.
- Vibration-watch: C sensor timers, Rust analyzers, independent Python alarm
  callbacks and observer. Its distinct compatible observer SDL contract,
  shared parameter APIs and local/distributed execution are preserved.

Vibration simulation now observes 19 blocks per machine over two seconds. Its
first signal timestamp is 100 ms; the increasing profile first alarms at
sequence 11 (1.1 seconds), with nine alarms before the duration bound.

All five container generators require v2 and only emit input-reference callbacks.
The Python snapshot runtime is removed; the public SDK exports CH/2 only.
Java/Ada snapshot APIs and CH/1 dispatch are removed as well. Native boundary,
common service, timeout, admission, replay, distributed and functional simulation
fixtures now use v2. The low-level C/Rust CH/1 APIs are also removed, including
legacy READY/tick decoding and publication helpers. Execution drivers use CH/2
exclusively; the snapshot scheduler and its runtime branches are removed.
Application models and compiled plans must be version 2, with execution model 2
for every component, including raw-command components. Rebuild old plans and
containers before running them. Deployment, scenario and telemetry versions
remain independent. No v1-to-v2 execution adapter is introduced.
All five local SDKs are qualified; actual JVM 7 execution is deferred and no
longer gates retirement. Runtime/SDK cleanup proceeds independently. The editor
accepts only v2 for structured editing and validation. Legacy sources remain
editable for migration with an explicit diagnostic. It provides Push/Pull/timer
controls, per-event output checklists, optional internal
activation arrows and an activation table. Model validation and undoable source
previews use the same execution declarations. Recording and replay preserve
optional application IDs independently of publication sequences, including zero
and duplicate values. Old recordings replay without an application ID. See the
[transition plan](cenv2-transition.md) for qualification and remaining gates.

From the OSE project directory, make test-example-inputs-v2 runs the example
qualification cases. On JDK 21, explicitly set OSE_JAVA_LOCAL_RELEASE=8 for this
local run; it does not qualify the default Java 7 target. That target is now
checked using JDK 17 `--release 7`; actual Java 7 JVM execution is deferred for later qualification.
The complete multilingual execution remains in make test-mixed and requires GNAT.

