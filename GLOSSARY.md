# SDL, OSE, Web Console and Model Editor glossary

This glossary defines the terminology used across the projects in this
repository. **SDL** describes and encodes data; **OSE** executes component graphs;
the **OSE Web Console** observes and configures running applications; the
**OSE Model Editor** edits application models, deployments and schema sources.
Definitions describe this implementation. Links in each section lead to the
detailed guides.

Updated through commit `b25accb` (15 November 2026), including application model
v2, interactive simulation, Thermal Workshop and direct local browser access.

## Projects and interfaces

| Term | Meaning |
| --- | --- |
| **SDL — Schema Design Language** | The schema language, compiler, generated bindings and runtimes used to describe typed messages and exchange their binary representations. |
| **SDL2** | The runtime catalogue and binary message format implemented by SDL. Source `.sdl` files and runtime SDL2 catalogues have different syntax. |
| **OSE — Orchestration, Simulation, Exécution** | The framework that validates, builds and executes graphs of components using SDL message contracts. The Python package and CLI are named `ose`. |
| **OSE Web Console / WS** | The separate `ose-ws` application: a Python web server and bundled browser interface for components, logs, measurements, recordings and parameters. It connects to a running real-time application or simulation server; its Simulation page controls the latter. |
| **OSE Model Editor** | The separate `ose-editor` application: a local Python service and bundled browser interface for reading and editing OSE JSON models, deployments and SDL schema sources. It works without a running application; saved changes enter execution through a subsequent application build and run. |
| **CLI — Command-line interface** | A terminal interface such as `ose`, `ose-ws`, `ose-editor` or `sdl-recording`. The interactive `ose record` console has its own commands. |
| **SDK — Software development kit** | The language APIs and protocol helpers used to implement components or external clients. Generated SDL bindings supply the application message types. |
| **API — Application programming interface** | A defined interface to functionality, such as an SDL codec function, an OSE client method or a Web Console HTTP endpoint. |
| **RPC — Remote procedure call** | A request/reply operation over a connection, such as reading OSE status or replacing a parameter block. |
| **Frontend / backend** | The browser interface and the server-side application, respectively. The console's Vue/TypeScript frontend calls its Python backend, which connects to OSE services. The model editor's frontend calls a separate Python backend managing application sources and validation. |
| **Runtime** | Code active during execution. Depending on context, this means an SDL language runtime providing codecs or the OSE execution services managing components and delivery. |
| **Application environment** | Shell configuration selecting an application's source root, build and command defaults. `ose app` exposes its build/run/record/parameter workflows; `ose-ws` and `ose-editor` use the same context for application discovery. |
| **Thermal Workshop** | The synthetic console workload in `thermal-workshop/`: 20 Python components, feedback through Pull inputs, variable nested SDL messages, editable parameters and scalar measurements. Its load profiles describe offered work, not measured throughput or a physical plant model. |

References: [repository overview](README.md), [SDL](sdl/README.md),
[OSE](ose/README.md), [Web Console](ose-ws/README.md),
[Model Editor](ose-editor/README.md),
[application environment](docs/application-environment.md),
[Thermal Workshop](thermal-workshop/README.md).

## Schemas, types and values

| Term | Meaning |
| --- | --- |
| **Schema** | A description of message types, fields, enums and their constraints. SDL accepts textual SDL, JSON, YAML and a supported annotated XSD profile. The console also exposes schema descriptions for typed editors. |
| **JSON / YAML / XSD** | Structured text formats accepted for SDL source schemas: JavaScript Object Notation, YAML and an annotated XML Schema Definition profile. OSE model/deployment documents and console configuration use JSON. |
| **Message / structure / struct** | A named SDL record containing fields. The source keywords `message`, `structure` and `struct` are interchangeable. A message instance is one concrete value of that type. |
| **Field** | A named member of a message with a numeric ID, type, cardinality and optional wire representation settings. |
| **Field ID** | The positive integer identifying a field within its message for schema matching. IDs may be assigned automatically in source; explicit IDs help preserve identity across revisions. |
| **Logical type name** | The SDL identity of a type, including its package when present, such as `acquisition.SignalBlock`. Generated language symbols can differ from this name. |
| **Package** | A namespace for SDL declarations, written as dotted segments. It determines qualified logical names and the generated language namespace or file hierarchy. |
| **Root type** | The top-level message selected for an encoded payload, port contract, filter or recording channel. A catalogue can describe several possible roots. |
| **Enum** | A declared set of named signed integer values. The console presents enum choices through their schema. |
| **Cardinality** | Whether a field contains one required value, an optional value, or a sequence: `required`, `optional`, `repeated` or `packed`. |
| **Required field** | A singular SDL field without an absence marker; `required` is the default source modifier. OSE's separate compatibility policy can require publishers to provide all consumer-required fields. |
| **Optional field / presence** | A field that can be absent or contain one value. Absence differs from a present zero, empty string or empty message; the console provides presence controls. |
| **Repeated field** | A variable-length sequence of zero or more values, encoded with an element count. |
| **Packed field** | A sequence using the repeated sequence layout and supporting bulk processing for suitable fixed-size element types. Custom variable-length integer encodings can affect that eligibility. |
| **Fixed-size array** | An array whose positive dimensions are part of the schema, such as `fl32[3][4]`. It uses row-major wire order without an element count and requires fixed-size elements. |
| **Nested message** | A message used as another message's field type. An inline anonymous declaration receives a generated logical name such as `Envelope$1`. |
| **Type alias** | A source name substituted by its target type before generation. An alias creates no separate wire type. |
| **Dimension constant** | A source integer constant or expression used in array dimensions. The compiler resolves it before generating bindings and catalogues. |
| **`fl32` / `fl64`** | SDL floating-point types corresponding to binary32 and binary64. Encoding can round values, and optional wire quantization can introduce further loss of precision. |
| **`c32` / `c64`** | SDL complex types with real and imaginary components using the corresponding floating-point precision. |
| **Quantization / quantum** | A lossy wire representation of a floating-point value as a signed integer multiplied by a quantum `q`. The console's validation preview shows the value obtained after encoding and decoding. |
| **Presentation metadata** | Console-side labels and validation constraints associated with schema fields. This metadata is separate from the SDL wire catalogue. |

