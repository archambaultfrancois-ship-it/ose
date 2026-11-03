# Application architecture

```
sensor_1 (C) -> analyzer_1 (Rust) --+
                                  +-> alarm (Python) -> observer (Python)
sensor_2 (C) -> analyzer_2 (Rust) --+
```

Local execution routes all subscriptions through the ordinary OSE broker.
Distributed mixed routing sends the 64 KiB signals directly between adapters;
analysis and alarm events use the distributed coordinator's broker queues.
Business callbacks and generated typed APIs are identical for both paths.

Each source's elapsed OSE time determines the amplitude ramp; sample index and
seed determine the waveform and noise. Rates, sizes and units are explicit in
the source configuration and signal contract. An analyzer computes RMS and peak
without external crates. Alarm classification is pure Python and tested without OSE.

Sensor generation settings and alarm thresholds are typed SDL shared parameter
blocks, separated per machine. Sensors and alarm policy read callback snapshots
through generated C/Python bindings. Configuration files supply build-time initial
values; an external `operator` client can replace complete blocks while running.
Readers apply changes between callbacks; changing a block does not itself trigger
a business tick. The broker/coordinator retains the latest value for replacement
workers, with no persistence across its own restart.

Global startup waits for all six components. After activation, a failed analyzer
can be replaced without resetting sources or the other branch. Signal recording
replay uses a separate model with external sources. There is no checkpoint,
durable alarm log or exactly-once guarantee. Sequence numbers restart
when a source restarts; OSE separately identifies network generations to fence
obsolete senders. Hardware-facing integrations would need device-side fencing too.

The coordinator is currently a singleton. If it fails, workers lose authorization,
stop their children and reconnect with cold state to the replacement coordinator;
the new run uses a fresh startup barrier and network epoch. Cluster control-plane
HA does not make application queues durable or make this interruption disappear.
In Kubernetes it holds a Lease through a dedicated service account. An isolated
old coordinator loses its authorization before a replacement can acquire the lease,
preventing separate network partitions from keeping two active coordinators.

The distributed transport uses TCP and mutual TLS. Direct admission consults the
coordinator before accepting a source generation, so a coordinator partition pauses
new admissions. Large payloads bypass it, but authorization traffic still scales
with admission rate; measure this overhead before targeting very high message rates.

Simulation covers logical behavior and deterministic traces. The loopback network
demo covers actual RPCs and worker replacement. Opt-in cluster tests cover image
deployment and placement. These validation levels establish different properties.
