# `model.json` grammar — OSE v2

This document describes the application model fields accepted by the OSE
validator. The filename `model.json` is conventional: `--model` selects the
actual path. This reference covers application model version **2**.

The model defines SDL contracts, components and their connections. Component
languages, handler paths, build commands, transport and placement belong in a
separate deployment document, whose version is **1**. See
[architecture](architecture.md) and [generated containers](callbacks.md).

## Conventions and JSON syntax

- A required field must be present, even when its value may be an empty object
  or array.
- Optional fields may be omitted; `null` does not mean “absent”.
- Property order is unrestricted. Duplicate keys and unknown fields are rejected
  at every level described here.
- The document must be a JSON object no larger than **1 MiB** (`1,048,576` bytes).
  Comments are not part of JSON syntax.
- A dictionary is an object whose keys are names chosen by the application.
  The contents of `parameters` and `initial` follow their own rules below.

The following structural notation is not literal JSON: `?` marks an optional
field, `Dictionary<T>` maps names to values of type `T`, and `Array<T>` contains
values of type `T`.

```text
Model = {
  version: 2,
  schemas: Path,
  components: Dictionary<Component>,
  subscriptions: Array<Subscription>,
  external_topics?: Dictionary<SDLContract>,
  shared_parameters?: Dictionary<SharedParameter>
}

Component = {
  inputs: Dictionary<MessageInput | TimerInput>,
  outputs: Dictionary<SDLContract>,
  parameters?: JSONObject,
  parameter_bindings?: Dictionary<ParameterBinding>,
  recording_types?: Dictionary<SDLContract>
}

SDLContract = { root: SDLName, schema?: Path }
MessageInput = {
  root: SDLName,
  schema?: Path,
  kind?: "message",
  mode?: "push" | "pull",
  timeout?: PositiveSeconds,
  activates?: Activations
}
TimerInput = {
  kind: "timer",
  timer: {
    mode: "periodic" | "one_shot",
    delay: PositiveSeconds,
    start?: "automatic" | "manual"
  },
  activates?: Activations
}
Activations = { message?: Array<OutputName>, timeout?: Array<OutputName> }

Subscription = {
  id: NonemptyString,
  from?: "component.output",
  from_topic?: ExternalTopicName,
  to: "component.input",
  qos?: QoS
}
SharedParameter = { root: SDLName, schema?: Path, initial: SDL_JSON_Value }
ParameterBinding = { path: ParameterPath, access: "read" | "write" | "read_write" }
```

Conditional restrictions, including the events allowed in `Activations` and the
exclusive choice between `from` and `from_topic`, are specified below.

## Model root

| Field | Type | Required | Meaning / default |
| --- | --- | --- | --- |
| `version` | integer | Yes | Must be exactly `2`. |
| `schemas` | nonempty string | Yes | Default SDL source file or directory. |
| `components` | nonempty object | Yes | Dictionary of components, keyed by name. |
| `subscriptions` | array | Yes | Subscription list; `[]` is allowed. |
| `external_topics` | object | No | Dictionary of external topics; defaults to `{}`. |
| `shared_parameters` | object | No | Dictionary of shared parameter blocks; defaults to `{}`. |

`schemas` and every `schema` field are resolved relative to the **model file's
directory**, including schemas for external topics, parameters and recording
types. Absolute paths are also accepted. An omitted `schema` uses the model's
global `schemas` value.

## Component definition

Each entry in `components` accepts only the following fields:

| Field | Type | Required | Meaning / default |
| --- | --- | --- | --- |
| `inputs` | object | Yes | Dictionary of message or timer inputs; `{}` is allowed. |
| `outputs` | object | Yes | Dictionary of SDL outputs; `{}` is allowed. |
| `parameters` | object | No | Application-defined static configuration; defaults to `{}`. |
| `parameter_bindings` | object | No | Bindings to `shared_parameters` blocks; defaults to `{}`. |
| `recording_types` | object | No | Additional SDL contracts for diagnostics; defaults to `{}`. |

The application chooses the keys and values inside `parameters`. They are not
SDL declarations and are not validated as shared parameter blocks.

### SDL contracts and outputs

An output, an external topic or a `recording_types` entry describes a contract:

| Field | Type | Required | Meaning / default |
| --- | --- | --- | --- |
| `root` | nonempty string | Yes | Logical SDL message name, optionally qualified by its package. |
| `schema` | nonempty string | No | Schema containing the message; defaults to the model's `schemas`. |

The `root` message must exist in the resolved SDL catalogue. An output does not
accept `kind`, `mode`, `timeout`, `timer` or `activates` fields.

### Message input

