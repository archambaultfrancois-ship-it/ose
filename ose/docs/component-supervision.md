# Component supervision and independent producers

A model specifies contracts and subscriptions. It does not reserve a topic for
one producer. A compatible producer can declare and publish `sensor.signal` even
while the model's `sensor` component runs. `external_topics` declares contracts
that are not supplied by component outputs. Diagnostic `component.record.*`
points remain reserved for recording.

## Launch and runtime commands

Start the normal application with selected components inactive:

```sh
ose run --plan build/plan.json --disable-component sensor_1 --disable-component sensor_2
ose distributed-local --plan build/plan.json --disable-component sensor_1
# With individually deployed workers, select initial states on the coordinator:
ose serve --plan build/plan.json --disable-component sensor_1 --insecure
```

`ose app run` and `ose app distributed` accept the same repeated launch option.
Unknown names fail before execution. Workers/adapters still register for every
component in the plan, including inactive ones. An initially inactive component
has no handler process, startup hook, callback or active timer.

Control a running local application through its broker:

```sh
ose component status --endpoint build/endpoint.json
ose component disable sensor_1 sensor_2 --endpoint build/endpoint.json
ose component enable sensor_1 sensor_2 --endpoint build/endpoint.json
```

`ose app component` exposes the same operations. For a distributed coordinator,
add `--distributed --host HOST --port PORT` and the usual `--tls-ca`, `--tls-cert`,
`--tls-key` options, or explicit loopback `--insecure`.
The result is JSON containing `enabled` (desired), `active` (acknowledged),
`version` and `pending` for each component. Before registration, `active` is null.
A transition waits for acknowledgement; a timeout reports a failure and leaves the
requested state visible for inspection. Selecting several components does not
make the transition atomic across the application.

Disabling waits for the current callback and its output admissions. It clears
pending input inboxes and deliveries to that component, rejects new admissions,
and freezes timer deadlines. Already committed outputs can still reach other
components. Enabling resumes the retained process state and remaining timer
delays. It calls startup once, on the first activation, rather than restarting
the component. Version checks reject deliveries from an earlier activation.
Parameters remain accessible while a component is inactive. Disabling a component
does not release external producer registrations on its output topics.

## Simulation and replay

Use `--disable-component` with `ose run --mode simulated` or
`ose replay --mode simulated`. The simulation console and command scripts support
`component status [NAME...]`, `component disable NAME...` and
`component enable NAME...` at callback boundaries. These commands pause progression;
use `resume` or stepping commands to continue. Timers freeze in virtual time.

Replay can use the original output names and the complete original plan:

```sh
ose replay signals.pcapng --mode simulated --plan build/plan.json \
  --map sensor_1.signal=sensor_1.signal --map sensor_2.signal=sensor_2.signal \
  --disable-component sensor_1 --disable-component sensor_2 --duration 3
```

For live replay, first suspend the sensors using launch options or runtime
supervision, then run `ose replay` with the same mappings. Live replay never
implicitly stops or reactivates components. Internal recording points of the
active downstream components remain available with `--record`.

## Protocol and recording

Publication declaration uses existing CENV/2 operations. Topic reference contracts
remain stable; each producer keeps its actual SDL descriptor. Messages carry their
actual S2PS/2 catalogue identity. Receivers fetch and cache the matching catalogue
and validate it against their input contract. No SDL SDK publication API changes
are required. Producer generation and transport sequence identify messages;
application IDs, including zero and uint64 maximum, remain independent.

Local output taps aggregate all producers at the broker. Distributed output taps
aggregate at the coordinator, including outputs forwarded temporarily from direct
routes when capture is active. Worker input and diagnostic taps retain their
per-component generation checks. Closing one producer does not terminate an
application output recording.

For aggregate taps, `recording_next` includes `catalogue_size`: its binary body
contains that many UTF-8 schema bytes followed by the S2PS/2 envelope. This keeps
large SDL catalogues outside CENV's 64 KiB metadata limit and within the frame and
capture byte budgets. `ose record` creates separate container channels when
compatible source schemas differ; message metadata preserves `ose.producer` and
optional `ose.app_id`. Filters evaluate using each actual source catalogue.
