# SDL, OSE and Web Console glossary

This glossary defines the terminology used across the three projects in this
repository. **SDL** describes and encodes data; **OSE** executes component graphs;
the **OSE Web Console** observes and configures running applications. Definitions
describe this implementation. Links in each section lead to the detailed guides.

## Projects and interfaces

| Term | Meaning |
| --- | --- |
| **SDL — Schema Design Language** | The schema language, compiler, generated bindings and runtimes used to describe typed messages and exchange their binary representations. |
| **SDL2** | The runtime catalogue and binary message format implemented by SDL. Source `.sdl` files and runtime SDL2 catalogues have different syntax. |
| **OSE — Orchestration, Simulation, Exécution** | The framework that validates, builds and executes graphs of components using SDL message contracts. The Python package and CLI are named `ose`. |
| **OSE Web Console / WS** | The separate `ose-ws` application: a Python web server and bundled browser interface for components, logs, measurements, recordings and parameters. It connects to an already running OSE application. |
| **CLI — Command-line interface** | A terminal interface such as `ose`, `ose-ws` or `sdl-recording`. The interactive `ose record` console has its own commands. |
| **SDK — Software development kit** | The language APIs and protocol helpers used to implement components or external clients. Generated SDL bindings supply the application message types. |
| **API — Application programming interface** | A defined interface to functionality, such as an SDL codec function, an OSE client method or a Web Console HTTP endpoint. |
| **RPC — Remote procedure call** | A request/reply operation over a connection, such as reading OSE status or replacing a parameter block. |
| **Frontend / backend** | The browser interface and the server-side application, respectively. The console's Vue/TypeScript frontend calls its Python backend, which connects to OSE services. |
| **Runtime** | Code active during execution. Depending on context, this means an SDL language runtime providing codecs or the OSE execution services managing components and delivery. |

References: [repository overview](README.md), [SDL](sdl/README.md),
[OSE](ose/README.md), [Web Console](ose-ws/README.md).

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
| **CENV/1** | OSE's network packet format carrying JSON metadata and a binary body. A DATA body contains an S2PS/2 envelope. |
| **CH/1** | The binary local protocol between an OSE adapter and its component, carried over standard input/output. It includes initialization, inputs, ticks, publications and completion. |
| **TCP / UDP** | Transmission Control Protocol provides an ordered byte stream; User Datagram Protocol sends individual datagrams. OSE supports both in local delivery and uses TCP for distributed routes. OSE admission, ACK and retry policies add their own application semantics. |
| **Frame / datagram** | A delimited transport unit. TCP requires framing over its byte stream; UDP provides datagram boundaries. OSE applies size limits to complete packets. |

References: [wire format](sdl/docs/wire_descriptor.md),
[runtime APIs](sdl/docs/runtime.md), [envelope](sdl/docs/pubsub_envelope.md),
[OSE transport](ose/docs/delivery.md), [component protocol](ose/sdk/protocol.md).

## Applications and execution

