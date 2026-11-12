# Distributed real-time execution

OSE runs one adapter/component pair per worker, a central coordinator and optional
direct TCP subscriptions. Business callbacks, SDL contracts and input timeouts
use the existing local APIs. Simulation remains local.

## Deployment and build

Add `"execution": "distributed"` to a deployment. Subscription bindings accept
`"routing": "broker"` (default) or `"routing": "direct"`, with `"transport": "tcp"`.
UDP and external client subscriptions remain local-only. The coordinator accepts
publisher-only CENV/2 clients and [live recording replay](replay.md), with a valid
CA-signed identity or explicit loopback `--insecure`. Producers may feed ordinary
component outputs without changing the plan, including direct subscriptions.
The [recorder](recording.md) captures application outputs at the coordinator;
workers forward direct outputs there only while selected for capture. Input and
internal diagnostic recording points remain worker-local.

Components may declare Kubernetes constraints in `placement`:

```json
{"node_selector": {"kubernetes.io/os": "linux"},
 "resources": {"requests": {"cpu": "100m", "memory": "128Mi"}}}
```

`build --component NAME` compiles only that executable while emitting the complete
plan, supporting separate component images. Registration checks a fingerprint of
the application/execution-model versions, contracts, parameters, environment,
triggers and routes. Build images with fixed
internal paths such as `/app`, since commands in resolved plans are absolute.

## Model v2 callbacks and Pull feedback

Workers use the same per-input scheduler and CH/2 services as local execution.
Each Push message or timeout activates one callback with one input reference.
Pull inboxes never activate or gate another input. FIFO/latest reception,
`get`/`pull`/`remove` selectors, timer controls and event-specific output permissions
have the same semantics. Data cycles through passive Pull inputs are accepted;
the event-to-output activation graph must remain acyclic at execution.
Models containing activation cycles can be compiled but require simulated
[`--microsteps`](simulation.md#microsteps-and-activating-feedback) to run.

Optional application IDs travel unchanged over broker and direct routes, including
zero and the full uint64 range. Absence remains distinct from zero. Retry,
deduplication and generation checks use engine handles, independently of these IDs.
Dynamic SDL contract exchange and S2PS/2 message bodies are unchanged.

Workers initialize timers at logical time zero. Their real-time clocks advance
after the initial READY barrier enables execution; arming in `on_start` does not
count initialization or barrier wait time. Each worker has its own monotonic
clock, with no globally synchronized timer phase. Cold replacements start fresh.

Withdrawals are hidden within the callback and committed on successful CH/2
completion; failure cancels them and discards staged publications. Capacity is
released before outbound admission waits. This is a callback-local journal,
not an atomic transaction across network deliveries, parameters or external effects.
Message arrivals during a callback become accessible at the next activation.

The [feedback example](../examples/feedback-pull-v2/README.md) includes a distributed
deployment. `make test-distributed-inputs-v2` exercises real Python and C processes,
broker/direct routes, mutual TLS, retries and generation fencing on loopback.
Rust, Java and Ada use the same worker path and are qualified separately through
local CH/2 tests (GNAT 12 for Ada). Their native distributed worker combinations
have not been separately exercised by this qualification.

## Independent publishers

Producer declarations register sessions and compatible schemas, rather than
exclusive ownership. Model outputs and `external_topics` are both valid targets.
Multiple producers can coexist with the model component; disabling the component
is a separate [supervision operation](component-supervision.md).

The publisher-only SDK operations are `client_hello`, `declare_publisher`,
`publish`, `release_publisher`, `list_topics` and `catalogue`. External subscriptions
are not implemented by the coordinator. Valid TLS client identities are accepted;
worker registration still requires the corresponding component identity.

Replay also uses the compatibility operations below:

| Operation | Request | Result |
| --- | --- | --- |
| `external_open` | `topics`: known application topics; `plan_id`: application fingerprint | Session `token` and coordinator `epoch` |
| `external_publish` | Session `token`, declared `topic`, S2PS/2 binary body | Per-subscription admission results |
| `external_close` | Session `token` | Releases this producer's registrations |

The coordinator checks actual SDL schemas and assigns transport sequences.
Workers validate the actual source catalogue against their input contracts and
check session generations. Closing or reopening a producer does not clear another
producer's deliveries; already accepted records may drain after disconnect.
Retired external generation history is bounded to 4096 sessions.
`broker.external.enabled`, `max_clients`, topic/catalogue limits and normal queue
policies continue to apply. Producers do not join the READY barrier.

## Managed local development cluster

For loopback development, OSE can supervise the coordinator and all workers:

```sh
ose distributed-local --plan build/plan.json --duration 30
ose distributed-local --params-sync parameters --params-client operator
ose app distributed --duration 30 PROFILE=cluster
```

The low-level command uses an already compiled distributed plan. `ose app
distributed` first invokes the application's `build` Makefile target (or
`--build-target TARGET`), forwarding its `NAME=VALUE` assignments. The example
assignment `PROFILE=cluster` is an application convention, not an OSE requirement.
The application must produce a distributed plan; local plans are rejected.
Application-specific demonstrations remain available through `ose app make`.