| Field | Type | Required | Meaning / default |
| --- | --- | --- | --- |
| `kind` | string | No | Only `"message"` is allowed; this is the default. |
| `root` | nonempty string | Yes | SDL message type received by this input. |
| `schema` | nonempty string | No | Input-specific schema; defaults to `schemas`. |
| `mode` | string | No | `"push"` or `"pull"`; defaults to `"push"`. |
| `timeout` | strictly positive number | No | Inactivity timeout in seconds; omitted means no timeout. |
| `activates` | object | No | Outputs permitted for each event; defaults to `{}`. |

A **Push** input activates its callback when a message arrives and, if `timeout`
is set, when inactivity expires. Valid receptions reset the inactivity deadline;
callback completion does not reset it.

A **Pull** input is passive: another callback reads it explicitly. It forbids
`timeout` and accepts no events in `activates`, so that object must be omitted
or equal to `{}`. Push/Pull mode is independent of the FIFO/latest QoS setting.

### Timer input

| Field | Type | Required | Meaning / default |
| --- | --- | --- | --- |
| `kind` | string | Yes | Exactly `"timer"`. |
| `timer` | object | Yes | Timer settings described below. |
| `activates` | object | No | Only the `timeout` event is allowed; defaults to `{}`. |

The `timer` object accepts:

| Field | Type | Required | Meaning / default |
| --- | --- | --- | --- |
| `mode` | string | Yes | `"periodic"` or `"one_shot"`. |
| `delay` | strictly positive number | Yes | Period or delay before expiration, in seconds. |
| `start` | string | No | `"automatic"` or `"manual"`; defaults to `"automatic"`. |

A timer has no SDL contract: `root`, `schema`, `mode` and `timeout` are forbidden
at the input level. Its mode belongs in `timer.mode`. A timer cannot be the
destination of a subscription.

An automatic timer first expires after `delay`, then preserves its cadence if
periodic. A manual timer must be armed by component code. The APIs also allow
declared timers to be armed or disarmed explicitly.

`timeout` and `delay` must be finite, strictly positive numbers, never booleans.
They are converted to unsigned 64-bit nanoseconds, rounded to the nearest
nanosecond with half values rounded up and a minimum of 1 ns. The scaled duration
before rounding must not exceed `2^64 - 1` ns.

### Publication permissions: `activates`

`activates` maps each allowed event to an array of output names from the **same
component**. Each name must exist in `outputs` and occur at most once per array.
An omitted array is equivalent to `[]`.

| Input | Allowed events in `activates` |
| --- | --- |
| Push message without `timeout` | `message` |
| Push message with `timeout` | `message`, `timeout` |
| Pull message | None |
| Timer | `timeout` |

Example of a Push input permitting an output for both events:

```json
{
  "root": "Measurement",
  "timeout": 0.5,
  "activates": {
    "message": ["result"],
    "timeout": ["result"]
  }
}
```

These links grant publication permission to the active callback. They do not
require a publication and do not create subscriptions. Without a link to an
output, that callback cannot publish to it.

## Subscriptions: `subscriptions`

| Field | Type | Required | Meaning / default |
| --- | --- | --- | --- |
| `id` | nonempty string | Yes | Identifier unique across all subscriptions. |
| `from` | string | One of the two | Internal output, written as `component.output`. |
| `from_topic` | string | One of the two | Key declared in `external_topics`. |
| `to` | string | Yes | Message input, written as `component.input`. |
| `qos` | object | No | Quality of service; defaults to `{}`. |

Specify **exactly one** of `from` and `from_topic`. Referenced components and
ports must exist. One output may feed several subscriptions, and several
producers may feed the same input.

Both endpoints must have the same logical SDL `root` name. Their schemas may
differ if they are compatible from publisher to consumer, checking required SDL
fields with `require_required=True`.

### The `qos` object

| Field | Type / values | Default |
| --- | --- | --- |
| `buffer` | `"fifo"`, `"latest"` | `"fifo"` |
| `capacity` | strictly positive integer | `1024`; `1` with `latest` |
| `overflow` | `"reject_new"`, `"drop_oldest"`, `"wait"` | `"reject_new"`; `"drop_oldest"` with `latest` |
| `ack` | `"none"`, `"received"` | `"none"` |
| `retry` | `"none"` or the object described below | `"none"` |
| `wait_timeout_ms` | strictly positive integer | `100` |
| `durability` | Only `"memory"`, accepted for compatibility | `"memory"`, removed during normalization |

When `retry` is an object, it accepts:

| Field | Type | Default within a `retry` object |
| --- | --- | --- |
| `max_retries` | integer greater than or equal to zero | `3` |
| `ack_timeout_ms` | strictly positive integer | `100` |
| `backoff_multiplier` | strictly positive integer | `2` |