| Term | Meaning |
| --- | --- |
| **Application / component graph** | A set of modeled components and their typed connections, together with execution configuration. OSE validates that the graph is acyclic. |
| **Model** | The JSON document describing components, ports, subscriptions, triggers and shared parameters independently of executable placement. |
| **Deployment** | The document associating model components with commands or generated containers, environment settings, transports and placement. |
| **Resolved plan** | The compiled execution description, usually `build/plan.json`. It contains resolved contracts, policies and absolute paths and is tied to the matching build/runtime. |
| **Component** | A logical application node running business code, such as acquisition, analysis or alarm handling. Its name identifies it in the graph and console. |
| **Adapter** | The OSE process managing one component's network delivery, queues, scheduling and local IPC. In real-time execution it is separate from the component executable. |
| **Broker** | The routing service managing publications and independent per-subscription delivery queues. The local supervisor hosts it. |
| **Supervisor** | The local process that starts adapters, coordinates readiness and shuts down the application on interruption or process failure. |
| **Coordinator** | The distributed service managing graph activation, worker registration, generations and admission authorization. Broker-routed data also passes through it. |
| **Worker** | In distributed execution, one adapter/component pair registered for a logical component. In simulation, `--workers` instead limits concurrent component calculations. |
| **Container** | In an OSE deployment's `container` entry, a generated executable wrapper around typed callbacks. In image/Kubernetes documentation, it means an operating-system container. |
| **Handler / callback** | Application code called by a generated wrapper, such as `on_start`, an input or snapshot handler, and `on_stop`. |
| **READY barrier** | Startup synchronization: modeled components initialize and report READY before the application is activated. An input can still be empty when its component reports READY. |
| **Port** | A declared typed component input or output. Its contract identifies a root type and schema. |
| **Endpoint** | A graph endpoint such as `sensor_1.signal`, or, in connection settings, a host/port address described by `endpoint.json`. The context determines which meaning applies. |
| **Topic** | A named publication stream. Each modeled output `component.port` is its own topic; declared external topics allow supported external publications. |
| **Contract** | The SDL root and catalogue defining the data accepted or produced at an interface. Connected ports undergo directional compatibility checking. |
| **Publication** | One output message emitted by a component or supported external publisher. Fan-out can produce several independent delivery attempts from it. |
| **Subscription** | In the OSE model, a connection from one output or external topic to one input, with its own QoS policy. A console WebSocket subscription instead selects UI notifications. |
| **Tick** | One processing cycle of a component: select inputs, invoke component processing and wait for completion. Components have independent ticks. |
| **Trigger** | The rule allowing a tick to start: input-driven `on_change` or `periodic` with a configured period, subject to input readiness. |
| **Input snapshot** | The set of input values chosen for one tick. It combines available FIFO entries and retained latest values; selected FIFO entries remain reserved until completion. |
| **Required / optional input** | An OSE port readiness rule, independent of SDL field cardinality. Required inputs gate ticks until data arrives or their configured timeout expires; optional inputs do not gate readiness. |
| **Input timeout** | A deadline on a required input that allows an empty input to contribute a null value to a tick. It does not create a separate timeout callback. |
| **FINISHED / completion** | The component's protocol notification that a tick has ended. It allows the adapter to consume reserved FIFO entries. Receipt acknowledgements are separate. |
| **IPC — Interprocess communication** | Communication between processes. OSE's component/adapter IPC uses CH/1 over pipes. |
| **Direct / broker route** | Distributed routing choices: payloads travel between workers directly, or through the coordinator's broker path. Direct admission still involves coordinator authorization. |

References: [architecture](ose/docs/architecture.md),
[execution](ose/docs/runtime.md), [callbacks](ose/docs/callbacks.md),
[distributed execution](ose/docs/distributed.md).

## Delivery and bounded state

