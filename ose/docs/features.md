# OSE feature overview

## Application models and builds

- **[Component graphs](architecture.md#documents)**: describe typed Push/Pull inputs, timers, activation links, subscriptions and parameters in JSON.
- **[Separate deployments](architecture.md#documents)**: bind the same model to executable commands, generated containers and transport settings.
- **[Contract validation](architecture.md#documents)**: check dynamic SDL publisher-to-consumer compatibility and check activation cycles against the selected execution mode.
- **[Multilanguage components](callbacks.md)**: connect Python, C, Rust, Java and Ada implementations in one application.
- **[Generated containers](callbacks.md)**: generate typed bindings, wrappers and compilation commands while preserving existing user handlers.
- **[Local application environment](../../README.md#local-application-environment)**: access application and recording commands with automatic setup of optional recording services.

## Callbacks and execution

- **[Typed callbacks](callbacks.md#lifecycle-and-execution)**: process one input reference per activation and publish through typed output methods.
- **[Lifecycle hooks](callbacks.md#python-callbacks)**: initialize component resources before READY and release them during shutdown.
- **[Periodic and input-driven execution](runtime.md#selecting-one-activation)**: run independent Push callbacks and explicitly declared timers.
- **[Pull inboxes](runtime.md#pull-access)**: read, pull or remove messages during another input's callback, with deferred consumption and optional ID selectors.
- **[Inactivity timeouts](runtime.md#inactivity-timeouts-and-timers)**: invoke the input callback with an expiration reference after reception inactivity.
- **[Shared parameter blocks](parameters.md)**: bind typed SDL settings to selected components, read or replace whole blocks through five language APIs, and distribute the latest values locally or across workers.
- **[Application clock](callbacks.md#completion-outputs-and-time)**: access callback time and activation indices in real-time execution and simulation.
- **[Process supervision](architecture.md#process-lifecycle)** *(local execution)*: start isolated components behind a READY barrier and shut them down cleanly on interrupts or failure.

## Delivery and quality of service

- **[Per-subscription policies](delivery.md)**: choose bounded FIFO queues or retained latest values independently for each connection.
- **[Backpressure and overflow](delivery.md)**: reject new messages, replace waiting values or wait for capacity with a bounded TCP admission timeout.
- **[Admission acknowledgements](runtime.md#admission-acknowledgement-and-processing-completion)**: confirm receipt into RAM separately from business processing completion.
- **[Retries and duplicate detection](delivery.md)**: configure retry budgets and backoff with a bounded in-memory deduplication window.
- **[Independent subscribers](runtime.md#backpressure-and-ordering)**: let other subscriptions progress while a consumer is slow or full.
- **[TCP and UDP transport](cenv2.md)** *(local broker)*: carry SDL binary envelopes with explicit frame and datagram limits.
- **[Volatile delivery](delivery.md)**: keep queues, latest values and retry state in bounded RAM; restarts begin without message recovery.

## Deterministic simulation

- **[Virtual-time execution](simulation.md#run-a-simulation)**: run the same compiled graph and component processes over a finite virtual horizon.
- **[Parallel calculations](simulation.md#virtual-time-and-waves)**: execute independent components concurrently while preserving logical results across worker counts for deterministic application code.
- **[Scripted external inputs](simulation.md#external-scenarios)**: inject validated SDL envelopes at scheduled virtual timestamps.
- **[Queue and timeout behavior](simulation.md#queues-waits-and-transport-semantics)**: exercise bounded admission and input deadlines without simulating physical network delays or losses.
- **[Logical traces](simulation.md#bounds-failures-and-observations)**: compare timestamped execution traces and hashes without recording message payloads.

## Distributed execution and deployment

- **[Remote workers](distributed.md#deployment-and-build)**: deploy one adapter/component pair per worker using the existing callbacks and SDL contracts.
- **[Direct and broker routes](distributed.md#delivery-and-bounds)**: send TCP payloads through the coordinator or directly between adapters with common FIFO/latest and admission semantics.
- **[Mutual TLS](distributed.md#processes-and-authentication)**: authenticate the coordinator and each logical worker using CA-verified certificate identities.
- **[Cold component replacement](distributed.md#lifecycle-and-replacement)**: restart an affected worker with empty state while surviving components continue according to their input requirements.
- **[Generation fencing](distributed.md#lifecycle-and-replacement)**: retire expired worker generations and reject stale admissions, including direct traffic.
- **[Kubernetes export](distributed.md#kubernetes-export)**: generate singleton Deployments, Services, resource and placement settings, and coordinator Lease/RBAC from digest-pinned images.
- **[Per-component builds](distributed.md#deployment-and-build)**: compile one selected executable while retaining the complete graph plan for separate component images.

## External integration and inspection

- **[Interactive recording](recording.md)**: select live outputs and component-owned internal points by name or message type and record labeled PCAPNG/MCAP sessions without adding a model component.
- **[Internal diagnostic points](recording.md#component-owned-internal-points)**: declare namespaced SDL points in Python, C, Rust, Java or Ada; context-owned hash registries cache encoders and skip serialization while disabled.
- **[Source content filters](recording.md#content-filters)**: filter publications at the broker or producer worker before transfer to the recorder.
- **[File rotation and retention](recording.md#file-rotation-and-cyclic-retention)**: split captures by size and optionally retain only the latest files.
- **[Recording replay](replay.md)**: feed PCAPNG/MCAP topics into simulation or live local/distributed inputs with looping, speed and pause controls, and optional compatible-schema adaptation.
- **[Recording transformations](../../sdl/docs/recording_transform.md)**: filter, concatenate/rebase and split recording series in their original format.
- **[External client SDKs](clients.md)** *(local broker)*: publish and subscribe from Python, C, Rust, Java and Ada without a client-side model.
- **[External input contracts](clients.md#feeding-a-model-input)**: connect declared application inputs to externally published topics and check their SDL compatibility.
- **[Runtime inspection](../README.md#quick-start)**: inspect live local queue, retry, failure and processing counters, or read the final saved status after shutdown.
- **[Live diagnostics](monitoring.md)**: monitor local/distributed rates, queues and tick durations with colored alerts and JSONL output.
- **[Application measurements](measurements.md)**: declare dot-separated fl64 names in all five component SDKs, retain only each current value in RAM, and sample local/distributed values with `ose measure`.
- **[Component logs](logs.md)**: deposit structured logs from all five component SDKs into a bounded in-memory runtime history, follow with `ose logs`, and filter the stream in the web console. Levels include distinct CRITICAL and FATAL entries.
- **[Cluster status](distributed.md#processes-and-authentication)**: query the distributed coordinator for worker and delivery status.
- **[Custom component processes](../sdk/input-protocol.md)**: integrate executables through the CH/2 single-input binary lifecycle protocol.