With `retry: "none"`, the retry count is zero. An empty `retry` object therefore
enables the object defaults, including three retries. The initial send is
separate from those retries.

Constraints:

- A `max_retries` value greater than zero requires `ack: "received"`.
- `buffer: "latest"` requires `capacity: 1` and `overflow: "drop_oldest"`;
  explicitly contradictory values are rejected.
- All subscriptions targeting the same input must share the **resolved** values,
  including defaults, of `buffer`, `capacity`, `overflow` and `wait_timeout_ms`.
  ACK and retry settings remain independent per subscription.
- `overflow: "wait"` requires TCP; UDP rejects it.
- Transport (`tcp` by default, or `udp`) and routing are configured under
  `subscriptions.<id>` in `deployment.json`, not in `qos`.
- Queues live in RAM. A `received` ACK confirms admission into memory; it does
  not guarantee completed business processing or persistence after restart.

See [delivery rules](delivery.md).

## External topics: `external_topics`

This object maps topic names to `{ "root": ..., "schema": ... }` contracts.
A name must contain between 1 and 200 characters; dots are allowed. A
subscription uses this exact name in `from_topic`.

An external topic must not:

- have the same name as an internal output, `component.output`;
- begin with `component.record.` for a declared component: that namespace is
  reserved for its internal recording points.

