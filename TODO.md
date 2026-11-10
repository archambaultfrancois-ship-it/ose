# OSE follow-up work

## Simulation with microsteps

- [x] Implement the execution-by-stages alternative for activating feedback loops.

Implemented as a simulation launch policy (step 12 of the
[transition plan](ose/docs/cenv2-transition.md#ordered-migration-gates)):
`ose run --mode simulated --microsteps`, also available for simulated replay.
There is no model or compiled-plan field. See the
[execution documentation](ose/docs/simulation.md#microsteps-and-activating-feedback)
and [runnable example](ose/examples/feedback-microsteps/README.md).

### Principles to preserve

- Separate physical/virtual time from logical stages. Represent an execution
  position as `(time_ns, microstep)`; a new microstep at the same timestamp must
  not invent a physical delay or advance an unrelated timer.
- Break instantaneous circular dependencies through a stage boundary. A
  calculation reads the inputs visible to its stage; its feedback must become
  visible at a later stage, rather than recursively restarting the same stage.
- Make the boundary part of the execution contract. Removing a cycle
  check alone is insufficient; the scheduler must enforce the visibility rule.
  Preserve rejection of unsupported instantaneous activation cycles.
- Keep deterministic selection, publication visibility and trace ordering,
  independently of worker count and physical callback completion order.
- Retain one input origin per callback, explicit timers and event-to-output
  permissions. Do not restore `on_tick` or implicit multi-input snapshots.
- Preserve Push/Pull, FIFO/latest, nonblocking absence and exact message-ID
  selection. Do not impose a mandatory initial message or latest-only feedback.
  An algorithm may still require its own initial state to make progress.
- Preserve deferred withdrawals: successful FINISHED commits consumption;
  callback failure cancels its withdrawals and staged outputs. A stage barrier
  must not silently turn this into a global transaction.
- Detect an unbounded same-time feedback loop. Define a microstep limit and
  diagnostics identifying the timestamp, activation path and pending work.
  Do not silently advance time, drop messages or declare convergence merely
  because successive payloads happen to be equal.
- Leave CENV/2, SDL payloads and dynamic per-message contract exchange unchanged.

### Resolved decisions and qualification

- [x] Global publication boundary, selected at launch; all model and endpoint
  validation remains active. Ordinary and real-time execution reject activation
  cycles before component startup; cyclic models can be edited and compiled.
- [x] One activation per component per microstep, parallel components up to the
  worker limit. Repeated activation at the same timestamp is allowed. Steps start
  at zero and reset when physical time advances; no ready or future work means
  quiescence.
- [x] Inject scenario messages and settle admissions before selecting callbacks.
  Reception cancels same-time inactivity expiration, but not an independent
  timer. The duration horizon remains exclusive.
- [x] Freeze eligible inbox handles before dispatch; commit successful callback
  withdrawals independently, then publish after the stage barrier in component
  and emission order. Admission waits retain physical virtual deadlines.
- [x] Simulation only. Trace/diagnostics expose the microstep; SDK references,
  CENV/2 and per-message dynamic SDL contract exchange remain unchanged.
- [x] Bound activation stages per timestamp with `--max-microsteps` (default
  10,000); fail explicitly with timestamp, activation path and pending work.
  The last allowed stage may settle without error if no activation remains.
- [x] Qualify terminating loops, state-based termination with equal payloads,
  bounded infinite loops, worker-count-independent traces, FIFO/latest, Pull
  rollback, waits and timer ordering. Preserve ordinary v2 trace bytes.
