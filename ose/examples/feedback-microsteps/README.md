# Activating feedback with microsteps

Both message inputs authorize their output: `a → b → a` is an activation cycle.
A one-shot timer starts it at 10 ms. Each message increases a counter; `a` stops
publishing after receiving 7. No initial queued message or latest policy is needed.
The ordinary v2 model contains no simulation configuration.

From the repository root, with the source environment configured:

```sh
ose build --model ose/examples/feedback-microsteps/model.json \
  --deployment ose/examples/feedback-microsteps/deployment.json \
  --sdl-root sdl --build-dir ose/build/feedback-microsteps
ose run --plan ose/build/feedback-microsteps/plan.json \
  --mode simulated --microsteps --duration .1 --workers 4 \
  --trace ose/build/feedback-microsteps/trace.jsonl
```

The run reaches quiescence after nine callbacks and eight publications, all at
`time_ns=10000000`. Callback microsteps are 0 through 8. Publications become
visible at the next logical boundary, without a physical delay. Using one worker
produces the same trace. Running without `--microsteps` refuses the activation
cycle before starting components.

Add `--max-microsteps 3` to exercise the explicit limit diagnostic: the run fails
at `(10000000, 3)`, reports the activation cycle and pending callbacks, and saves
these details in `last-status.json`. The default limit is 10,000 activation stages
per timestamp. Equal payloads never imply convergence.

See [simulation](../../docs/simulation.md#microsteps-and-activating-feedback)
for ordering, queue visibility, failure and replay rules.
