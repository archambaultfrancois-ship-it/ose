# OSE follow-up work

## Simulation with microsteps

- [ ] Revisit the execution-by-stages alternative discussed for feedback loops.

This is a deferred design option (step 12 of the
[transition plan](ose/docs/cenv2-transition.md#ordered-migration-gates)), not part
of the current v1 retirement. Push/Pull already permits feedback whose return
path does not activate an output. Microsteps would address models that need
activating feedback as well.

### Principles to preserve

- Separate physical/virtual time from logical stages. Represent an execution
  position as `(time_ns, microstep)`; a new microstep at the same timestamp must
  not invent a physical delay or advance an unrelated timer.
- Break instantaneous circular dependencies through a stage boundary. A
  calculation reads the inputs visible to its stage; its feedback must become
  visible at a later stage, rather than recursively restarting the same stage.
- Make the boundary part of the execution/model contract. Removing a cycle
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

### Decisions still to specify

- [ ] Choose the model declaration and boundary granularity: all publications,
  selected feedback links or explicit component/input stages.
- [ ] Specify whether an activation can run again at the same timestamp, stage
  numbering/reset, deterministic tie-breaking and the definition of quiescence.
- [ ] Define ordering between scenario events, arrivals, timer/inactivity
  expirations and microsteps at the same timestamp, including the exclusive
  simulation horizon.
- [ ] Specify inbox visibility and admission waits across boundaries, including
  FIFO capacity release, latest replacement, fan-in and arrivals during callbacks.
- [ ] Decide whether the feature is simulation-only or also has a real-time
  interpretation; document any SDK-visible stage metadata.
- [ ] Qualify a terminating feedback loop, a loop requiring application state,
  an infinite loop with diagnostics, and identical traces for multiple worker
  counts. Preserve existing v2 traces when the option is not enabled.

The discussion retained the execution-by-stages direction; the declaration
syntax and choices above remain design work, not previously agreed API details.