References: [schema language](sdl/docs/schema.md),
[source formats](sdl/docs/schema_formats.md),
[console configuration](ose-ws/docs/configuration.md).

## Catalogues, codecs and wire formats

| Term | Meaning |
| --- | --- |
| **Catalogue** | The compact canonical UTF-8 runtime description beginning with `SDL2\|`. It describes message and enum declarations independently of generated classes and payload bytes. |
| **Canonical representation** | The specified catalogue rendering with fixed ordering and no whitespace or trailing newline. Identity calculations use these exact bytes. |
| **Catalogue ID / catalogue hash** | A 32-bit value derived from the first four bytes of SHA-256 over the exact catalogue bytes. Collisions are possible; full catalogue text is needed to establish equality. |
| **Message type ID** | A catalogue-local number selecting the top-level message in a payload. It derives from message declaration ordering and can change when the catalogue changes. It differs from a field ID or catalogue ID. |
| **Binding / generated code** | Language-specific types and codec integration emitted from a schema. A parameter binding in OSE has a different meaning, defined below. |
| **Codec** | An encoder/decoder translating logical values to binary bytes and back according to a catalogue. |
| **Prepared context** | Runtime state created by `prepare` from a catalogue and, for typed decoding, local types. It can be reused across multiple messages. |
| **Typed decoding** | Decoding into generated application classes or records. |
| **Dynamic decoding** | Decoding using the recorded or received catalogue without requiring generated application classes. Used by inspection tools and generic services. |
| **Directional compatibility** | Whether a consumer can read a publisher's schema for selected roots. The check is publisher-to-consumer; reversing the direction can change the result. |
| **Schema evolution** | Changes to message declarations across versions. Compatibility depends on logical names, field IDs, types, cardinality and the selected required-field policy. |
| **Wire representation** | The exact binary layout used for transmission or recording, independent of native language memory layout. `wire(...)` clauses can customize it. |
| **Payload** | The encoded SDL2 message bytes, including the root message type ID. Transport metadata, envelopes and recording container headers are separate layers. |
| **Big endian** | Byte order in which the most significant byte comes first; used by ordinary SDL multibyte numeric encodings. Custom encodings follow their own rules. |
| **Varint / ULEB128** | A variable-length unsigned integer encoding using seven value bits per byte. SDL's `encoding=varint` requires nonnegative values. |
| **ZigZag** | A mapping from signed integers to unsigned values, then ULEB128, allowing small negative and positive values to have short encodings. |
| **Alignment / padding** | Explicit wire settings that insert zero bits or bytes to align a value or pad after it. They are specified by the catalogue rather than a compiler's native struct layout. |
| **S2PS/2 envelope** | SDL's optional transport-independent wrapper around an SDL2 payload, carrying a version and catalogue ID. OSE uses it at the network boundary. |
| **CENV/2** | OSE's network packet format carrying JSON metadata and a binary body. A DATA body contains an S2PS/2 envelope; optional application IDs remain separate from transport sequencing and deduplication. All peers must use the matching protocol version. |
| **CH/2** | The binary local protocol between the execution engine and its component, carried over a private Unix socketpair. It carries single-input activations, message references, Pull requests, publications, completion and shared services. Standard input/output/error remain available to application code. |
| **TCP / UDP** | Transmission Control Protocol provides an ordered byte stream; User Datagram Protocol sends individual datagrams. OSE supports both in local delivery and uses TCP for distributed routes. OSE admission, ACK and retry policies add their own application semantics. |
| **Frame / datagram** | A delimited transport unit. TCP requires framing over its byte stream; UDP provides datagram boundaries. OSE applies size limits to complete packets. |

References: [wire format](sdl/docs/wire_descriptor.md),
[runtime APIs](sdl/docs/runtime.md), [envelope](sdl/docs/pubsub_envelope.md),
[OSE transport](ose/docs/delivery.md), [component protocol](ose/sdk/input-protocol.md),
[component transport](ose/docs/component-transport.md).

