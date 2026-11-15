# Native and Python runtime optimizations

The normal runtime uses bounded catalogue identity caches, parameter version
caches returning independent copies, grouped callback writes, and simulation
observation refreshed at most every 50 ms while running. Pauses, breakpoints and
completed advances expose an exact committed snapshot. Commands during an active
wave never force observation of its partially received measurements. Every log
is collected at the ordinary wave boundary even when observation is deferred.

`ose._native` accelerates SDL validation, record/activation encoding and CH/2
size checks and frame parsing. It uses the existing SDL C runtime and has no
Rust dependency. It validates external data and callback outputs; successful
native validation is not repeated in Python. Invalid input goes through the
Python validator for consistent diagnostic messages.

Build from a checkout with `make -C ose native SDL_ROOT=/path/to/sdl`, using a C
compiler and development headers matching the selected Python. Wheel builds
compile the extension when SDL_ROOT and those headers are available. A source
distribution includes the OSE C wrapper but requires the SDL checkout to compile
it. Python fallback kernels remain available without that extension and can be
selected explicitly with `OSE_PYTHON_NO_NATIVE=1`.

Thermal Workshop separately compiles `native/workload.c` into its build directory
and places it on each generated component's Python path. The plant calls native
sample generation directly; the Python analyzer reference can also use native
burn. Four complete C analyzers are the default deployment. Their ordinary double
summation intentionally does not emulate Python's compensated sum.

## Integration measurements, 2026-11-15

Thermal Workshop stress, 20 actual component processes, 8 workers, interval
[0, 1 s), startup and shutdown excluded. Three sequential reference runs before
integration and three runs after builds/tests finished. Medians:

| Metric | Before (already using plain C analyzers) | Integrated runtime/application |
|---|---:|---:|
| Elapsed | 3.182 s | 2.154 s |
| Coordinator CPU | 2.733 s | 1.944 s |
| Aggregate component CPU | 4.790 s | 2.870 s |

Elapsed reduction: approximately 32.3%, or 1.48 times the throughput of the
already-C-analyzer reference. These are separate short series, not interleaved
pairs; host load and frequency are uncontrolled. The workload remains slower
than real time. Do not interpret small differences as statistically qualified.

All six runs execute 6,026 callbacks, 6,453 publications and 891 waves, with trace
SHA-256 `2f2ced87ac4ad97735b044869465b264564f2d94fd816e7679279f1531b46c76`.
The normal runtime is used without callback source transformation. Raw data remain in
the workspace's ignored `build/performance/production-before.json` and
`production-after.json`.

Validation includes 414 unit tests, 64 simulation tests with native kernels and
the same 64 with Python fallback, 7 Thermal Workshop tests including live web
reads, and integration checks for CH/2 processes, shared parameters and measures.
The built wheel contains the extension and its installed Python/C SDKs execute
outside the checkout. Dedicated differential tests cover malformed envelopes,
arrays, exact wire sizes and IDs, fragmented frames and EOF/error ordering.

## Real-time broker optimizations

The broker now prepares a CENV/2 metadata prefix per admitted route, checking the
complete size from that prefix and the body length. It no longer copies the body
just to check whether a delivery fits. Queued TCP deliveries and retries reuse
the prefix. Each route has its own copy of the record metadata, including for
external subscribers and retained-state delivery.

TCP output groups up to 32 frames and 256 KiB per write/drain, with a larger valid
frame sent alone. Each packet retains its ordinary length prefix and CENV/2 bytes.
The connection lock spans the entire write/drain. Deliveries are marked sent only
after drain succeeds, and an ACK received while drain is blocked cannot resurrect
an already removed record. No timer cancels and rewrites a pending write.

When every route uses a non-waiting overflow policy, admission runs inline and
yields once afterwards for network progress. Routes that can wait retain their
independent concurrent waits. Compact CENV/2 headers are encoded in C when the
extension provides that kernel; generic and extended metadata retain the Python
encoding. This is an encoder optimization, not a new protocol version or a
native rewrite of the whole broker.

Thermal Workshop stress measurements use its 20 real component processes and a
status reader polling every 500 ms. Application endpoint/status files are isolated
in a temporary directory, and all processes are reaped after each run. Startup and
shutdown are excluded. An initial three-run series measured roughly 9% more
publications/s from prefix caching, TCP grouping and inline fan-out alone.

The final comparison adds native compact encoding. Three blocks surround each
6-second optimized run with two reference runs using the previous broker and
CENV/TCP implementations. Other runtime/application optimizations, C analyzers and
the stress workload are shared. Median gains relative to the mean adjacent
references:

| Metric | Paired gain |
|---|---:|
| Publications per second | +11.7% |
| DATA deliveries per second | +12.0% |
| Broker CPU per publication | −10.6% |

No retries occurred during these nine runs. The three publication-throughput gains
range from 7.7% to 21.8%; machine load/frequency are uncontrolled and three blocks
are not a statistical qualification. The broker still consumes approximately one
full core under this stress load; this change improves throughput without claiming
to remove that architectural limit. Real-time queue losses and callback counts can
change with throughput, so simulation trace equality is not a real-time acceptance
criterion.

Raw data: `build/performance/broker-before.json`, `broker-after.json`,
`broker-native-after.json`, `broker-paired.json` and its individual block files.
The reference sources were temporary copies of the pre-change broker, sessions,
CENV and TCP files; their SHA-256 hashes are recorded in `broker-paired.json`.
An instrumented profile is kept separately from throughput comparisons.

For a new measurement, source the checkout's `setenv.sh`, then run:

```sh
"$PYTHON" ose/tools/bench_broker.py --plan thermal-workshop/build/plan.json --output build/performance/broker-current.json
```

`--duration`, `--repeats` and `--profile` control collection; profiling requires a
single run and must be measured separately. The benchmark is not a workload
generator: it runs the compiled plan and measures actual broker traffic.

Final verification: 418 unit tests, 41 broker/client/process integration tests,
and all 7 Thermal Workshop tests pass. The CENV/TCP fallback suite passes with
native kernels disabled (its native-only comparison is skipped). Added regression
cases cover compact C/Python wire equality and invalid metadata, exact sizing,
blocked multi-frame writes, early ACKs, retry exhaustion, FIFO order across byte-
bounded batches, and serialization against concurrent control writes. The built
wheel was installed separately and its native CENV kernel was exercised there.

## Second broker pass

The next pass removes the intermediate CENV packet allocation before TCP framing:
length prefix, cached metadata and body are joined once into the write buffer.
Mutable caller buffers are frozen before waiting for the connection lock; the
normal immutable-byte path avoids that additional copy.

Sender notifications produced by its own sent/remove operations are cleared
before reading the current retry heap. New admissions that arrived during a
blocked drain remain runnable through their current deadlines. Empty queues wait
directly on the event; immediately due work yields without constructing a timeout.

Publication requests can complete eagerly with Python 3.12+ when neither
admission nor output needs to wait. The standard eager task constructor owns
the coroutine throughout: an admission wait or backpressured drain suspends as
an independent task, leaving the reader able to process ACKs and control RPCs.
Ordinary task creation remains in use with older Python or a custom loop task
factory. Every 32 buffered input frames, the connection yields to other tasks.
Direct calls to Broker.publish retain their existing yield by default.

Comparison uses a temporary copy of the already-optimized runtime from the end
of the first pass, including its CENV C encoder. Each series has three blocks
reference → candidate → reference, six real seconds per run, with startup and
shutdown excluded and the same 500 ms status polling.

| Series | Median publication throughput gain | Median CPU/publication reduction |
|---|---:|---:|
| Ordinary CPU affinity | +3.3% | 4.0% |
| Broker pinned to one allowed CPU, components on the others | +10.5% | 9.7% |

These are additional gains relative to the previous broker pass. They are not
uniform: individual ordinary-affinity gains range from −5.5% to +7.4%, and pinned
gains from −5.0% to +11.8%. Three blocks do not establish a reliable minimum gain;
host load/frequency and the changing real-time traffic mix still matter. CPU
pinning is only a benchmark option, not a change to normal application launches.
The broker remains a single event loop and can still saturate one core.

Raw results: `build/performance/broker-stage2-paired.json` and
`broker-stage2-pinned.json`, with per-run files and source hashes in the same
directory. `bench_broker.py --broker-cpu N` pins only the benchmark broker and its
own descendants during collection, then restores the parent's original affinity.
It requires Linux and at least two allowed CPUs. The instrumented pre-change
profile is separate from the throughput series.

Regressions cover data enqueued during a blocked drain without a later wakeup,
immutable wire ownership while waiting for a write lock, ordered bounded batches,
ACKs/retries, custom task factories, and the path without eager-task support.

Final checks pass: 420 unit tests, 44 broker/client/process integration tests,
7 Thermal Workshop tests, the Python-only transport suite, and installed wheel
SDK checks for Python and C. A reverse-reference comparison patch is preserved as
`build/performance/broker-stage2-change.patch`; the temporary source copy was
removed. Measurement processes and application services are stopped.
