# OSE Model Editor: language coverage inventory

Status: required coverage baseline, version 0.1, 7 November 2026.

This inventory is part of the [editor specification](specification.md). Every
row is required for the complete initial authoring release. Current implementation
and qualification mappings are in
[`coverage.json`](../src/ose_editor/coverage.json) and the
[validation record](validation.md); implemented surfaces do not imply complete
row-specific qualification. The initial authority is repository
commit `d4ccf89f0c21182aac27aec2f14aee35202dcc79`.

For every row, qualification MUST demonstrate structured inspection, creation,
modification, source mapping, no-op preservation, semantic round-trip and
undo/redo where applicable. Validation rules need accepted and rejected
fixtures. Read-only derived information needs inspection/mapping evidence.
At implementation time a machine-readable manifest MUST map these IDs to
controls, adapters and fixture/test IDs; a missing mapping blocks a claim of
complete coverage. Grammar/version changes require an inventory review.

## 1. OSE application model JSON, version 1

Authorities: [loader](../../ose/src/ose/modeling/model.py),
[structural validator](../../ose/src/ose/modeling/validation.py),
[compiler](../../ose/src/ose/modeling/compiler.py),
[parameter validator](../../ose/src/ose/parameters/model.py).

| ID | Source elements | Structured surface and required behavior |
| --- | --- | --- |
| M01 | Root `version`, `schemas`, `components`, `subscriptions`, `external_topics`, `shared_parameters` | Project properties; distinguish required/optional keys, source input files/directories and absent/empty objects. |
| M02 | Component names and maps | Graph/explorer commands; portable-name guidance and exact OSE constraints; identity-preserving rename. |
| M03 | Component `inputs`, `outputs` | Port tables and graph handles; empty collections, naming, direction and endpoint references. |
| M04 | Port `root`, `schema` | Type picker scoped to the correct source input, explicit override/inherited input and resolved logical name. |
| M05 | Port `required`, `timeout` | Declared/effective values; required defaults, optional presence, finite positive fractional seconds and timeout applicability. Preserve validator-accepted fields even where they have no scheduling effect on outputs. |
| M06 | `trigger.mode`, `trigger.period_ms` | `on_change`/`periodic`, positive integer period and source-component requirement. |
| M07 | Component `parameters` | Free, lossless JSON tree, including nested objects/arrays, null, booleans, strings and exact numbers. |
| M08 | `parameter_bindings.<alias>.path`, `.access` | Shared block selector, alias controls, `read`/`write`/`read_write` and usage relations. |
| M09 | `recording_types.<alias>.root`, `.schema` | Component diagnostic contract declarations and scoped schema navigation; does not create runtime recording points. |
| M10 | Subscription `id`, `from`, `from_topic`, `to` | Link inspector; exactly one source form, endpoint/topic pickers and refactoring across deployments. |
| M11 | External topic names, `root`, `schema` | External publisher nodes, scoped contracts, reserved-name/collision validation. |
| M12 | Shared parameter paths, `root`, `schema`, `initial` | Path tree, scoped contract and complete SDL-typed initial-value editor. |
| M13 | Graph invariants | One producer per input, required connectivity, component acyclicity, root identity and directional SDL compatibility. |
| M14 | JSON source rules | Duplicate-key detection, exact numbers, version/type/size checks, unknown-key preservation with diagnostics; source order/spacing retained. |

## 2. QoS and effective policies

Authority: [policy resolver](../../ose/src/ose/delivery/policies.py).
Authored QoS belongs to the model; transport and routing belong to deployment.

| ID | Source elements | Structured surface and required behavior |
| --- | --- | --- |
| Q01 | `qos.buffer` | `fifo`/`latest`, omission and effective value. |
| Q02 | `qos.capacity` | Positive integer; latest capacity one and implicit latest defaults. |
| Q03 | `qos.overflow`, `qos.wait_timeout_ms` | `reject_new`/`drop_oldest`/`wait`; positive wait timeout and UDP restriction. |
| Q04 | `qos.ack` | `none`/`received`; admission acknowledgement semantics. |
| Q05 | `qos.retry` | String `none` or object, including an empty object with resolver defaults. |
| Q06 | Retry `max_retries`, `ack_timeout_ms`, `backoff_multiplier` | Nonnegative retry budget, positive timing/backoff values and ACK dependency. |
| Q07 | Legacy `qos.durability: "memory"` | Preserve and identify accepted legacy syntax; reject unsupported persistence through OSE. New controls must not invent disk durability. |
| Q08 | Resolved policy | Read-only authored/default provenance and effective combination; never overwrite authored omission merely to display defaults. |

## 3. OSE deployment JSON, version 1

Authorities: [validator](../../ose/src/ose/modeling/validation.py),
[compiler](../../ose/src/ose/modeling/compiler.py),
[container identities](../../ose/src/ose/modeling/managed_containers.py),
[process identities](../../ose/src/ose/execution/identity.py),
[external limits](../../ose/src/ose/config.py).

