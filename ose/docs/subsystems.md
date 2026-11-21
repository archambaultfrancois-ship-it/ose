# Subsystem composition and autonomous harnesses

OSE resolves hierarchical source models into its ordinary flat execution graph.
Existing version 2 models and version 1 deployments remain accepted. Composition
adds optional source fields, a version 1 subsystem definition and version 1
harness documents. Older OSE versions reject these new source fields; rebuild
with a composition-capable compiler.

See the [French development and integration guide](../../docs/developpement-sous-systemes.md)
and the [executable example](../examples/subsystems/README.md).

## Subsystem definition

`subsystem.json` contains exactly:

```json
{
  "version": 1,
  "model": "model.json",
  "deployment": "deployment.json",
  "interface": {
    "inputs": {"measurements": {"to": "processor.input"}},
    "outputs": {"alarms": {"from": "processor.out"}},
    "parameters": {"threshold": {"path": "settings"}}
  }
}
```

All four top-level fields are required. Interface dictionaries are optional and
default to empty. Interface and instance names are portable ASCII identifiers
starting with a letter. Message port interfaces resolve through a local component
or an immediate child's public interface. Timers cannot be exposed. Each public
port targets one port; aliases create no runtime queues or additional callbacks.
Public parameter paths refer to this model's declared shared parameter blocks.
To expose a descendant's parameter, declare a block in the parent, bind the child
to it and expose the parent block.

## Model instances

Models accept an optional `subsystems` dictionary:

```json
"subsystems": {
  "detection": {
    "source": "../detection/subsystem.json",
    "parameters": {"threshold": {"limit": 80}}
  }
}
```

`source` is required and can name a definition file or its directory. Optional
`parameters` replaces whole initial values of exposed blocks. Optional
`parameter_bindings` maps exposed parameter aliases to shared parameter paths in
the parent model. One alias cannot have both an initial override and a binding.
The parent's initial value wins for a bound block. Binding checks SDL compatibility
in both directions, since components may read or write the shared value.

`components` and `subscriptions` remain required. `components` may be empty when
subsystems supply executable nodes; the resolved graph must contain at least one
component. Local component and subsystem instance names cannot collide.
Subscriptions use the existing `{id, from|from_topic, to, qos?}` grammar, with
`instance.public_port` endpoints. Access to child internals is rejected. Internal
subscriptions and their deployment bindings are imported for every instance.
Final shared-input reception, SDL and activation validation uses the existing
rules over the complete flattened graph.

All schema and definition paths retain the directory of their declaring file.
Parameters and external topics in children are instance-prefixed with `/`.
Local root names remain unchanged. Logical component paths are arrays of instance
names followed by the component name. Their dotted rendering is the runtime
component identity: `cell_1.plant`, including in inspect, measure and record.
Subscription and parameter-client identities also retain their dotted instance
path. Qualified component endpoints use `:` to separate the port, for example
`cell_1.plant:frame`; diagnostic points use `cell_1.plant:record.dsp.fft` and
measurements use `cell_1.plant:temperature`. Existing flat `component.port`
endpoints remain supported. Source connections through immediate subsystem
interfaces can keep their existing `instance.public_port` notation.

Generated native-language symbols are private and separate from runtime names.
Rebuild and restart applications built with hashed `ss_` identities; recreate
watches, recording selections and permissions that refer to those old identities.
Historical captures keep their original channel names.

## Deployment composition

Deployments accept optional `subsystems`, keyed by model instance name:

```json
"subsystems": {
  "detection": {
    "source": "deployments/detection-custom.json",
    "components": {"processor": {"env": {"MODE": "qualification"}}}
  }
}
```

Both entry fields are optional. `source` selects another deployment relative to
the parent deployment. Otherwise the definition's default deployment is used.
`components` applies overrides to immediate local components of that selected
deployment: `env` merges by key and `placement` replaces the value. Other
component properties are changed by selecting a deployment file. Child instance
deployments can themselves select their own descendants' deployments.

The selected deployment covers exactly its local components. Internal subscription
transport/routing bindings, parameter-client permissions and process aliases
are namespaced and retained. Child client identities use their dotted instance paths. Root
application settings control execution, broker, frame limits and process prefix;
nested deployments must omit those settings, except `execution: "local"`.

Handler paths, `cwd`, `{project}` and `{generated}` retain their originating
document/schema context. Build outputs still use the selected build destination.
Root `--profile local|cluster` is applied after complete assembly.

`plan.json` contains optional `composition` metadata: component and subscription
source locations, logical paths, resolved instance interfaces and parameter
binding constraints. This is provenance for tools, not an extra execution layer.
`ose build --component monitoring.detection.processor` selects a logical leaf
while retaining the complete plan, just as ordinary `--component` does.

Resolution is bounded to 32 nested instance levels, 1024 model/deployment loads
and 64 MiB of those source documents, with the existing 1 MiB per-document limit.
Canonical definition paths on the active recursion stack detect reference cycles;
multiple independent instances of one definition remain allowed.