## Applications and execution

| Term | Meaning |
| --- | --- |
| **Application / component graph** | A set of modeled components and their typed connections, together with execution configuration. Data connections can contain feedback through passive Pull inputs. Activating cycles require simulated execution with `--microsteps`. |
| **Model** | The version 2 JSON document describing components, message/timer inputs, outputs, per-event output permissions, subscriptions and shared parameters independently of executable placement. Deployment and scenario versions are independent. |
| **Deployment** | The document associating model components with commands or generated containers, environment settings, transports and placement. |
| **Resolved plan** | The compiled execution description, usually `build/plan.json`. It contains resolved contracts, policies and absolute paths and is tied to the matching build/runtime. |
| **Component** | A logical application node running business code, such as acquisition, analysis or alarm handling. Its name identifies it in the graph and console. |
| **Adapter** | The OSE process managing one component's network delivery, queues, scheduling and local IPC. In real-time execution it is separate from the component executable. |
| **Broker** | The routing service managing publications and independent per-subscription delivery queues. Local execution hosts it alongside supervision; simulation uses centrally managed logical admissions rather than network delivery queues. |
| **Supervisor** | The local process that starts adapters, coordinates readiness and shuts down the application on interruption or process failure. |
| **Coordinator** | The distributed service managing graph activation, worker registration, generations and admission authorization. Broker-routed data also passes through it. |
| **Worker** | In distributed execution, one adapter/component pair registered for a logical component. In simulation, `--workers` instead limits concurrent component calculations. |
| **Container** | In an OSE deployment's `container` entry, a generated executable wrapper around typed callbacks. In image/Kubernetes documentation, it means an operating-system container. |
| **Handler / callback** | Application code called by a generated wrapper: lifecycle hooks `on_start`/`on_stop` and `on_<input>` callbacks receiving one activating input reference. Push messages, Push inactivity timeouts and timers can activate callbacks; Pull inputs have none. |
| **READY barrier** | Startup synchronization: enabled components initialize and report READY before the application is activated. An input can still be empty when its component reports READY; initially disabled components defer handler startup. |
| **Port** | A declared typed component input or output. Its contract identifies a root type and schema. |
| **Endpoint** | A graph endpoint such as `sensor_1.signal`, or, in connection settings, a host/port address described by `endpoint.json`. The context determines which meaning applies. |
| **Topic** | A named publication stream. Each modeled output `component.port` defines a reference contract but can also accept compatible independent producers. `external_topics` declares contracts not supplied by modeled outputs. |
| **Contract** | The SDL root and catalogue defining the data accepted or produced at an interface. Connected ports undergo directional compatibility checking. |
| **Publication** | One output message emitted by a component or supported external publisher. Fan-out can produce several independent delivery attempts from it. |
| **Subscription** | In the OSE model, a connection from one output or external topic to one input, with its own QoS policy. A console WebSocket subscription instead selects UI notifications. |
| **Activation / tick** | One selected message or timeout event invoking one component callback. Some counters and CLI options retain the word tick; model v2 has no joint input tick or `on_tick` callback. |
| **Push / Pull input** | Push invokes its callback for a selected message or inactivity timeout. Pull is passive and can be read during another input's callback. This choice is independent of FIFO/latest reception policy. |
| **Timer input** | An input without a message contract, configured as periodic or one-shot with a positive delay. It activates a timeout callback; arm/disarm controls its deadline. Automatic timers first fire after their delay, and periodic expirations preserve cadence and may coalesce. |
| **Input reference** | The activating origin or a value read from a Pull inbox, with message, source topic, engine handle and optional application ID. Timeout references have no message. The activating reference also exposes event timing. |
| **Eligible inbox view** | Pull handles visible when a callback is selected. Later arrivals become accessible at the next activation; existing entries remain subject to overflow eviction. This is not a combined snapshot callback. |
| **Get / pull / remove** | Nonblocking Pull operations: get reads without consumption, pull reads and journals consumption, remove journals consumption without returning the value. Optional ID, handle and source selectors restrict the match. |
| **Withdrawal journal / staged publication** | Consumption is hidden from subsequent reads within a callback and commits on successful FINISHED, releasing capacity before outbound admissions. Failure cancels the journal and staged outputs. Parameters, logs, measurements and external side effects have separate semantics. |
| **Input timeout** | An inactivity deadline on a Push message input, reset by valid receipts. Expiry invokes the same input callback with no message and its timeout event's output permissions. Pull inputs have no activating timeout. |
| **Application ID / engine handle** | An optional uint64 business correlation/selection value and the engine's technical publication identity. An absent application ID differs from zero; duplicates are allowed and do not replace transport retry/deduplication identity. |
| **Activation graph / output permission** | The graph connecting input events to allowed outputs and subscriptions to activating destination inputs. `activates.message` and `activates.timeout` separately authorize publication. Passive Pull feedback does not itself create an activating cycle. |
| **Trigger / required input / joint snapshot (legacy)** | Model v1 concepts replaced by independent input callbacks, timer inputs and explicit business coordination. Runnable plans and generated containers require v2; SDL required/optional field cardinality still applies. |
| **FINISHED / completion** | Successful callback completion in CH/2, committing input withdrawals and releasing staged publications. It is separate from a transport receipt acknowledgement and carries no business result. |
| **IPC — Interprocess communication** | Communication between processes. OSE's component/engine IPC uses CH/2 over an inherited private Unix socketpair. |
| **Component supervision** | Enable/disable control with desired, acknowledged and pending state. Disabling waits for callback/output admissions, clears pending inputs and freezes timers; enabling resumes retained state. Initially inactive components have no handler process until enabled. |
| **Independent producer** | A compatible publisher on a modeled output topic, with its own generation and transport sequence. Replay can replace a disabled component's output without changing downstream subscriptions; it does not automatically disable the original producer. |
| **Direct / broker route** | Distributed routing choices: payloads travel between workers directly, or through the coordinator's broker path. Direct admission still involves coordinator authorization. |