The coordinator binds an automatically assigned port (`--port` overrides it).
Workers come from the compiled plan, and execution is supervised after the
coordinator reports the graph enabled. The duration defaults to 300 seconds
after readiness. Any child exit fails the launch; duration, Ctrl+C and SIGTERM
stop and reap all children. Workers are not automatically replaced by this
launcher. An application exercise can use the internal `LocalCluster.restart`
operation for deliberate replacement.

This command uses insecure transport on loopback only. For remote or production
execution, use the authenticated `serve`/`worker` commands below.

Logs are reset for the current processes under `BUILD/distributed-logs`, or
`--logs DIR`. `BUILD` is the plan's build directory. The launcher publishes its
actual port in `BUILD/distributed-endpoint.json`; use that port explicitly for
`cluster-status --insecure --port PORT`. Successful completion writes
`distributed-status.json` and `distributed-processes.json` in `BUILD`.
With synchronization enabled, its messages go to `params-sync.log` and its
failure stops the cluster too. Relative sync and custom log directories follow
the launch directory, including with `ose app --root`.

## Processes and authentication

Build normally, then start a coordinator and one worker per logical component:

```sh
ose serve --plan /app/build/plan.json --host 0.0.0.0 --port 7000 \
  --tls-ca ca.crt --tls-cert coordinator.crt --tls-key coordinator.key
ose worker --plan /app/build/plan.json --node source \
  --host coordinator.example --port 7000 --listen-host 0.0.0.0 \
  --listen-port 7001 --advertise-host source.example \
  --tls-ca ca.crt --tls-cert source.crt --tls-key source.key
ose cluster-status --host coordinator.example --port 7000 \
  --tls-ca ca.crt --tls-cert observer.crt --tls-key observer.key
```

All connections require mutual TLS. Each certificate has one common name:
`coordinator` for the coordinator, the exact model name for a worker. A certificate
from the same CA may read status. OSE checks certificate identities independently
of DNS, allowing pod addresses to change. Issuance, private-key distribution and
rotation are operator tasks. Certificates need normal CA/key-usage and client/server
authentication extensions required by the host's TLS implementation.

`--insecure` is explicitly limited to loopback listen, advertised and coordinator
addresses. It is for local demonstrations and tests, not cluster manifests.
All advertised worker endpoints must be reachable by their upstream workers.

## Lifecycle and replacement

The coordinator waits for every READY before initial activation. After activation,
surviving components continue according to their inputs and timeouts. Each Push
or timer activates independently; passive Pull inboxes do not gate readiness.
A replacement registers a new generation and starts with empty state.

The default lease is six seconds, renewed every quarter lease. Connection loss or
lease expiry retires that generation and clears affected broker queues. Concurrent
live registrations for one component are refused. Direct senders discard messages
addressed to obsolete target generations. Each recipient checks admission with
the coordinator, including current source and target generations. A disconnected
old sender cannot keep publishing just because its process cannot be terminated.

`coordinator authorization expired` is the worker watchdog's lease timeout;
`component lease expired` is the coordinator rejecting an expired worker
session. Renewal requires a successful heartbeat, including during component
initialization. Both sides measure time with a clock that includes host suspend
on Linux. A blocked or suspended runtime event loop cannot send/process
heartbeats even when the TCP connection remains open. Component calculations
run in child processes; a long callback alone does not block that event loop.