## Harness document

The definition's directory contains `tests/harness.json`:

```json
{
  "version": 1,
  "profiles": {"standalone": {"cases": ["nominal"]}},
  "cases": {
    "nominal": {
      "scenario": "nominal/scenario.json",
      "expectations": "nominal/expectations.json"
    }
  }
}
```

Without a harness file, `validate` and `build` use an implicit `standalone` profile
with no cases. `test` rejects an empty case selection; it never reports success
without executing a case.

Profiles accept `model`, `deployment`, `inject`, `collect` and `cases`. Model and
deployment must occur together, with paths relative to the harness. A custom
model must instantiate the targeted definition as `sut`. With neither path,
OSE creates a model containing only `sut`. `inject` defaults to public inputs
not already connected in the custom model; `collect` defaults to all public
outputs; `cases` defaults to all declared cases. Selections must be unique and
known. An empty injection list allows a real neighboring producer to supply data.
Collectors can observe outputs also consumed by real neighbors.

Case paths resolve relative to the harness. `scenario` is required;
`expectations` is required for `test`, optional for observation through `run`.

## Scenario and assertions

A scenario contains `{version: 1, duration: seconds, events: [...]}`. Events are
`{at, input, value, app_id?}` or `{at, parameter, value}` using public aliases.
`at` must lie in `[0, duration)`. JSON values are encoded through the SDL codec
before launching. Input events become ordinary external scenario publications;
parameter events become ordinary simulated parameter updates. At most 10000
events and 64 MiB of encoded input envelopes are accepted per case. Event ordering
uses existing simulation ordering, including its parameter-update rules.

Expectations contain `{version: 1, assertions: [...]}` with a nonempty list.
Each assertion accepts:

| Field | Meaning |
| --- | --- |
| `output` | Required collected public output alias. |
| `from`, `until` | Absolute simulated seconds, default 0 and case duration; half-open window. |
| `value` | Partial JSON object match; nested objects are partial, arrays require equal length and order. |
| `count` | Exact matching-message count, including zero for absence. |
| `min_count`, `max_count` | Inclusive count bounds; cannot accompany `count`. |
| `sequence` | Exact sequence of partial message values within the window; cannot accompany `value`. |
| `tolerance` | Nonnegative absolute numeric tolerance, default zero. |

Without an explicit count bound, `value` requires at least one matching message.
`sequence` verifies all observed messages in the window in order. A `value` count
counts matching messages; omit `value` when counting all messages. Every assertion
must specify a count bound, `value` or `sequence`. All assertions must pass.

Collectors are actual CH/2 processes. Their FIFO holds 10000 messages; each file
allows at most 10000 messages and 16 MiB. The assembled collection allows at most
10000 messages and 64 MiB. Nonaccepted collector admissions, pending collector
messages, budget exhaustion and process failures produce an execution error,
never a successful incomplete test. Collection files reject non-finite floats.
Cross-output ordering is only by virtual reception time; sequence assertions are
per output, not a causal ordering across outputs.

## Commands and scope

Use `ose subsystem {init,validate,build,run,test} --help`. `validate` checks all
profiles by default, SDL contracts, encodable stimuli and assertion structure;
it does not compile or execute handlers. `build` compiles one harness.
`run` simulates one case without checking expectations; `test` simulates selected
cases and evaluates expectations. Runtime tests require local deployment;
`--profile local` can derive it from a distributed source.

`--integration-profile` defaults to `standalone` outside validation. `--case`
selects one case; otherwise `test` runs all profile cases and `run` uses the first.
`--workers` controls simulation parallelism. `--microsteps` and
`--max-microsteps` use the existing bounded activating-feedback semantics.
`--report junit --output FILE`
exports test results. Exit codes: 0 success, 1 assertion failure, 2 configuration
or build error, 3 execution/collection error. Result files distinguish these
outcomes. Interrupted executions do not acquire a successful verdict.

The editor discovers recursive definitions, exposes public ports in its graph,
opens shared definitions with separate model layouts and validates unsaved
compositions through the same resolver. Creation, instantiation, public-port
exposure and local-component extraction use previewed, undoable commands.
Extraction preserves registered deployment variants, existing handler paths and
schema/project substitution contexts through relative references. Source roots
outside the editor project require `--source-root`.

CLI diagnostics, component lists, logs, recording points and measurements use
these runtime identities directly, without separate display-name aliases.
Log component filters accept the dotted runtime names and globs.

Observation metadata includes logical component/subscription paths and public
message interfaces, without source locations or parameter values. The console
can navigate instance scopes while retaining runtime identities, diagnostics and
crossing links. Rebuild older plans to obtain this metadata.

Filesystem dependencies and simulation harnesses remain the initial packaging
and test scope. Remote package locking and reactive temporal assertion syntax
are subsequent work.
