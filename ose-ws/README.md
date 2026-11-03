# OSE Web Console

`ose-ws` observes and configures an **already running** OSE application. It is a
separate Python package with a bundled Vue 3 / TypeScript frontend. It connects
through the existing OSE RPCs; it does not launch, build, stop or redeploy OSE.

The interface, source code, messages and documentation are in English.

## Install from this repository

Run these commands from the [OSE repository root](../README.md), which contains
the `ose/`, `sdl/` and `ose-ws/` project directories.

Requirements: Python 3.10+, Linux or Termux, the compatible OSE and SDL Python
runtimes. Node.js 22 LTS (22.12+) or 24 LTS is needed only for frontend development.
On Termux, install Rust/Cargo and a C toolchain if Pydantic's native dependency
must be built from source rather than installed from a compatible wheel.

```sh
python3 -m venv .venv
. .venv/bin/activate
python -m pip install ./ose './ose-ws[recording]'
export PYTHONPATH="$PWD/sdl/runtime/python${PYTHONPATH:+:$PYTHONPATH}"
```

The checked-in frontend is included in the wheel. Installed users do not need
Node.js, a CDN, a model checkout or generated application classes. The SDL runtime
must remain importable; optional recording uses `sdl_recording` and MCAP 1.5.0.
For filters, prepare the native SDL filter service in the console **and** the
broker/workers before starting them:

```sh
make -C sdl filter-python
export PYTHONPATH="$PWD/sdl/runtime/filter/python:$PWD/sdl/build/filter/release:$PYTHONPATH"
```

No HTTP request downloads dependencies or invokes a compiler.

## Start

Run from the application directory, where `build/endpoint.json` identifies the
running local broker:

```sh
ose-ws init --config ./ose-ws.json
ose-ws check --config ./ose-ws.json
ose-ws --config ./ose-ws.json
```

Open **http://127.0.0.1:8080** and enter the access token from
`.ose-ws/access.token`. The initializer prints its path, never the token itself.
It creates the configuration and token exclusively, with owner-only permissions.
`check` validates local settings and secrets without contacting OSE.

```sh
ose-ws --config ./ose-ws.json --endpoint ./other-build/endpoint.json --port 8081
```

An unavailable OSE application does not prevent the web server from starting.
The console shows a disconnected state and reconnects with exponential backoff.
Endpoint files are reread on each connection attempt. Use one Uvicorn worker;
an exclusive lock refuses another console using the same state directory.

## Four workspaces

- **Components:** shared `status` sampling, the same diagnostics as `ose top`,
  sliding-window rates, freshness, FIFO occupancy, alerts and queue details.
  Freezing the view does not stop server collection.
- **Measurements:** component/name discovery, leased logical selections, bounded
  in-memory history, ECharts zoom and separate-chart mode, CSV export. Points
  represent successful observations of latest values. They are not an exhaustive
  stream of emissions. Missing/stale values create gaps; generations create
  separate segments. History starts at selection and is lost on console restart.
- **Logs:** the second page, after Components; an in-memory component log stream
  with component/category/text filters, minimum severity through FATAL, pause,
  resume and JSONL export. Scoped `logs.read` rights control access. See
  [component logs](../ose/docs/logs.md) for SDK deposits, CLI access and retention.
- **Recordings:** exact names, SDL types and globs; concrete draft selections;
  optional validated source predicates; PCAPNG/MCAP; rotation and cyclic
  retention; independent capture counters, explicit stop, protected download
  and offline validation. Closing a browser does not stop a capture. Capture
  configuration can be duplicated into a new one-shot object.
- **Parameters:** exact-path access, schema exploration, recursive typed fields,
  fixed multidimensional arrays, paged sequences, optional presence, enums and
  complex values. Validation round-trips through the SDL codec and previews
  `fl32`/wire quantization. Import/export preserves exact 64-bit integers.

Parameter access requires a declared OSE client with the appropriate rights.
Set its existing token without putting it in the configuration:

```sh
export OSE_PARAMETER_TOKEN='the-token-declared-in-your-OSE-deployment'
ose-ws --config ./ose-ws.json
```

Applying a parameter replaces the **entire block**. The last write processed by
OSE wins, including concurrent writes from another browser, a component or CLI.
There is no CAS, lock, revision, automatic merge or `If-Match`. An acknowledgement
means stored by OSE; it does not mean every component has consumed the value.
External changes preserve a browser draft and update its separate remote value.
The active section is kept in the URL fragment (for example, `#parameters`), so
reloading or returning to a browser tab restores that section. Drafts stay in
browser memory when navigating; reload/closing can discard them.