| Term | Meaning |
| --- | --- |
| **QoS — Quality of service** | Per-subscription rules for buffering, capacity, overflow, acknowledgements and retries. Transport is selected in deployment configuration. |
| **FIFO — First in, first out** | A bounded queue preserving admission order. One available entry per FIFO input is selected for a tick. |
| **Latest** | A capacity-one policy retaining the newest state. Replacing an older value is expected behavior; intermediate publications can be skipped. |
| **Inbox / outbox** | The consumer's input buffer and the sender's outgoing delivery queue. Each subscription has independent delivery state. |
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
| **Scenario** | In OSE simulation, scheduled external inputs. An application can also use this word for a business profile, such as Vibration Watch's increasing-vibration signal generator. |
| **Wave / publication barrier** | In simulation, a deterministically selected set of independent ready components and the boundary after which their buffered publications become visible in a stable order. |
| **Logical trace** | A simulation record of logical events and hashes used to compare executions. It is separate from a message-payload recording. |
| **Wall-clock / monotonic time** | Wall-clock time expresses calendar timestamps and can be adjusted; monotonic time measures elapsed intervals without clock-setting jumps. OSE uses monotonic timing for scheduling and retry deadlines. |
| **Metric / counter** | Runtime diagnostic data such as queue depth, accepted messages, retries or completed ticks. A counter accumulates events; a rate derives changes over an interval. |
| **Measurement / measure** | An application-declared named finite `fl64` scalar, such as `analyzer_1.vibration.rms`. OSE retains only its latest value; the console can collect a bounded history of observations. |
| **Observation / sample / series** | An observation is a reader's view at a given time. A console measurement sample is an observed latest value, and a series is its history. It can omit intermediate component emissions. A signal sample inside an SDL message is a separate application value. |
| **Generation segment** | The portion of a console measurement series associated with one component generation. Replacement starts a separate segment. |
| **Rolling window / rate** | A recent moving interval and the count change divided by its actual elapsed span, such as publications per second. |
| **Throughput / latency** | Throughput is work or data processed per unit of time; latency is elapsed delay between specified events. Publication rate, input admission delay and callback duration measure different parts of execution. |
| **MB / MiB** | A megabyte is 1,000,000 bytes; a mebibyte is 1,048,576 bytes. Console capture sizes use decimal KB/MB/GB, while some configured memory budgets use MiB. |
| **Tick p95** | The 95th percentile of recent completed tick durations. It includes component execution and IPC but excludes adapter publication/admission waiting after protocol processing. It measures elapsed time, not CPU time. |
| **Busy duration** | Elapsed time for a tick currently in progress, including delivery waits. It is separate from completed-tick p95. |
| **CPU time / average CPU usage** | CPU time is execution time charged by the operating system to a process. Average CPU usage is CPU time divided by elapsed observation time; under the one-logical-CPU convention, 100% means one CPU fully occupied. |
| **Executable / process / thread** | An executable is a program; a process is a running instance; a thread is an execution path within that process. A Python process can spend CPU time inside native libraries, so process CPU alone does not isolate interpreter cost. |
| **Log / level / logger** | A structured text event, its severity and optional category. OSE retains bounded recent logs in RAM. `FATAL` is a distinct level and does not itself terminate the component. |
| **Freshness / stale** | Whether runtime reports are recent enough to trust as current. Live diagnostics mark running component reports older than three seconds as stale. |

References: [simulation](ose/docs/simulation.md),
[monitoring](ose/docs/monitoring.md), [measurements](ose/docs/measurements.md),
[logs](ose/docs/logs.md), [console collection](ose-ws/docs/configuration.md).

## Application flow and Live values

| Term | Meaning |
| --- | --- |
| **Application flow / topology** | The console graph of components, external topics and declared subscriptions, derived from OSE's compiled plan. Arrows represent declared connections. |
| **Link / source contract** | A link represents one subscription. Its panel shows the connected ports, delivery policy and source SDL schema, fetched on demand. |
| **Live values** | A read-only preview of the latest observed source publication, with bounded values and no file or history. It may skip publications and does not confirm destination delivery. |

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
| **CAS — Compare-and-swap** | A conditional write that succeeds only if an expected old value or revision still matches. OSE parameter writes do not provide this mechanism; a capture draft's local revision is a separate console feature. |
| **Import / export** | Transfer of parameter values as JSON. Path-keyed exports map each block path to its complete value. Multi-block imports validate first but apply sequentially without an atomic transaction across blocks. |
| **Directory synchronization** | Optional bidirectional synchronization between parameter JSON files and OSE blocks, using the parameter service and a filesystem watcher. |
| **JSON Pointer / glob / regular expression** | Different selection syntaxes. JSON Pointer addresses fields in console presentation metadata; globs select names in tools such as measurement/recording discovery; application parameter exports use regular expressions unless `--exact` is supplied. |

References: [shared parameters](ose/docs/parameters.md),
[console editing](ose-ws/README.md), [validation API](ose-ws/docs/api.md).

## Recording, filtering and replay

