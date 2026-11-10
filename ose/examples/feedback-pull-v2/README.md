# V2 feedback

This example runs the new execution model with generated Python containers,
CH/2 input callbacks and real SDL contracts. Build from the repository root:

```sh
PYTHONPATH=ose/src python3 -m ose.cli build \
  --model ose/examples/feedback-pull-v2/model.json \
  --deployment ose/examples/feedback-pull-v2/deployment.json \
  --sdl-root sdl --build-dir ose/build/feedback-v2
PYTHONPATH=ose/src python3 -m ose.cli run --plan ose/build/feedback-v2/plan.json \
  --mode simulated --duration .1 --workers 4 --trace ose/build/feedback-v2/trace.jsonl
PYTHONPATH=ose/src python3 -m ose.cli run --plan ose/build/feedback-v2/plan.json --duration .3
```

Use `deployment-rust.json` for two native Rust components or
`deployment-mixed.json` for a Rust timer/Pull source and Python responder:

```sh
PYTHONPATH=ose/src python3 -m ose.cli build \
  --model ose/examples/feedback-pull-v2/model.json \
  --deployment ose/examples/feedback-pull-v2/deployment-rust.json \
  --sdl-root sdl --build-dir ose/build/feedback-v2-rust
PYTHONPATH=ose/src python3 -m ose.cli run --plan ose/build/feedback-v2-rust/plan.json \
  --mode simulated --duration .1 --workers 4
```

See [Rust input callbacks](../../docs/rust-inputs.md) for typed references,
selectors, timers and callback error handling. The Rust and Python examples
produce the same logical trace. `make test-rust-inputs-v2` exercises both native
and mixed graphs with one/four workers and real TCP/UDP delivery.

Use `deployment-c.json` for two C components or `deployment-c-python.json` for a
C timer/Pull source and Python responder. Build/run with the same commands above,
substituting that deployment and a distinct build directory. See
[C input callbacks](../../docs/c-inputs.md) for reference lifetimes and API details.
`make test-c-inputs-v2` checks C/Python exchanges in both directions.

Use `deployment-java.json` for two Java components or `deployment-java-python.json`
for a Java timer/Pull source and Python responder. See
[Java input callbacks](../../docs/java-inputs.md) for the typed API, timers and
toolchain requirements. `make test-java-inputs-v2` checks Java/Python exchanges in
both directions. JDK 21 local builds require explicit `OSE_JAVA_LOCAL_RELEASE=8`;
these builds do not verify Java 7 compatibility.

`deployment-ada.json` and `deployment-ada-python.json` provide Ada components for
the same example. The all-Ada deployment compiles with GNAT 12.2.0 and produces
the same trace as the Python/C/Rust/Java reference with 1 and 4 workers: six
callbacks, six publications and quiescence, SHA-256
`796b451975944f54c7b029e2f007806efae542fb6f4b029d39d099abd5e03002`.
All 23 strict CH/2 Ada tests pass, including both directions of mixed Ada/Python
feedback. See [Ada input callbacks](../../docs/ada-inputs.md) and
`make test-ada-inputs-v2` for the execution qualification.

For distributed real-time execution, use `deployment-distributed.json`, with a
direct forward route and broker feedback. From the repository root, build once:

```sh
PYTHONPATH=ose/src python3 -m ose.cli build \
  --model ose/examples/feedback-pull-v2/model.json \
  --deployment ose/examples/feedback-pull-v2/deployment-distributed.json \
  --sdl-root sdl --build-dir ose/build/feedback-v2-distributed
```

Run each command below in its own terminal. `--insecure` is restricted to loopback;
use mutual TLS for remote deployments. Stop the processes after observing three
publications from each worker. Workers remain available after the timer disarms.

```sh
PYTHONPATH=ose/src python3 -m ose.cli serve \
  --plan ose/build/feedback-v2-distributed/plan.json --host 127.0.0.1 --port 7000 --insecure
PYTHONPATH=ose/src python3 -m ose.cli worker \
  --plan ose/build/feedback-v2-distributed/plan.json --node a --host 127.0.0.1 --port 7000 --insecure
PYTHONPATH=ose/src python3 -m ose.cli worker \
  --plan ose/build/feedback-v2-distributed/plan.json --node b --host 127.0.0.1 --port 7000 --insecure
PYTHONPATH=ose/src python3 -m ose.cli cluster-status --host 127.0.0.1 --port 7000 --insecure
```

Real-time feedback depends on delivery timing; it has no simulated trace
equivalence guarantee. See [distributed execution](../../docs/distributed.md) for
READY, timer clocks, replacement and generation fencing.
`make test-distributed-inputs-v2` tests Python and C processes on loopback.

The real-time run uses the binary CENV/2 network transport. A smaller in-process
reference remains available without CH or network delivery:

From the repository root:

```sh
PYTHONPATH=ose/src python3 ose/examples/feedback-pull-v2/reference.py --sdl-root sdl
```

The data graph is `a.out -> b.input -> b.out -> a.feedback`. The feedback input
is Pull and starts empty. Only `a.clock` and `b.input` activate outputs. Their
`on_clock(ctx, input)` and `on_input(ctx, input)` callbacks receive one reference
each; `on_tick` is absent.

A fires every 10 ms, consumes the preceding feedback when available, and sends
a `Value` with an application ID. B adds 100 and preserves that ID. A disarms its
timer after its third activation. The result has six callbacks, feedback values
`null`, `101`, `102`, and a final queued response. That response alone does not
activate A, so the run ends with `reason: quiescent`.

Pull withdrawals are hidden immediately within the callback, and release capacity
on successful completion. An exception cancels them. Publications are also staged
until successful completion. The final response remains queued without activating
another callback. Real-time runs stop after the requested duration; simulation
detects quiescence.

From the OSE project directory, `make test-inputs-v2` runs the model/scheduler
regressions, binary CH/2 checks and subprocess/typed SDL integration tests.