| ID | Source elements | Structured surface and required behavior |
| --- | --- | --- |
| D01 | Root `version`, `components`, `subscriptions`, `max_frame`, `max_datagram`, `broker`, `execution`, `parameter_clients`, `process_names` | Deployment properties with declared/default provenance and profile comparison. |
| D02 | Component map | Exact correspondence to model components; missing/excess entries, duplicate profile and rename handling. |
| D03 | Component `command` | Nonempty argument vector, mutually exclusive with `container`; no implicit shell interpretation or execution. |
| D04 | `container.language`, `.handler` | Python/C/Rust/Java/Ada selectors, path/suffix/source-versus-build location checks. |
| D05 | `container.class`, `.unit` | Required Java class/Ada unit identities, language applicability, naming and reserved namespace diagnostics. |
| D06 | Component `build`, `cwd`, `env` | Lists of nonempty argument vectors, working directory and environment map; no execution during authoring. |
| D07 | Substitutions `{python}`, `{project}`, `{sdl_root}`, `{build}`, `{generated}` | Preserve authored strings and show path/value resolution using OSE semantics. |
| D08 | `execution` | `local`/`distributed`, omitted local default and dependent policy checks. |
| D09 | Component `placement` | `local` or constraints object, preserving omitted/empty forms. |
| D10 | Placement `node_selector`, `resources.requests`, `resources.limits` | String maps and resource quantities; remote-placement restrictions. |
| D11 | Subscription `transport`, `routing` | `tcp`/`udp`, `broker`/`direct`, absent entries/defaults, distributed TCP and external-input routing constraints. |
| D12 | `max_frame`, `max_datagram` | Integer/range validation, authored/effective limits. |
| D13 | `broker.host` | Supported local bind choices and omission/default behavior. |
| D14 | `broker.external.enabled`, `.max_clients`, `.max_subscriptions`, `.max_capacity`, `.max_topics`, `.max_catalogues`, `.max_wait_timeout_ms` | Boolean enablement and all positive integer client limits; inherited defaults. |
| D15 | `process_names.prefix`, `.components` | Prefix and per-component alias editing, resolved identities, collision/length constraints and rename impact. |
| D16 | `parameter_clients.<identity>.read`, `.write`, `.token` | Client identity, exact block-path lists, masked token editing/preservation and local token requirements. |
| D17 | JSON source rules and profile comparison | Duplicate/unsupported keys and versions, exact values, per-document path bases and multi-profile refactorings. |

## 4. Textual SDL source grammar

Authorities: [formal syntax](../../sdl/docs/formal_syntax.md),
[schema language](../../sdl/docs/schema.md) and
[source parser/compiler](../../sdl/sdlc/generator.py).

| ID | Source elements | Structured surface and required behavior |
| --- | --- | --- |
| S01 | UTF-8 source units and recursive directory discovery | Input-set explorer; correct generation-unit identity, source scope and cross-file resolution. |
| S02 | `package` and global/qualified/unqualified references | Package explorer/reference picker; current-package lookup and explicit global leading dot, no invented import syntax. |
| S03 | `message`, `structure`, `struct` named declarations | Message editor with declaration-spelling property; empty messages allowed. |
| S04 | Anonymous nested `message`/`structure`/`struct` fields | Recursive tree and field editor; derived anonymous names inspectable, identity changes previewed. |
| S05 | `enum`, item names and signed int32 values | Ordered item table, negative/zero values, uniqueness and nonempty constraint. |
| S06 | `bool`, `int8/16/32/64`, `uint8/16/32/64` | Primitive type picker and exact ranges/values. |
| S07 | `fl32`, `fl64`, `c32`, `c64`, `string` | Primitive picker, precision and complex-value controls where values are edited. |
| S08 | Message/enum field references | Scoped declaration navigation, forward/cross-file references and cycle checks. |
| S09 | Explicit and implicit numeric field IDs | Declared/resolved ID columns, positive uint32 limits, uniqueness and source-order numbering rules. |
| S10 | `required`, `optional`, `repeated`, `packed`, omitted modifier | Cardinality selector; explicit/implicit default retained and fixed-logical-size restrictions explained. |
| S11 | Fixed arrays `T[d1][d2]...` | Ordered dimension controls, literal/constant reference, effective sizes and required/fixed-element restrictions. |
| S12 | Top-level `const NAME = expression` | Named expression editor, cross-package/forward references and dependency tree. |
| S13 | Constant literals, references, unary signs, `+`, `-`, `*`, `/`, parentheses | Preserve expression spelling, show exact integer evaluation, precedence, exact division, cycles and range/depth errors. |
| S14 | Top-level `alias NAME = TYPE`, alias chains | Alias editor and final target, cross-package/forward resolution, missing-target/cycle diagnostics. |
| S15 | `wire(bits=N)` | Type-dependent integer/enum/bool/quantized-float bit widths and valid numeric ranges. |
| S16 | `wire(q=Q)` | Exact authored decimal/exponent token, positive finite quantum, `bits` dependency and quantization preview. |
| S17 | `wire(encoding=varint/zigzag)` | Encoding selector, type/range restrictions and mutual exclusion with `bits`. |
| S18 | `wire(align=N,pad_after=N)` | Alignment/padding controls, allowed ranges, explicit neutral settings and element/whole-field semantics. |
| S19 | Wire option ordering/combinations | Preserve declared order, enforce recognized unique keys and nonempty legal clauses. |
| S20 | `//`, `/* ... */`, `/** ... */` | Source-preserving comment editing, attached documentation fields, leading/trailing association and placement errors. |
| S21 | Source ordering, punctuation, whitespace and numeric spelling | Byte-identical no-op save; unaffected ranges preserved, explicit formatting/spelling edits previewed. |
| S22 | Names, namespace uniqueness and backend restrictions | Exact language acceptance versus portability guidance; converted-name and selected-backend collisions. |
| S23 | Size/depth/count constraints | Compiler-backed limits on IDs, declarations, dimensions, nesting, catalogue size and file dependencies. |
| S24 | Source and resolved views | Alias/constant intent, implicit defaults and anonymous declarations retained alongside derived types/IDs/dimensions. |