| Term | Meaning |
| --- | --- |
| **Recording point** | A discoverable source of typed messages: an output topic or a component-declared internal diagnostic point. |
| **Internal point** | A component-owned diagnostic source, such as `analyzer_1.record.dsp.input`, declared before READY and activated for recording. Its samples do not enter application subscriptions. |
| **Tap** | A bounded observation path exposing selected publications or internal samples to a recorder or Live source observer without consuming application input queues. Live and recordings share the source's tap quotas. |
| **Recorder / recording session** | An external recording client and its source-side session state, selection and filters. Multiple recorders can operate independently. |
| **Capture** | A console-managed recording object with a concrete selection, settings, lifecycle and output files. A browser closing does not stop an active capture. |
| **Label** | Informational text attached to a recording session or capture. It need not be unique and is separate from its ID, topic names and filesystem paths. |
| **Content filter / predicate** | A typed expression selecting messages based on their values. Object evaluation uses logical values; buffer evaluation uses encoded values after quantization. |
| **Source filtering** | Evaluation in the local broker or distributed producer worker before capture queue admission and network transfer. Each recording session has independent filters. |
| **Match / NoMatch / Indeterminate** | Predicate outcomes: true, false, or unable to decide because of a missing value, out-of-range index or NaN comparison. Filter APIs also report evaluation errors separately. |
| **Recording container / channel** | The file format holding catalogues and messages, and a file-local association between a topic, immutable catalogue and root type. Here “container” means a file structure. |
| **PCAPNG / MCAP** | The two supported recording containers. SDL uses its own profiles to store self-contained catalogues and raw SDL2 payloads. PCAPNG is the default writer format; a filename extension alone does not choose MCAP. |
| **Log time / publish time** | Recording timestamps for logging/receipt and publication, respectively. They are metadata distinct from any timestamp carried inside the application message. |
| **Rotation / cyclic retention** | Rotation splits a capture into successively numbered files at a configured size; cyclic retention keeps only a configured number of recent files. |
| **Artifact / finalization / integrity validation** | An artifact is a capture output file. Finalization closes the writer and writes required end records. Integrity validation checks file structure and message validity; a file left by an interrupted capture can have unknown integrity. |
| **Replay** | Reading recorded messages for paced live publication or virtual-time input. OSE maps selected recorded topics to declared external inputs; compatible-schema mode can explicitly adapt payloads. |
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
| **Snapshot / invalidation / resynchronization** | A web snapshot is current resource state. An invalidation asks the browser to fetch it again. A resynchronization request directs the browser to reload available history after a live-stream gap. An OSE tick snapshot is a separate concept. |
| **Watch** | A renewable console selection owned by a browser session. Measurement watches retain observations for bounded history; Live watches expose only the latest source preview. Several watches can share collection. |
| **Session / token** | Context-specific authentication or connection state. A browser session cookie, local console login token, OSE parameter token and recording session token have distinct roles. |
| **Actor / permission / scope** | The authenticated web user, the operation they may perform, and the resources on which it is allowed. Web authorization and OSE client permissions both apply. |
| **TLS / mTLS / CA** | TLS encrypts and authenticates connections; mutual TLS authenticates both peers using certificates from a trusted certificate authority. Browser HTTPS and OSE connection credentials are configured separately. |
| **CSRF token** | A token required on console HTTP mutations to protect authenticated browser sessions from cross-site request forgery. |
| **Idempotency key** | A client-supplied identifier for one explicit command. Reusing the same actor, target, key and body retrieves its recorded operation; it does not create a fresh command. |
| **Operation / audit entry** | An operation records command intent and outcome. Audit entries preserve actor/action history. The console persists this metadata in SQLite independently of OSE's volatile delivery state. |
| **Uncertain / closed unknown** | An uncertain operation has no confirmed outcome, for example after an acknowledgement is lost. Administrative closure records `undetermined` without resending the command or declaring success. |
| **State directory** | Console-owned storage, normally `.ose-ws`, containing metadata and capture files. It is separate from the application's build directory and OSE's in-memory queues. |
| **Quota / retention / budget** | Bounds on active objects, stored history, samples, bytes or disk use. Measurement history budgets are separate from capture file retention and do not cap total process memory. |
| **JSONL / CSV** | Export formats: JSON Lines contains one JSON value per line; comma-separated values represent tabular data. Exporting observations does not recover emissions missed between readings. |

References: [console API](ose-ws/docs/api.md),
[configuration and authorization](ose-ws/docs/configuration.md),
[operations and recovery](ose-ws/README.md#operations-and-recovery).
