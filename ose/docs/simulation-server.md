# Observe and control a simulation

CLI simulated runs automatically host a loopback OSE RPC server. The server and
scheduler share one process; business components keep their CH/2 subprocesses.
The transport is separate from the functional simulation engine: network reads
never advance virtual time, and the Python `Simulation`/`run` library API remains
usable without a server. Simulated replay retains its existing CLI controls.

From an application environment:

```sh
source ./setenv.sh
ose app simulate --duration 60 --interactive
# Another terminal, with the same application environment:
ose-ws
```

Open the console and select **Simulation**. The application is built first;
`--duration` is the exclusive horizon in simulated seconds. Interactive runs
start paused and retain their listener and final logical snapshot after reaching
the horizon, quiescence or an execution failure. Ctrl+C/SIGTERM closes the server
and reaps components. Closing the browser does not stop the session. Terminal
controls are also available when launched from a terminal; a detached terminal's
EOF leaves the server available. Without a terminal, use the web console or
`--command`/`--command-file` for scripted commands.

The terminal prompt and its history belong to the server and survive session
completion and reset. Terminal and browser commands use the same command service,
operation arbitration and breakpoints. Interactive terminal advances return the
prompt immediately and report completion asynchronously, so `status` and `pause`
remain available during an advance. Command scripts and piped input wait for each
operation in order. A command script ends the session when its commands finish;
EOF on ordinary terminal/piped input only detaches that input. `stop`/`quit` stops
the session; Ctrl+C closes the interactive server. These terminal changes apply
to simulation runs; replay keeps its existing controls and lifetime.

Without `--interactive`, simulation runs automatically and the listener closes
when the run finishes. Short batch simulations may finish before a web console
can observe them. Existing Makefile simulation targets retain their application
scenario preparation and use this same automatic server through `ose run`.

For a compiled plan, the equivalent command is:

```sh
ose run --mode simulated --plan build/plan.json --duration 60 --interactive
```

`--port` selects the RPC port; omission selects a free port. The server atomically
publishes `build/endpoint.json`, with `mode: "simulated"` and an execution epoch.
ose-ws discovers it using its existing reconnect path. Its HTTP port is separate.
The simulation RPC listener binds only to `127.0.0.1`; remote web access uses the
console's existing authenticated HTTPS proxy configuration.

## Session and control semantics

The server hosts one session. Its execution states are preparing, paused,
running, resetting, finished and failed. Reset is available on interactive
servers, while paused or terminal and with no active advance. It recreates the
components, virtual clock and parameters from the initial plan, generates a new
epoch and clears ordinary breakpoints and the session log. Persistent breakpoints
are re-armed at their original absolute times. Trace/capture outputs on reset
receive an epoch suffix, preserving the previous session's output. Commands naming an old
epoch are refused. Reset is not a checkpoint or reverse execution.

Only one advance/reset command can be active. A pause may interrupt an advance;
it waits for the current wave calculation, leaves the clock paused and marks
the advance interrupted. A callback which never returns cannot be safely paused
in its middle; process shutdown remains available through Ctrl+C/SIGTERM.
Observation remains available from the last committed snapshot during a wave.

- `step_instants`: settle the current instant and advance N future instants,
  pausing before callbacks at the final instant (external input injection at
  that instant follows the existing CLI instant-step semantics).
- `step_duration`: advance by a positive relative duration.
- `run_until`: advance to a strictly future absolute position.
- Duration/absolute targets are exclusive: events exactly at the endpoint have
  not run. RPC targets beyond the horizon are refused; terminal `step DURATION`
  retains its existing behavior of clipping to the horizon.
- `step_microsteps`: advance logical stages without jumping to a future timer;
  available only when launched with `--microsteps`.
- `break_add`/`break_remove`/`break_clear`: manage one-shot temporal breakpoints. A breakpoint
  precedes input injection and callbacks at its timestamp.
  Set `persistent: true` when adding one to retain it across resets, or use
  `break-time TIME --persistent` in the terminal. It fires once per session and
  remains listed while waiting for reset. Removing or clearing it also removes
  its persistence. Persistence lasts for this server process, not across restarts.
- `component_set`: enable or disable selected components under the wave barrier,
  pausing progression before changing their state.

RPC time arguments are unsigned decimal nanosecond **strings**, within uint64;
the browser uses BigInt when converting decimal seconds. Commands are structured
RPC operations, not shell commands. Parameters are applied under the same wave
barrier, traced with their logical position and payload hash, and become visible
to subsequent callbacks. Ordinary last-write-wins parameter semantics still apply.

## Services and clocks

The server implements `status`, `subscription_schema`, `logs`, the existing
`parameters_*` operations, `component_status`, `simulation_command` and
`simulation_operation`. Parameter RPCs retain the deployment's client/token and
path permissions through the shared `ParameterEndpoint`. Functional topology,
input queues and counters are exposed through the existing observation schema.
No transport delivery queue is invented for simulation's shared input buffers.

`status.execution` includes capabilities, position/horizon, microstep, pause/stop
reason, current operation and recent operation outcomes. Epoch-bound commands
require a command ID; duplicate IDs return the original operation, and conflicting
reuse is refused. The server retains at most 256 operations. It is an in-memory
service; persistence and durable web idempotency belong to ose-ws.

| Data | Clock |
| --- | --- |
| Position, functional timeouts, admission/publication rates | Simulated |
| Application measurement chart horizontal axis | Simulated |
| Callback duration, component CPU and RSS | Real |
| Network freshness, authentication and collection cadence | Real |

Pause does not make a component's logical snapshot stale. At termination the
console labels the retained state as a final snapshot; component processes have
stopped. Logs preserve their real emission timestamp plus the simulated position
at which the server collected them at a wave boundary.

Measurement charts remain bounded **observations of latest values**, not an
exhaustive record of emissions. Polling a fast simulation can skip intermediate
values. Frozen view and simulation pause are separate controls. Reset separates
measurement segments through the new epoch; simulated and real-time segments
are not combined on one chart axis. CSV exports label their time basis.

Dynamic recording/tap RPCs and Live message previews are not yet implemented for
the simulation server. The console hides those live controls. Launch-time
`--record`, `--record-file`, `--record-from` and `--record-until` remain available
on `ose run --mode simulated`; use offline recording tools for those artifacts.
Existing recordings held by ose-ws remain accessible.

## Web authorization and operations

Reading `GET /api/v1/simulation` requires `components.read`. Sending
`POST /api/v1/simulation/commands` requires `simulation.control` (scope `*`), a
valid CSRF token, an Idempotency-Key and the current epoch. The local administrator
already has these rights. Proxy users can receive observation and control rights
independently. Normal measurement, log and parameter permissions continue to apply.

The console records a durable operation before dispatching a command and returns
its operation ID immediately. It follows the server operation on an independent
connection, allowing pause while an advance is active. Lost acknowledgements stay
uncertain; the console does not automatically resend commands. Application state,
including simulation state, is distributed through the existing shared collector
and application WebSocket stream, with periodic HTTP refresh as a fallback.