References: [architecture](ose/docs/architecture.md),
[execution](ose/docs/runtime.md), [callbacks](ose/docs/callbacks.md),
[distributed execution](ose/docs/distributed.md),
[model v2 migration](ose/docs/model-v2-migration.md),
[component supervision](ose/docs/component-supervision.md).

## Model authoring and source editing

| Term | Meaning |
| --- | --- |
| **Editor project / application root** | The application source directory opened by `ose-editor --root`, containing the selected model and registered deployment/schema documents. The development shell shortcut defaults to `OSE_APP_ROOT`. |
| **Source document / source scope** | An editable file and the filesystem roots allowed for its discovery and access. Additional schema/deployment roots are explicitly registered with `--source-root`. Source declarations retain information that a resolved runtime catalogue does not contain. |
| **Workspace metadata** | Editor layout, groups, views and deployment registration stored in `.ose-editor/workspace.json`. These settings accompany the application sources without defining runtime component placement or delivery. |
| **Deployment profile** | An execution configuration selected for an application, such as local or cluster placement, and in the editor a registered deployment document selected for editing or validation. Validation results belong to the selected deployment and draft revision; a profile does not change the model's business graph. |
| **Graph / Tables / Sources** | Editor views of the shared project draft: component connections, structured configuration and editable source text. The editor graph derives from authored sources; the Web Console topology derives from a compiled execution plan. |
| **Inspector** | The editor panel exposing properties of the selected component, connection, JSON object or SDL declaration. |
| **Component editing / activation arrows** | The editor's component view for creating and configuring Push, Pull, timer and output ports. Optional internal arrows and the activation table expose per-event output permissions; subscription arrows connect components. |
| **Symbol navigation / guided diagnostic** | Navigation to an indexed component, port or source declaration, and source-located diagnostics offering relevant navigation or supported correction previews. Corrections remain revision-checked, previewed and undoable. |
| **Authoring draft / unapplied source draft** | The authoring draft is the backend's current editable project state. Unapplied text in the Sources view remains browser-local until Preview source changes and Apply changes update that state. Neither action saves the maintained source files. |
| **Project revision** | An opaque identifier for an editor state. Commands and validation jobs carry their base revision; mutations with an outdated revision are rejected. This identity is separate from Git commits and SDL catalogue IDs. |
| **Structured projection / stale projection** | The parsed objects used to display source documents in structured views. Syntax errors leave the text editable and mark the structured view stale; that view cannot authorize further structured edits until the source is repaired. |
| **Source-preserving edit** | A targeted source change that retains unaffected authored text, including formatting, comments and explicit literals where supported. Resolved catalogues and generated bindings are inspection/build outputs, not replacements for maintained sources. |
| **Prepared command / change preview** | A proposed editor operation bound to a project revision, with affected-file diffs and available impact information. Apply changes accepts the proposal into the authoring draft; it does not write files or modify a running application. |
| **Refactoring / Rename references** | A coordinated change to a name and its supported indexed uses, such as component references across the model and registered deployments. Handler code is listed for review rather than automatically rewritten; supported source formats and dependency cases limit coverage. |
| **Undo / redo** | Reversing or restoring an applied project command in the shared authoring draft. A compound command is one history step; unapplied source text has its own editor history. |
| **Validation snapshot / validation job** | An immutable copy of a draft revision checked with the matching public OSE/SDL tools in temporary directories. Validation can inspect unsaved sources, contracts and typed initial values; it does not compile or execute application handlers. Results from older revisions are stale. |
| **Diagnostic** | A reported source or validation problem, with its origin and available file/location details. Successful model validation does not establish application business behavior; compilation, execution and application tests are separate steps. |
| **Initial-value codec preview** | The value obtained after encoding and decoding a modeled initial parameter through SDL. It exposes representability, rounding and quantization before execution; it does not update a live parameter block. |
| **Save / Save draft** | Save writes the shared authoring draft to maintained files after checking known errors and external changes. Save draft explicitly permits known errors. Saving alone does not establish validation success or reload a running application. |
| **Disk baseline / save conflict** | The file contents recorded by the editor and a mismatch detected when those files change externally. A conflict exposes baseline, draft and current disk contents for review instead of silently overwriting the external edit. |
| **Save journal / draft recovery** | The save journal supports restoring or completing an interrupted multi-file save. Separate bounded server-side draft recovery can restore applied but unsaved edits after reopening; unapplied browser-local text is outside that recovery. |
| **Language coverage inventory** | The list of OSE model, deployment, SDL source, source-format and runtime-inspection constructs mapped to editing surfaces and qualification evidence. An inventory entry or specification requirement is not proof of complete implementation; the coverage map and validation record identify remaining limits. |