Failed validation marks the affected field in red with an inline message and
moves focus to it. Nested branches expand and long sequences show the page
containing the invalid element. **Go to field** returns to that location.
The marker clears when that value is edited; validate again before applying.
Failures without a field location stay visible beside the editor's action buttons.

## Configuration and network access

See [configuration](docs/configuration.md) for paths, settings, proxy identity
and web policies, and [the specification](../ose/docs/ose-ws-specification.md)
for the functional contract.

The default is loopback with a local token and an HttpOnly/SameSite session
cookie. Mutations require CSRF. Sessions expire after 30 minutes idle or eight
hours total. WebSocket pings and ordinary reads do not extend session lifetime.
Do not expose local-token mode on the network.

Network access requires an HTTPS reverse proxy that authenticates each user,
strips incoming identity headers and sets `X-OSE-User` and
`X-Forwarded-Proto: https`. Configure the proxy's exact IP addresses and each
user's permission scopes. Direct requests outside that trust set are rejected.
The console's listener must only be reachable from those proxies. Browser TLS
and the application's OSE mTLS credentials are independent.

## Operations and recovery

Commands use `Idempotency-Key`; the frontend generates one for each explicit
action and **does not automatically resend a command** after a transport failure.
An explicit unchanged retry reuses the original in-memory key; acknowledged
actions release it. A lost reply is never silently converted into a fresh write.
SQLite persists the intent before dispatch. Retries with the same actor, target,
key and body return that operation; different bodies are rejected.

A lost OSE acknowledgement leaves the outcome uncertain. Re-reading an equal
value is not proof that a particular write succeeded. Administrators can use
**Operations & audit** to close an uncertain operation as **undetermined**, with
a required audit note. This sends no command and never claims success. Closed
keys remain protected for at least 24 hours; unresolved keys do not expire.
Closure releases the separate unresolved-operation quota while retaining the
idempotency record. The total retained-operation quota still applies.

When metadata storage fails, new mutations stop. Explicit stops of active
captures remain available through a degraded, non-durable operation. This is
the exception to persisting an intent before dispatch. The UI reports the
storage warning; a subsequent crash may leave the final state unknown.

On console restart, previously active captures become `INTERRUPTED`. Existing
files are enumerated as artifacts of unknown integrity until validated. The
console never resumes a capture or replays an old parameter write automatically.
SIGTERM attempts to drain captures; SIGKILL cannot guarantee finalization.

Capture files remain under `.ose-ws/captures/<id>/`; labels are not paths.
Offline integrity checks use one short-lived read-only worker at a time, with
a configured deadline; the main process retains exclusive ownership of state.
Only closed files can be downloaded. File descriptors are opened without
following symbolic links and checked against recorded device/inode identities.
Use **Delete** on a draft or a finished capture to remove it from the list.
The confirmation keeps files on disk by default; select **Delete files permanently**
to remove them too. Retained files stay under `captures/<id>/` in the state
directory and are no longer accessible through the console. Active captures
must be stopped and finalized first. Only an administrator or the capture owner
with `recordings.delete` permission can delete it. Operation and audit history
are retained. Do not modify active capture files outside the console.

## Build and verify

```sh
cd ose-ws/frontend
npm ci
npm run build
npm test
cd ../..
python -m pip install './ose-ws[test,recording]'
make -C ose-ws test
cd ose-ws
python -m build
python tests/check_wheel.py dist/ose_ws-0.1.0-py3-none-any.whl
```

`npm run build` type-checks Vue/TypeScript and replaces bundled static assets.
`package-lock.json` locks frontend dependencies. `requirements.lock` records
the backend environment used for qualification; Python package metadata keeps
compatible ranges so other supported Python versions can resolve dependencies.

Backend tests use real OSE brokers, parameter RPCs, tap sessions and recordings;
WebSocket tests run the HTTP server on an ephemeral loopback port. They also
cover uncertain operations, duplicate dispatch, emergency stops, quotas, exact
integer/quantized values and file replacement. Frontend tests cover exact value
conversion, structural editing, page isolation and confirmed capture deletion.
CI builds both wheel and sdist and verifies
that the wheel includes its offline frontend.
The installed-wheel check runs outside the checkout and verifies initialization,
embedded assets, authentication, startup without OSE, exclusive state ownership,
occupied-port failure and graceful shutdown.

The initial reference load remains a qualification target, not a throughput
guarantee: 100 components, 128 followed measures at 2 Hz and 20 browsers. No
universal capture rate or real-time latency guarantee is made.

See [validation results](docs/validation.md) for the checked configurations and
the remaining browser/production-load qualification limits.
