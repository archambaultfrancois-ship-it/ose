# Prepared SDL validation context: CPU measurements

Measured on 2026-11-03 in Termux, Android 16, ARM64, Python 3.13.13,
with eight logical CPUs and the pure Python SDL runtime. The change caches up
to 64 prepared catalogue contexts per codec instance; it still checks each
envelope, decodes each payload and checks its expected root.

The real Vibration Watch application used **9.8% less aggregate CPU** with the
cache, while processing **1.4% more signal blocks per second**. Aggregate CPU
time per signal block fell **11.1%**. These are measurements of this local
workload, not estimates extrapolated from the validation microbenchmarks.

## Real application protocol

- Same freshly built local Vibration Watch plan and executables for both
  versions, scenario `increasing-vibration`, all five subscriptions over TCP.
- Two C sensors, two Rust analyzers, Python alarm and observer, six Python
  adapters, and one Python process hosting the broker and supervisor: 13
  processes in total. No console or recorder was running.
- Each sensor produces 16,384 samples per block, with a configured period of
  100 ms. OSE schedules that period after completion, so the actual rate is
  below 10 Hz per sensor and can change slightly with processing speed.
- Six independent trials in order: before, after, after, before, before,
  after. Each trial starts fresh, waits for READY, warms up for 5 seconds,
  then measures for 30 seconds. Each version therefore has 90 measured seconds.
  Short pilot runs are excluded.
- Before uses the exact `Runtime.validate` method from commit `d83a966`, loaded
  into each Python process through an inherited, benchmark-only
  `sitecustomize` hook. After uses the working-tree implementation. No source
  swapping is required; marker files verify the mode in all nine Python
  application processes. The unused cache constructor remains present in
  before, and initialization is outside the measurement window.
- CPU is the delta of `utime + stime` in `/proc/PID/stat`, sampled every second,
  divided by elapsed monotonic time. Child CPU is excluded from parent
  counters, avoiding double counting. The kernel reports 100 ticks/second.
  Suspend time is checked separately with `CLOCK_BOOTTIME`.
- **100% means one CPU core.** A sum above 100% is possible. The measurement
  harness and build processes are excluded. Means below pool CPU seconds and
  elapsed seconds across the three trials of each version.

## Mean CPU by process

| Process | Before, CPU % | After, CPU % |
| --- | ---: | ---: |
| Broker + supervisor | 33.83 | 30.66 |
| Adapter `sensor_1` | 8.58 | 8.41 |
| Adapter `sensor_2` | 8.47 | 8.20 |
| Adapter `analyzer_1` | 13.91 | 13.29 |
| Adapter `analyzer_2` | 13.76 | 13.16 |
| Adapter `alarm` | 12.43 | 9.60 |
| Adapter `observer` | 10.11 | 7.24 |
| Component `sensor_1` (C) | 3.75 | 3.46 |
| Component `sensor_2` (C) | 3.84 | 3.38 |
| Component `analyzer_1` (Rust) | 0.67 | 0.69 |
| Component `analyzer_2` (Rust) | 0.67 | 0.67 |
| Component `alarm` (Python) | 1.27 | 1.33 |
| Component `observer` (Python) | 1.54 | 1.63 |
| **Total** | **112.83** | **101.72** |

Rounded process values may not sum exactly to the rounded total. All adapters
together fell from 67.26% to 59.90% CPU. The largest adapter reductions are
`alarm` (22.8%) and `observer` (28.3%); broker CPU fell 9.4%.

| Workload and variability | Before | After |
| --- | ---: | ---: |
| Total CPU, trial 1 | 110.32% | 98.49% |
| Total CPU, trial 2 | 113.52% | 104.05% |
| Total CPU, trial 3 | 114.65% | 102.61% |
| Sample standard deviation of total CPU | 2.25 percentage points | 2.89 percentage points |
| Signal blocks published, both sensors, over 90 s | 1,396 | 1,416 |
| Signal blocks per second, both sensors | 15.51 | 15.73 |
| Observer events per second | 15.53 | 15.73 |
| Total application CPU time per signal block | 72.75 ms | 64.66 ms |

The per-block cost includes all 13 processes, including control activity and
downstream processing. It is not the latency of a sensor tick. Status queries
and observer log offsets bound the measurement window separately, so a small
number of in-flight events can cross its edges.

Broker and UDP error counters stayed empty/zero; subscription counters showed
no dropped, refused, failed or retried deliveries. Observer sequences were
contiguous for each machine in every trial. During the measurement windows,
the observer reported the expected active alarm for machine 1 and inactive
alarm for machine 2, with valid RMS/peak values.

## Earlier microbenchmarks

These measure process CPU time per operation, using the median of seven
alternating before/after batches with a warm cache. The synthetic broker case
has two RAM subscriptions, without sockets, adapters or business processes.
It therefore measures a different scope from the application above.

| Operation | Payload case | Before, microseconds | After, microseconds | Reduction |
| --- | --- | ---: | ---: | ---: |
| Validate | Measurement, 26 bytes | 134.15 | 32.74 | 75.6% |
| Validate | Vibration alarm, 47 bytes | 872.38 | 70.79 | 91.9% |
| Validate | Vibration signal, 65,577 bytes | 2,675.82 | 1,785.97 | 33.3% |
| Broker publish, RAM only | Measurement | 379.59 | 241.28 | 36.4% |
| Broker publish, RAM only | Vibration alarm | 1,214.59 | 285.90 | 76.5% |
| Broker publish, RAM only | Vibration signal | 3,373.84 | 2,483.18 | 26.4% |

The large validation gains translate into a smaller application gain because
networking, scheduling, IPC, payload decoding and business computation remain.
The data supports retaining this small Python optimization. It does not
establish that the small changes in individual business processes are caused
by the cache: their code is unchanged, actual throughput differs slightly,
and CPU scheduling/frequency vary on this phone. No fixed CPU affinity or
frequency was imposed. Three trials per version do not establish a portable
performance guarantee or quantify energy consumption. Distributed execution,
TLS and active recording need separate measurements.

## Local measurement artifacts

The following files are available in the ignored `build` directory of the
measurement checkout:

- `build/validation-context-cache/benchmark.py` and `results.json`: synthetic
  benchmark and its raw results.
- `build/validation-context-cache/process_cpu.py`: real-process measurement
  harness; its `hook/sitecustomize.py` is generated automatically.
- `build/validation-context-cache/before.py`: baseline runtime source, SHA-256
  `d82100a7e086a4d59ec7f08d2d7945f39a608b249aa2949b99b9604d83bff221`.
- `build/validation-context-cache/process-cpu/results.json`: six full trials,
  PIDs, counters, CPU samples and runtime source hashes.
- `build/validation-context-cache/process-cpu/summary.json`: pooled means and
  throughput normalization, produced by `consolidate_cpu.py`.
- Each trial directory contains its isolated plan, startup mode markers,
  observer log, broker status and raw result. All launched processes were
  stopped after their trial.

To repeat locally while retaining the original measurements:

```sh
make -C vibration-watch build
python3 build/validation-context-cache/process_cpu.py --output process-cpu-repeat
```

The earlier optimization checks passed 186 unit tests and 38 focused
integration tests, covering validation errors, delivery/backpressure and
distributed execution. This measurement task adds no runtime changes.