Set the worker lease with `ose serve --lease-seconds SECONDS` (default `6`).
Choose it to exceed expected runtime scheduling pauses and control round trips;
shorter leases trade tolerance for faster failure detection. Expired generations
are fenced, rather than silently revived. This setting is separate from the
Kubernetes leadership lease described below. The
[lease investigation](distributed-lease-investigation.md) records the short-lease
test reproduction and its correction.

Directory changes propagate on heartbeats. During replacement a surviving direct
sender can still address the old recipient generation, and those messages may be
discarded. Workloads requiring a resumed request/response exchange must account
for discovery convergence and implement application-level retries as needed.

The worker CLI cold-reconnects after a transport or process failure. Application
effects outside OSE (devices, files or external services) require their own fencing
and idempotency. There is no exactly-once execution, checkpoint, message journal,
historical replay or bounded failover-time guarantee.

The coordinator is a singleton with volatile state. Its replacement starts a new
epoch; workers cold-reconnect and rebuild the initial readiness barrier. Kubernetes
control-plane HA does not replicate OSE queues or remove that interruption.
Kubernetes exports also fence the coordinator with a dedicated Kubernetes Lease.
Only its service account can read/update that lease. Authorization expires before
the full lease duration; a replacement observes an unchanged API resource version
for the complete duration before taking ownership using a compare-and-swap update.
This avoids comparing different hosts' wall clocks. If the old coordinator loses
API access, it stops authorizing admissions even if some workers can still reach it.
The lease is ten seconds, with an eight-second local authorization budget and
renewals every 2.5 seconds. This assumes normally progressing monotonic clocks.
For manual serve/worker deployments, maintain a single coordinator; enable
`serve --kubernetes-lease NAME --namespace NS` when using Kubernetes failover.

## Delivery and bounds

Broker routes enqueue at the coordinator and are polled on the persistent worker
control connection. Direct routes use bounded sender engines at the source and
send payloads to the destination adapter. Only authorization RPCs, not those payload
bytes, pass through the coordinator. Both paths retain receiver FIFO/latest buffers,
overflow rules, admission ACKs and retry budgets. ACK means admission in RAM, not
business callback completion. A cold replacement does not replay history.

Direct admission requires coordinator connectivity. This favors fencing over
continued admission during partitions. Control load still grows with admission
rate; benchmark before targeting very high message rates. Each subscription has
its own network loop. Application publication admission obeys its bounded wait
policy. TCP frames obey the plan limit and CH/2 IPC remains limited to 1 MiB;
chunk larger application objects into suitably sized SDL messages.

## Kubernetes export

Supply a JSON image map covering `coordinator` and every component. Values must
be pinned registry references: `registry/image@sha256:<64 hex digits>`.

```sh
ose export-kubernetes --plan build/plan.json --images build/images.json \
  --namespace my-app --output build/kubernetes.json
kubectl apply -f build/kubernetes.json
```

The export creates a namespace and one singleton Deployment/Service per component,
plus the coordinator, its Lease and narrowly scoped RBAC. It uses Recreate updates, Linux placement, declared resources
and preferred host spreading. Images must contain `/app/build/plan.json` at runtime.
The coordinator needs OSE and the SDL Python runtime; workers also need their binary.

Create Secrets `ose-coordinator-tls` and `ose-<component DNS name>-tls`, each with
`ca.crt`, `tls.crt`, `tls.key`. Underscores become hyphens in Kubernetes names;
certificate identities retain model names. Collisions and the reserved
`coordinator` and `replay` names are refused. Pass `--image-pull-secret NAME` to the export when using a private
registry; the Secret must exist in the namespace. TCP readiness
probes indicate a listening process; OSE READY/status separately indicate application
readiness. The export neither installs K3s nor publishes images.

Kubernetes controls node failure detection and replacement timing. Provision
compatible spare capacity and registry access or preloaded images. Use simulation
for deterministic business behavior, network tests for transport/fencing, and
cluster tests for image deployment and placement.
The export sets ten-second tolerations after a node becomes NotReady or unreachable;
this is additional to Kubernetes failure-detection and image/startup delays.