SDL has no source imports, inheritance or field default-value declarations.
The interface MUST NOT offer these as accepted SDL grammar features. Binary
wire restrictions are validated by SDL, not inferred from an unrelated target
language's memory layout.

## 5. SDL descriptors and format adapters

Authority: [source format reference](../../sdl/docs/schema_formats.md) and
[format adapters](../../sdl/sdlc/schema_formats.py).

| ID | Source elements | Structured surface and required behavior |
| --- | --- | --- |
| F01 | `.sdl`, `.sdl.json`, `.sdl.yaml`/`.sdl.yml`, `.sdl.xsd` | Recognize dedicated suffixes and mixed input sets; distinguish schema descriptors from ordinary application JSON. |
| F02 | Descriptor `format`, `version`, `package`, `doc`, `constants`, `aliases`, `enums`, `messages`, `generated_by` | Source-unit properties and all declaration editors; preserve absent defaults and generated-marker provenance. |
| F03 | Constant `{value, doc}`, alias `{target, doc}` | Format-aware controls, retaining the representation's actual capabilities. |
| F04 | Enum `{values, doc}` and item `{name, value, doc}` | Ordered item controls and exact signed values. |
| F05 | Message `{fields, doc}` and field `{name, type, id, modifier, dimensions, wire, doc}` | Complete field editor; preserve optional keys/defaults, explicit neutral wire values and empty structural lists. |
| F06 | Wire numeric/string `q`, dimensions and anonymous descriptor types | Lossless literal handling, anonymous structure identity/navigation and format validation. |
| F07 | YAML JSON-style scalars, comments and rejected YAML features | Format-aware source preservation; safe accepted scalar model, duplicate/cycle/merge/custom-object diagnostics and optional dependency handling. |
| F08 | Annotated XSD structure, SDL metadata, documentation and unit ownership | Edit one semantic declaration while keeping XML structure, appinfo and affected dependency copies coherent. |
| F09 | XSD naming/profile constraints | Reversible name encoding, explicit profile boundaries, no silent acceptance of arbitrary XSD. |
| F10 | Explicit conversion/export | Preview normalization/loss of source intent, preserve logical semantics where representable, reject unsupported conversion without writes. |

Expressions in textual SDL constants and integer values in descriptors are
different authoring representations. Compiler exports can normalize expressions,
resolve field aliases and lift anonymous declarations. Structured edits MUST
preserve the maintained format; conversion is a separate reviewed operation.

## 6. Derived inspection and qualification

| ID | Derived information or evidence | Required behavior |
| --- | --- | --- |
| R01 | Resolved SDL2 catalogues and identities | Read-only inspection with source/root mapping; never used to reconstruct the original authoring source. |
| R02 | Publisher-to-consumer compatibility | Show direction, matching logical root, field-ID matching, nested differences and OSE's required-field policy. |
| R03 | SDL-typed initial-value encode/decode | Explain normalized/quantized preview, exact integers, optional absence, enums, complex values, arrays and sequences. |
| R04 | Uses and impact | Scoped cross-file references, registered deployments, affected handlers for review and explicitly unassessed external consumers. |
| R05 | Public tool compatibility | Record actual OSE/SDL versions, capabilities, accepted/rejected fixture results and skipped checks. |
| R06 | Coverage manifest | Every inventory ID maps to structured controls/adapters and meaningful passing evidence before claiming complete coverage. |

Existing fixture families to reuse include OSE acquisition/shared-parameter/
multilingual examples, Vibration Watch's contract override, SDL compatibility
and representation fixtures, and the mixed-format SDL example. Their current
CLI results are the baseline; the editor must add authoring/fidelity/refactoring
evidence rather than assuming compiler tests prove UI coverage.