Declaring a topic describes its contract. External service access permissions
are configured separately in the deployment.
`external_topics` is not a list of permitted producer identities. A producer
absent from the model may also declare and publish any existing component output
with a compatible SDL contract. Multiple producers may share that topic without
changing subscriptions. Use the output's existing `from` subscription; it does
not need a duplicate `external_topics` declaration. See
[independent producers](clients.md#feeding-a-model-input).

## Shared parameters: `shared_parameters`

This object maps parameter paths to descriptions:

| Field | Type | Required | Meaning / default |
| --- | --- | --- | --- |
| `root` | nonempty string | Yes | SDL message type of the block. |
| `schema` | nonempty string | No | Block schema; defaults to `schemas`. |
| `initial` | object matching the SDL message | Yes | Complete initial value, encoded during validation and build. |

At most **4096 blocks** are accepted. A path must contain between 1 and 200
UTF-8 bytes. It consists of nonempty segments separated by `/`, each matching
`[A-Za-z0-9_-]+`. Example: `machines/1/detection`.

The JSON representation of `initial` follows SDL types:

- messages and nested messages: objects; unknown fields are rejected;
- required fields: must be present, including repeated sequences;
- optional fields: may be omitted or set to `null`;
- sequences and arrays: JSON arrays, with fixed dimensions checked;
- enums: a value label or its corresponding integer;
- booleans, integers, finite real numbers and strings: corresponding JSON types,
  with SDL constraints checked during encoding;
- complex numbers: objects such as `{ "real": 1.0, "imag": 2.0 }`.

Encoded parameter values also have storage and frame limits: current blocks may
occupy at most **64 MiB** in total, and individual values and catalogues must fit
the deployment's transport limit and the component's 1 MiB frame limit.

### Component bindings: `parameter_bindings`

Each component may declare at most **256 bindings**, keyed by local alias.
A binding contains:

| Field | Type | Required | Meaning |
| --- | --- | --- | --- |
| `path` | string | Yes | Existing path in `shared_parameters`. |
| `access` | string | Yes | `"read"`, `"write"` or `"read_write"`. |

Aliases must be distinct ignoring case, contain at most 200 characters, not be
Python keywords and match `[A-Za-z](?:[A-Za-z0-9]|_[A-Za-z0-9])*`. This excludes
consecutive or trailing underscores and names beginning with a digit or
underscore.

Several components may bind the same block. Readers receive initial values
before `on_start`, and updates are applied between callbacks. External client
permissions belong in the deployment's `parameter_clients` section. See
[shared parameters](parameters.md).

## Recording types: `recording_types`

Each entry maps an alias to an SDL contract, `{ "root": ..., "schema": ... }`.
This field adds a schema needed for component diagnostics without creating a
port or subscription. The alias is a declaration key; it does not define a
recording point's name.

All message types from schemas already used by the component are available for
recording. Schemas used by its ports, parameter bindings and `recording_types`
contribute to its generated types. A component without these contracts uses
the global `schemas` source.

Component code declares recording point names and descriptions during
`on_start`. C helpers are named `ose_declare_recording_Xxx` and `ose_record_Xxx`.
Rust uses `ctx.declare_recording_point::<T>` and `ctx.record`. See
[internal recording](recording.md#component-owned-internal-points).

## Names, deployment and global constraints

- Component and port names must be nonempty and contain no dots. An endpoint
  contains exactly one dot: `component.port`.
- Generated containers also require valid identifiers. Input names `start`,
  `stop` and `tick` are reserved.
- C and Rust additionally require portable ASCII identifiers starting with a
  letter and excluding reserved words. Collisions with generated symbols may
  also be rejected, including collisions between ordinary publication methods
  and methods ending in `_with_id`.
- Java and Ada have additional language, case and namespace constraints; see
  [their containers](java-ada.md).
- SDL bindings needed by the same component must not introduce conflicting
  generated modules or type bindings. Python schema module names must not
  shadow standard library or SDK modules.
- The deployment must cover **exactly** the model's components. Each deployment
  entry chooses either `command` or `container`.
- A component with no active input receives no input callbacks. A periodic
  source therefore needs an explicit timer.
- Data cycles are possible, including through Pull inputs. Cycles in the
  event → output → Push input activation graph are accepted during compilation
  but require simulated execution with `--microsteps`. Real-time execution
  rejects them.

Old model v1 fields, such as component triggers and input `required` flags, are
not part of this grammar. See [v2 migration](model-v2-migration.md).

## Complete validation example

Create the following three files in the same directory. This model demonstrates
a timer, a Push input, a Pull input, an external topic, a shared parameter block
and a diagnostic type.

### `messages.sdl`

```sdl
message Measurement {
    uint64 sequence;
    fl64 value;
}

message Settings {
    fl64 gain;
}

message Diagnostic {
    fl64 intermediate;
}
```

### `model.json`

```json
{
  "version": 2,
  "schemas": "messages.sdl",
  "components": {
    "source": {
      "inputs": {
        "clock": {
          "kind": "timer",
          "timer": {
            "mode": "periodic",
            "delay": 0.1,
            "start": "automatic"
          },
          "activates": {"timeout": ["signal"]}
        }
      },
      "outputs": {"signal": {"root": "Measurement"}},
      "parameters": {"machine_id": 1}
    },
    "analyzer": {
      "inputs": {
        "input": {
          "root": "Measurement",
          "kind": "message",
          "mode": "push",
          "timeout": 0.5,
          "activates": {
            "message": ["result"],
            "timeout": ["result"]
          }
        },
        "reference": {"root": "Measurement", "mode": "pull"}
      },
      "outputs": {"result": {"root": "Measurement"}},
      "parameter_bindings": {
        "settings": {"path": "analyzer/settings", "access": "read"}
      },
      "recording_types": {"intermediate": {"root": "Diagnostic"}}
    }
  },
  "subscriptions": [
    {
      "id": "signal",
      "from": "source.signal",
      "to": "analyzer.input",
      "qos": {
        "buffer": "fifo",
        "capacity": 64,
        "overflow": "reject_new",
        "ack": "received",
        "retry": {"max_retries": 3}
      }
    },
    {
      "id": "reference",
      "from_topic": "external.reference",
      "to": "analyzer.reference",
      "qos": {"buffer": "latest"}
    }
  ],
  "external_topics": {"external.reference": {"root": "Measurement"}},
  "shared_parameters": {
    "analyzer/settings": {"root": "Settings", "initial": {"gain": 2.0}}
  }
}
```

### `deployment.json`

This deployment selects two Python containers so the example can be validated
without native compilation. Handler files may be absent during validation.

```json
{
  "version": 1,
  "components": {
    "source": {
      "container": {"language": "python", "handler": "components/source.py"}
    },
    "analyzer": {
      "container": {"language": "python", "handler": "components/analyzer.py"}
    }
  }
}
```

### Commands

With the OSE CLI installed or the checkout environment loaded:

```sh
ose validate --model model.json --deployment deployment.json
ose build --model model.json --deployment deployment.json
```

If needed, add `--sdl-root /path/to/sdl` or set `SDL_ROOT`. `validate` checks the
model, deployment and SDL contracts, generating temporary artifacts. `build`
creates missing handler skeletons and artifacts under `build/`, including
`plan.json`. Implement callbacks before running the application; existing
handlers are never rewritten.

## Validator source references

This reference describes current code behavior. Validation is implemented in:

- [JSON loading](../src/ose/modeling/model.py): version, size, duplicate keys
  and field sets;
- [graph structure](../src/ose/modeling/validation.py): model root, components,
  subscriptions and deployment consistency;
- [inputs and activations](../src/ose/modeling/activation.py): ports, timers,
  events and dependencies;
- [durations](../src/ose/timing.py): conversion to nanoseconds;
- [QoS](../src/ose/delivery/policies.py): defaults and reception policies;
- [parameter declarations](../src/ose/parameters/model.py) and
  [value conversion](../src/ose/parameters/values.py);
- [model compilation](../src/ose/modeling/compiler.py): schema resolution, SDL
  compatibility and container generation.