References: [editor workflow](ose-editor/README.md#editing-workflow),
[editor API](ose-editor/docs/api.md),
[language coverage](ose-editor/docs/language-coverage.md),
[implementation and qualification](ose-editor/docs/validation.md),
[application commands](docs/application-environment.md).

## Delivery and bounded state

| Term | Meaning |
| --- | --- |
| **QoS — Quality of service** | Rules for buffering, capacity, overflow, acknowledgements and retries. Subscription delivery state is distinct from destination reception; multiple producers feeding one input share its reception buffer. Transport is selected in deployment configuration. |
| **FIFO — First in, first out** | A bounded queue preserving admission order. A Push activation selects one origin; Pull operations explicitly select eligible entries. Successful consumption releases capacity. |
| **Latest** | A capacity-one policy retaining the newest state. Replacing an older value is expected behavior; intermediate publications can be skipped. |
| **Inbox / outbox** | The consumer's input buffer and the sender's outgoing delivery queue. Several subscriptions can feed one shared inbox while retaining independent delivery state. |
| **Capacity / depth / occupancy** | The configured queue limit, current entry count and amount of that limit in use. Reserved in-flight FIFO entries affect available capacity. |
| **Admission** | Acceptance into a bounded RAM queue after applicable validation. Acceptance does not mean the application has processed the message. |
| **Backpressure** | Slower progress or refusal propagated when a downstream receiver cannot accept more data. OSE handles subscriptions independently. |
| **Overflow policy** | Behavior when a buffer is full: `reject_new` refuses the arriving entry, `drop_oldest` removes an eligible waiting entry, and `wait` waits for bounded admission time. |
| **ACK / receipt acknowledgement** | Confirmation, when configured, that a message was validated and admitted into receiver RAM. It does not confirm business completion or persistence across a crash. |
| **Retry / backoff** | Another delivery attempt after an acknowledgement timeout, subject to a retry budget. Backoff increases the time between attempts. |
| **Deduplication** | Detection of repeated message IDs within a bounded in-memory window. Retries retain their original ID; this mechanism provides no unlimited exactly-once guarantee. |
| **Fan-out** | Delivering one publication independently to multiple subscriptions. |
| **Volatile state** | RAM-only state lost on restart, including OSE delivery queues, retained latest values and deduplication history. Console metadata and recording files have separate storage rules. |
| **Epoch / generation** | An epoch identifies a broker/coordinator run; a generation identifies one component incarnation or external publisher session. Replacements or restarts change the relevant identity. |
| **Lease / fencing** | A lease is a periodically renewed validity period, such as a worker registration or console measurement/Live watch. Distributed fencing rejects traffic belonging to retired generations. |

References: [delivery policies](ose/docs/delivery.md),
[distributed lifecycle](ose/docs/distributed.md),
[console watches](ose-ws/docs/configuration.md).

## Time, diagnostics and performance

| Term | Meaning |
| --- | --- |
| **Real-time execution** | OSE execution paced by elapsed host time and external arrivals. The Python runtime provides no hard real-time deadline guarantee. |
| **Virtual time / simulation** | A centrally controlled application clock advanced without waiting for the corresponding wall time. Simulation runs actual component processes with deterministic scheduling rules. |
| **Simulation server / session** | A loopback RPC listener hosted by CLI simulated runs, publishing a simulated endpoint for terminal and web observation/control. Interactive sessions start paused and retain the final snapshot after completion; ordinary batch runs close the listener when finished. |
| **Horizon / quiescence** | The exclusive simulated duration bound, and early termination when no immediate work or future event remains. Events exactly at the horizon do not run. Simulated replay may omit a horizon and continue until quiescence or an explicit stop. |
| **Microstep / activating feedback** | A logical stage at one virtual timestamp, enabled with `--microsteps`. Stage outputs become visible at the next boundary without adding physical delay. `--max-microsteps` bounds stages per timestamp; exceeding it fails execution rather than dropping messages or advancing time. |
| **Step / run until / pause** | Shared simulation controls for advancing event instants, a relative duration, an absolute time or microsteps, and pausing after the current wave. Only one advance/reset can be active. A callback cannot be paused safely in its middle. |
| **Temporal breakpoint / persistent breakpoint** | An absolute simulated time at which progression pauses before input injection and callbacks. A normal breakpoint fires once; a persistent one re-arms after reset for the same server process. |
| **Simulation reset / execution epoch** | Reset recreates components, initial parameters and virtual time with a new epoch. Old-epoch commands are refused; ordinary breakpoints and session logs clear. Reset is neither checkpoint restore nor reverse execution. |
| **Simulation workers** | The maximum concurrent callback calculations, independent of component process count. A paused server can change this limit while preserving time, state and epoch; the setting survives reset in that process. |
| **Scenario** | In OSE simulation, scheduled external inputs. An application can also use this word for a business profile, such as Vibration Watch's increasing-vibration signal generator. |
| **Wave / publication barrier** | In simulation, a deterministically selected set of eligible component callbacks and the boundary after which their staged publications become visible in stable component/emission order. Connected components can share a wave but cannot see each other's staged outputs within it. |
| **Logical trace** | A simulation record of logical events and hashes used to compare executions. It is separate from a message-payload recording. |
| **Wall-clock / monotonic time** | Wall-clock time expresses calendar timestamps and can be adjusted; monotonic time measures elapsed intervals without clock-setting jumps. OSE uses monotonic timing for scheduling and retry deadlines. |
| **Metric / counter** | Runtime diagnostic data such as queue depth, accepted messages, retries or completed ticks. A counter accumulates events; a rate derives changes over an interval. |
| **Measurement / measure** | An application-declared named finite `fl64` scalar, such as `analyzer_1.vibration.rms`. OSE retains only its latest value; the console can collect a bounded history of observations. |
| **Observation / sample / series** | An observation is a reader's view at a given time. A console measurement sample is an observed latest value, and a series is its history. It can omit intermediate component emissions. A signal sample inside an SDL message is a separate application value. |
| **Generation segment** | The portion of a console measurement series associated with one component generation and application epoch. Replacement or simulation reset starts a separate segment; real-time and simulated time axes are not combined. |
| **Rolling window / rate** | A recent moving interval and the count change divided by its actual elapsed span, such as publications per second. |
| **Throughput / latency** | Throughput is work or data processed per unit of time; latency is elapsed delay between specified events. Publication rate, input admission delay and callback duration measure different parts of execution. |
| **MB / MiB** | A megabyte is 1,000,000 bytes; a mebibyte is 1,048,576 bytes. Console capture sizes use decimal KB/MB/GB, while some configured memory budgets use MiB. |
| **Callback / tick p95** | The 95th percentile of recent completed callback durations, including component execution and IPC but excluding subsequent outbound publication/admission waits. It measures elapsed time, not CPU time. Some reports retain the tick label. |
| **Busy duration** | Elapsed time for an activation currently in progress, including delivery waits. It is separate from completed-callback p95. |
| **Timer lag / coalesced expiration** | Delay from a timer's scheduled deadline to callback selection, and extra elapsed periodic expirations grouped into one callback. Timer diagnostics are per input; Push inactivity timeouts are separate. |
| **Input wait / publication wait** | Input wait measures local admission to Push selection or first successful Pull read in a callback. Publication wait measures an outbound call's transport/admission delay after calculation, excluding later asynchronous ACK waits and downstream processing. |
| **RSS / process CPU** | Resident memory and CPU observed for a component or its runtime process. CPU is measured over a report interval, with 100% representing one core; unavailable readings differ from zero. RSS can count shared pages in several processes. |
| **CPU time / average CPU usage** | CPU time is execution time charged by the operating system to a process. Average CPU usage is CPU time divided by elapsed observation time; under the one-logical-CPU convention, 100% means one CPU fully occupied. |
| **Executable / process / thread** | An executable is a program; a process is a running instance; a thread is an execution path within that process. A Python process can spend CPU time inside native libraries, so process CPU alone does not isolate interpreter cost. |
| **Log / level / logger** | A structured text event, its severity and optional category. OSE retains bounded recent logs in RAM. `FATAL` is a distinct level and does not itself terminate the component. |
| **Freshness / stale** | Whether runtime reports are recent enough to trust as current. Live diagnostics mark running component reports older than three seconds as stale. A paused simulation's logical snapshot does not become stale merely because virtual time is stopped; connection freshness still uses real time. |

References: [simulation](ose/docs/simulation.md),
[simulation server](ose/docs/simulation-server.md),
[monitoring](ose/docs/monitoring.md), [measurements](ose/docs/measurements.md),
[logs](ose/docs/logs.md), [console collection](ose-ws/docs/configuration.md).

## Application flow and Live values

| Term | Meaning |
| --- | --- |
| **Application flow / topology** | The console graph of components, external topics and declared subscriptions, derived from OSE's compiled plan. Arrows represent declared connections. |
| **Link / source contract** | A link represents one subscription. Its panel shows ports, delivery policy and source SDL schema. Its diagnostic checks source-to-destination compatibility and explains field differences by numeric ID. |
| **Live values** | A read-only preview of the latest observed source publication, with bounded values and no file or history. It may skip publications and does not confirm destination delivery. |
| **Link health / delivery admission rate** | Console observations of delivery queue occupancy, admissions, refusals and loss events. Destination input occupancy is separate and can be shared across producers. Refusals count attempts, losses count queue events, and admission does not establish callback processing. Missing/stale telemetry is unavailable. |
| **Metric overlay / component history** | A selected metric on graph cards and optional browser-held sparklines/state transitions. History is bounded to 120 reports over two minutes, collected while Components is active, and resets on epoch/generation changes or reload. Each component has its own vertical scale. |
| **Frozen view / simulation pause** | Freezing holds displayed console observations and browser component history while collection continues. Simulation pause stops logical progression at a wave boundary; it is a separate control. Measurements in simulated sessions use virtual time, while resources and connection freshness use real time. |

References: [Web Console](ose-ws/README.md), [monitoring](ose/docs/monitoring.md).

## Parameters and editing

| Term | Meaning |
| --- | --- |
| **Shared parameter block** | A named typed SDL settings message stored by OSE and shared with authorized components or clients. Writes replace the complete block. |
| **Parameter path / alias / binding** | The path identifies a shared block, such as `machines/1/sensor`. A component-local alias names its binding to that block, with declared read/write access. |
| **Parameter client** | A declared technical identity with permissions to read or replace selected parameter blocks. The console uses its configured OSE identity independently of browser-user identities. |
| **Draft / remote value** | In the parameter editor, a draft is the browser's editable value; the remote value is the latest value read from OSE. An external change can update the remote value while preserving the draft. |
| **Validation preview** | The normalized result of validating and round-tripping a draft through the SDL codec, including encoded bytes and hashes needed to apply it. It exposes floating-point rounding and wire quantization. |
| **Schema digest / preview hash** | A schema digest fingerprints a console contract, including the contract attached to a Live sample. Parameter validation/apply also uses a preview hash binding the previewed payload and constraints. These differ from SDL's 32-bit catalogue ID and do not test whether the remote parameter value has changed. |
| **Last-write-wins** | The last complete block write processed by OSE becomes current. Parameter editing has no automatic merge, lock or compare-and-swap against an old value. |
| **CAS — Compare-and-swap** | A conditional write that succeeds only if an expected old value or revision still matches. OSE parameter writes do not provide this mechanism. Console capture draft revisions and model editor project revision checks apply to their respective drafts, not live parameter writes. |
| **Import / export** | Transfer of parameter values as JSON. Path-keyed exports map each block path to its complete value. Multi-block imports validate first but apply sequentially without an atomic transaction across blocks. |
| **Directory synchronization / managed parameter sync** | Optional bidirectional synchronization between parameter JSON files and OSE blocks, using the parameter service and a filesystem watcher. Application commands can manage its lifecycle and resolve the configured parameter-client credentials. |
| **JSON Pointer / glob / regular expression** | Different selection syntaxes. JSON Pointer addresses fields in console presentation metadata; globs select names in tools such as measurement/recording discovery; application parameter exports use regular expressions unless `--exact` is supplied. |

References: [shared parameters](ose/docs/parameters.md),
[console editing](ose-ws/README.md), [validation API](ose-ws/docs/api.md).

## Recording, filtering and replay

| Term | Meaning |
| --- | --- |
| **Recording point** | A discoverable source of typed messages: an output/external topic, observed component message input or component-declared internal diagnostic point. Natural input/output names may coincide; point kind and channel metadata distinguish them. |
| **Input recording point** | A read-only observation of messages selected at an input, named `component.port` with kind `input`. Several producers share that point. It records observed values rather than every admitted arrival and does not establish callback completion; absent timeout references produce no message sample. |
| **Internal point** | A component-owned diagnostic source, such as `analyzer_1.record.dsp.input`, declared before READY and activated for recording. Its samples do not enter application subscriptions. |
| **Tap** | A bounded observation path exposing selected publications, observed inputs or internal samples without consuming application input queues. Live and recordings share source tap quotas. Dynamic taps and Live previews are not implemented by the simulation server; launch-time batch capture is supported. |
| **Recorder / recording session** | An external recording client and its source-side session state, selection and filters. Multiple recorders can operate independently. |
| **Capture** | A console-managed recording object with a concrete selection, settings, lifecycle and output files. A browser closing does not stop an active capture. |
| **Batch capture** | Recording attached to `ose run` or `ose replay` with repeated `--record 'TOPIC_GLOB[, FILTER]'` clauses, combined with OR semantics per point. It starts with execution/injection and finalizes when the command ends. `--record-file` chooses PCAPNG/MCAP by extension and overwrites an existing output, unlike exclusive file creation in the interactive recorder. |
| **Temporal capture window** | The half-open relative interval `[from, until)` selected with `--record-from`/`--record-until`. Its clock is simulated position, elapsed real-time run position or replay playback position. Windowing neither changes execution nor rebases stored timestamps; absolute replay source-range selection is separate. |
| **Label** | Informational text attached to a recording session or capture. It need not be unique and is separate from its ID, topic names and filesystem paths. |
| **Content filter / predicate** | A typed expression selecting messages based on their values. Object evaluation uses logical values; buffer evaluation uses encoded values after quantization. |
| **Source filtering** | Evaluation in the local broker or distributed producer worker before capture queue admission and network transfer. Each recording session has independent filters. |
| **Match / NoMatch / Indeterminate** | Predicate outcomes: true, false, or unable to decide because of a missing value, out-of-range index or NaN comparison. Filter APIs also report evaluation errors separately. |
| **Recording container / channel** | The file format holding catalogues and messages, and a file-local association between a topic, immutable catalogue and root type. Here “container” means a file structure. |
| **PCAPNG / MCAP** | The two supported recording containers. SDL uses its own profiles to store self-contained catalogues and raw SDL2 payloads. PCAPNG is the default writer format; a filename extension alone does not choose MCAP. |
| **Log time / publish time** | Recorder arrival and source deposit timestamps, respectively. Replay uses publish time, normalizes the first selected timestamp to position zero and retains file order for ties. Real captures use Unix time and assume synchronized source clocks; simulation uses virtual time. Payload timestamps are unchanged. |
| **Rotation / cyclic retention** | Rotation splits a capture into successively numbered files at a configured size; cyclic retention keeps only a configured number of recent files. |
| **Artifact / finalization / integrity validation** | An artifact is a capture output file. Finalization closes the writer and writes required end records. Integrity validation checks file structure and message validity; a file left by an interrupted capture can have unknown integrity. |
| **Replay** | Reading recorded messages for paced live publication (the default) or simulated application input. Without `--map`, matching recorded output/external topic names are selected automatically. Explicit mappings target known application topics; optional application IDs are preserved while technical publication identities are fresh. Compatible-schema mode can explicitly adapt payloads. |
| **Recording transformation** | Offline filtering, concatenation/rebasing or splitting of recording files. The SDL transform tool preserves each input's container format. |

References: [OSE recording](ose/docs/recording.md),
[SDL recordings](sdl/docs/recording.md),
[PCAPNG profile](sdl/docs/recording_pcapng.md),
[filters](sdl/docs/filtering.md), [replay](ose/docs/replay.md),
[transformations](sdl/docs/recording_transform.md).

## Web sessions, operations and storage

| Term | Meaning |
| --- | --- |
| **HTTP / WebSocket** | HTTP serves console resources and commands. The WebSocket connection carries live notifications, measurement points and Live previews; it does not carry mutation commands. “WS” as a project shorthand means the Web Console. |
| **Snapshot / invalidation / resynchronization** | A web snapshot is current resource state. An invalidation asks the browser to fetch it again. A resynchronization request directs the browser to reload available history after a live-stream gap. Simulation exposes the last committed logical snapshot during a wave and a retained final snapshot after an interactive run ends. |
| **Watch** | A renewable console selection owned by a browser session. Measurement watches retain observations for bounded history; Live watches expose only the latest source preview. Several watches can share collection. |
| **Session / token** | Context-specific authentication or connection state. A browser session cookie, local console login token, editor access token, OSE parameter token and recording session token have distinct roles. |
| **Direct local access / `--auth`** | The console and editor open without a browser token by default and accept only loopback hosts. `--auth` enables a local access token, creating it when missing. The console still uses isolated cookie sessions and CSRF protection in direct mode; configured proxy authentication remains active. Browser mode does not change OSE parameter credentials. |
| **Editor access token** | The bearer credential created/reused with `ose-editor --auth` in the application's `.ose-editor/access.token` and entered in the editor login screen. It authorizes editor API requests; existing tokens are ignored without `--auth`. Web Console and OSE runtime credentials are separate. |
| **Actor / permission / scope** | The web identity (including the automatic local administrator), the operation it may perform, and the resources on which it is allowed. Web authorization and OSE client permissions both apply; simulation observation and control can be granted separately. |
| **TLS / mTLS / CA** | TLS encrypts and authenticates connections; mutual TLS authenticates both peers using certificates from a trusted certificate authority. Browser HTTPS and OSE connection credentials are configured separately. |
| **CSRF token** | A token required on console HTTP mutations to protect browser sessions from cross-site request forgery, including automatic sessions in direct local mode. |
| **Idempotency key** | A client-supplied identifier for one explicit command. Reusing the same actor, target, key and body retrieves its recorded operation; it does not create a fresh command. |
| **Operation / audit entry** | An operation records command intent and outcome. Audit entries preserve actor/action history. The console persists this metadata in SQLite independently of OSE's volatile delivery state. |
| **Uncertain / closed unknown** | An uncertain operation has no confirmed outcome, for example after an acknowledgement is lost. Administrative closure records `undetermined` without resending the command or declaring success. |
| **State directory** | Tool-owned storage: the console's `.ose-ws` holds metadata and capture files; the editor's `.ose-editor` holds workspace metadata, writer lock, recovery files and an optional access token. Console runtime state is relative to the launch/working directory, while its token path is relative to configuration. These stores are separate from application builds and volatile OSE queues. |
| **Quota / retention / budget** | Bounds on active objects, stored history, samples, bytes or disk use. Measurement history budgets are separate from capture file retention and do not cap total process memory. |
| **JSONL / CSV** | Export formats: JSON Lines contains one JSON value per line; comma-separated values represent tabular data. Exporting observations does not recover emissions missed between readings. |

References: [console API](ose-ws/docs/api.md),
[configuration and authorization](ose-ws/docs/configuration.md),
[operations and recovery](ose-ws/README.md#operations-and-recovery).
