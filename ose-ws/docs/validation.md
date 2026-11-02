# Validation record

The initial implementation was checked on Termux / Android arm64 with Python
3.13.13, Node.js 24.17.0 and npm 11.17.0. The primary Python dependency versions
are recorded in `requirements.lock`; frontend versions are locked in
`frontend/package-lock.json`.

| Check | Coverage |
| --- | --- |
| OSE unit suite | 166 existing tests, including shared diagnostics, selection, transport, measurements and parameters. |
| Console backend suite | HTTP sessions/CSRF/Origin/policy, actual broker/coordinator RPCs, exact SDL types and quantization, complete-block overwrites, import/export, leases/history, WebSocket invalidation, real capture drain/validation/download and artifact replacement. |
| Recovery and quotas | Persisted pending intents, interrupted captures, at-most-once dispatch, administrative unknown closure, unresolved quota release without key loss, and degraded stops during storage failure. |
| Sustained history | A simulated hour at 128 signals / 2 Hz, with generation replacement every five minutes, within 32 MiB of accounted history buffers and 250,000 samples. This uses a virtual clock, not a wall-clock deployment load test. |
| Frontend | Five value/tree helper tests, three mounted Vue interaction tests and three command-recovery tests: exact uint64 editing, optional presence, preserving a dirty draft during remote changes, validated application, frozen reactive views and unchanged retries after lost replies. |
| Production build | Vue/TypeScript type checking and Vite's embedded, local-only assets. |
| Distribution | Wheel and sdist include the frontend; an installed wheel is launched outside the source tree, serves the English frontend, authenticates locally, stays ready without OSE and exits through SIGTERM. |

The backend suite was run against FastAPI 0.119.0 / Pydantic 1.10.27 / Starlette
0.48.0, and FastAPI 0.162.0 / Pydantic 2.14.2 / Starlette 1.10.0. The latter
configuration is the primary reference environment. On Termux, its native
Pydantic dependency was compiled with release optimization disabled for the
local functional checks; no throughput comparison is inferred from that build.

CI repeats the backend and frontend checks on Python 3.10 and 3.13 under Linux,
builds the distributions and launches the installed wheel. The workflow is
included in this change; a remote CI result is not inferred from local runs.

## Qualification limits

The mounted Vue tests use a DOM test environment. No interactive browser visual
inspection, screen-reader audit or cross-browser certification was performed.
The full reference load (100 components, 20 actual browsers, sustained capture
throughput and p95 render latency) remains a deployment qualification exercise.
The simulated history test is not a substitute for that load measurement.

Distributed control/parameter discovery is tested with a real coordinator.
Production mTLS topology, producer-worker reachability and certificates must be
qualified with the intended deployment. Source filters reuse the existing native
OSE implementation and require its optional service to be prepared at both ends.
No general capture-throughput, loss-free delivery or real-time latency guarantee
is claimed.
