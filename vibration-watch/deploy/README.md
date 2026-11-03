# Deploy to K3s

Prepare a Linux cluster with at least three K3s servers for HA, an image registry
reachable from every worker and enough spare resources to move a failed worker's
components. Servers can also host components. Configure registry credentials and
trust on the cluster using your K3s/containerd installation policy. For a private
registry using a Kubernetes pull Secret, create it in the application namespace
and pass `IMAGE_PULL_SECRET=registry-auth` to `make render` or `make deploy`.
It is attached to both coordinator and worker pods.

The LAN must allow Kubernetes pod/service networking and OSE TCP ports 7000 and
7001 between application pods. The component images contain Python and their
language runtimes; application compilers are build-time dependencies.
The coordinator also needs access to the Kubernetes API to renew its leadership
lease. Exported resources include a dedicated service account and a Role limited
to reading/updating that one Lease; workers do not mount Kubernetes API tokens.

On a build machine with Docker:

Use the same CPU architecture as the Linux cluster, or configure an appropriate
cross-platform Docker builder. All machines in this initial deployment are homogeneous.

```sh
make push-images REGISTRY=registry.lan/vibration-watch
make render
make certs
kubectl apply -f build/namespace.json
kubectl apply -f build/tls-secrets.json
kubectl apply -f build/kubernetes.json
kubectl -n vibration-watch get pods -o wide
kubectl -n vibration-watch logs -f deployment/ose-observer
```

`make push-images` explicitly builds and publishes six images and records their
registry digests in `build/images.json`; `make images` only builds them.
The coordinator uses the observer image, since it needs no native component binary.
`make render` resolves the same application configuration and exports Kubernetes
Deployments and Services. All containers use `/app/build/plan.json`, produced at
the same path during image construction. Build and render with the same SCENARIO
and ROUTING settings; changing configuration requires rebuilding the images.

The generated TLS credentials are development credentials, signed by a local CA.
Each identity gets a separate private key; only its leaf credentials and the CA
certificate go into its Kubernetes Secret. Keep `build/pki/ca.key` off the cluster.
For production, supply equivalently named Secrets using your certificate issuer
and rotation procedures. OSE matches exact certificate common names to component
identities, independent of changing pod addresses. The coordinator's identity is
`coordinator`; worker identities are the model names such as `analyzer_1`.
The external parameter client uses a separate `operator` identity, declared in
`deployment.cluster.json`. `make certs` also creates its credentials under
`build/pki/` and a corresponding Secret. Keep its private key with the operator;
application workers do not need it. From a host that can reach the coordinator,
use the parameter commands in the [application guide](../README.md) with
`--distributed`, explicit `--host`/`--port`, the CA and operator leaf credentials.
Runtime changes survive worker replacement; coordinator replacement restores
initial settings. Changed SDL contracts or initial configuration still require
rebuilding the images.

`make deploy` combines namespace creation, credentials and application resources.
It requires an explicitly selected kubectl context. `NAMESPACE` defaults to
`vibration-watch` and can be overridden consistently for render, certs and deploy.

Each component is a singleton Deployment with Recreate updates. Preferred topology
spreading distributes pods across machines while allowing replacement on remaining
nodes. Linux node selectors and CPU/memory requests constrain placement; sufficient
compatible capacity must remain available after failure. No GPU or physical device
is required by this synthetic example.

## Replacement demonstration

On a disposable cluster with at least two schedulable workers:

```sh
VIBRATION_CLUSTER_TESTS=1 python3 -m unittest discover -s tests/cluster -v
```

This opt-in test cordons the analyzer's node, deletes only its pod, checks that a
replacement on a different node receives direct traffic and that survivor pod
identities stay unchanged, then uncordons the node in a finally block. It requires
permissions to cordon nodes and manage application pods. It tests relocation;
power loss and network partitions need separate infrastructure fault injection.

For a real worker outage, K3s handles pod replacement after its node-failure
detection/eviction policy. OSE expires the old generation after its lease and
rejects its publications. Replacement starts with empty state and buffers.
