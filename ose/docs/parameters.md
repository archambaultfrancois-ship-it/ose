# Shared parameter blocks

OSE retains complete typed SDL blocks at named paths and distributes their latest
values to explicitly bound clients. Several clients may read or write a block.
The last write processed by the service replaces the entire previous value.
There are no revisions, locks, merges or business acceptance callbacks.

## Model and deployment

Declare each block in `shared_parameters` with a message `root`, an optional
`schema` path (default: the model's `schemas`) and a complete JSON `initial` value:

```json
"shared_parameters": {
  "machines/press_1/detection": {
    "root": "DetectionParams",
    "initial": {"enabled": true, "threshold": 80.0, "hysteresis": 5.0, "window_ms": 200}
  }
}
```

Messages, nested records, fixed arrays, repeated/Packed sequences, enums and
optional fields use their SDL types. Enums accept labels or integer values;
complex values use `{"real": ..., "imag": ...}`. Required fields must be supplied,
including repeated fields; optional fields may be omitted or null. Unknown JSON
fields and invalid scalar/array values are errors. OSE encodes initial values
during build/validation. Contracts stay fixed during a run.

Each component declares local bindings:

```json
"parameter_bindings": {
  "detection": {"path": "machines/press_1/detection", "access": "read"},
  "security": {"path": "atelier/security", "access": "read"}
}
```

Access is `read`, `write` or `read_write`. Readers receive current values before
`on_start`, then updates between callbacks. A writer-only binding needs no initial
value in the component. Shared parameter paths form a namespace, without implicit
inheritance or subtree subscriptions. A component may bind several blocks and
several components may bind the same block. Existing static `parameters` remain
available through the existing APIs.

External clients receive rights in the deployment:

```json
"parameter_clients": {
  "operator": {
    "read": ["machines/press_1/detection", "atelier/security"],
    "write": ["machines/press_1/detection"],
    "token": "example-local-token-change-me"
  }
}
```

Local external sessions require the configured token (at least 16 characters).
Distributed sessions authenticate their exact client identity with mutual TLS;
their token can be omitted. Component rights come from their bound worker session
and compiled plan. Local component sessions retain the existing trusted-host model.
An external parameter session cannot impersonate a worker or publish message topics.

## Generated component APIs

| Language | Read binding `detection` | Write binding `detection` |
| --- | --- | --- |
| Python | `ctx.parameters_detection()` | `ctx.set_parameters_detection(value)` |
| C | `parameters_detection(ctx)` | `set_parameters_detection(ctx, &value)` |
| Rust | `ctx.parameters_detection()` | `ctx.set_parameters_detection(&value)?` |
| Java | `ctx.parameters_detection()` | `ctx.set_parameters_detection(value)` |
| Ada | `Parameters_Detection (Ctx)` | `Set_Parameters_Detection (Ctx, Value)` |

Methods are generated only for the declared access. Values are generated SDL
messages/records. Python and Java reads return independent decoded objects; C and
Rust return borrowed constant references valid during the callback; Ada returns a
record value. Reads do not use the network. Mutating a local copy has no service
effect; use the setter to send a complete block.

Writes are allowed from `on_start` and tick/input callbacks, not `on_stop` or
background tasks. They synchronously return the service's technical result: the
contract, size and write right have been checked and the value stored. They do
not wait for readers to apply it. A read/write component retains its current
callback snapshot even after its own write; it receives that value through its
subscription before a later callback. There is no atomic switch across blocks
or across components. A parameter change alone does not trigger a business tick.

The adapter manages network subscriptions. Generated runtimes decode typed blocks
and keep their values stable for each callback. Invalid wire data does not replace
a valid cached value or enter business code. Business constraints belong at the
writing application, or in an explicit application component.

## Retention, reconnects and limits

Adapters opt into event-driven `parameters_next` requests with `wait: true`.
An unchanged request waits up to ten seconds; a parameter change wakes it
immediately. Pending values still coalesce per block, and disconnect wakes the
reader and releases its subscription. Only one wait per subscription is allowed.
Legacy requests without this flag retain their 250 ms empty response, including
native clients. Python clients can opt in with `await client.next(wait=True)`;
`client.next()` preserves the legacy bounded-poll behavior.

The local broker or distributed coordinator owns the same parameter service.
A slow reader retains only a pending indication per block and receives the latest
value when it next polls. Intermediate values may disappear. A disconnected
reader releases its subscription and fetches current values on reconnect. A
replacement worker receives current values before starting its component.

Values live in RAM. A broker/coordinator restart restores the plan's initial
values. The service has no journal and does not restore runtime writes.
An interrupted write may have reached the service even if its reply was lost;
read the current value after reconnecting rather than blindly retrying an old write.

Limits: 4096 declared blocks, 256 bindings per component or paths per subscription,
1024 subscriptions/external sessions and 64 MiB of current encoded values in total.
Individual blocks and catalogues must fit the existing transport and 1 MiB CH/2
frame limits. Status reports expose block roots, reader counts and storage bytes;
reading values requires the block's read right.

## CLI

From an application's root with a compiled plan and running broker:

```sh
export OSE_PARAMETER_TOKEN='example-local-token-change-me'
ose parameter list --client operator
ose parameter get machines/press_1/detection --client operator
ose parameter set machines/press_1/detection --client operator --file detection.json
ose parameter watch machines/press_1/detection --client operator --count 3
```

The application CLI provides automatic client, token, plan and endpoint defaults:

```sh
ose app parameter list
ose app parameter get machines/press_1/detection
ose app parameter get '/detection$'
ose app parameter get
ose app parameter set --file parameters.json
ose app parameter set machines/press_1/detection --file detection.json
ose app parameter watch machines/press_1/detection --count 3
ose app parameter sync --dir ./parameters
```

Application selection is `ose app --root DIR`, then `OSE_APP_ROOT`, then the
current directory. These commands default to client `operator`; set
`OSE_PARAMETER_CLIENT` or pass `--client NAME` to choose another identity.
On every invocation the token is loaded for that client from `build/plan.json`,
or the first existing `deployment.local.json` / `deployment.json` before a build.
The first existing configuration is authoritative even when it has no token.
Explicit `OSE_PARAMETER_TOKEN` values, including an empty token, take precedence.
Tokens stay inside the command's process and are never printed; sourcing
`setenv.sh` only selects paths and enables completion.
`--token-env` selects a manually managed environment variable.
Relative filenames follow the application directory. The sync storage option
`ose app parameter sync --dir DIR` follows the caller's current directory;
its plan and endpoint still follow the application directory.
All service and TLS options remain available. Use `ose app parameter --help`.

### Export and restore blocks by path

The `ose app parameter get` command always includes paths in its output. Single-block and
multiple-block exports use the same JSON shape:

```json
{
  "machines/press_1/detection": {
    "enabled": true, "threshold": 85.0, "hysteresis": 5.0, "window_ms": 200
  }
}
```

Use `ose app parameter get PATTERN` to search paths with a Python regular expression.
The pattern is optional: `ose app parameter get` alone exports every listed block.
Use `ose app parameter get --exact PATH` to select
one literal path. Quote patterns to keep the shell from expanding them.
Anchor with `^` / `$` when needed; for example `^machines/press_1/` selects one
machine and `/detection$` selects detection blocks. Every selected block must be
readable. A pattern matching nothing produces `{}`; invalid patterns fail.

`ose app parameter set --file FILE` reads the embedded paths, without an `--all` option:

```sh
ose app parameter get > parameters.json
ose app parameter set --file parameters.json
ose app parameter get '/detection$' | ose app parameter set --file -
```

The ordinary CLI supports `ose parameter get --export [PATTERN] --client operator`
and `ose parameter get --export --exact PATH --client operator`. Export mode
interprets the positional argument as a pattern, or selects all paths when omitted.
`ose parameter get --client operator` with no path also exports all blocks.
For compatibility,
`ose parameter get PATH` without `--export` still returns only the value.
An explicit path on `set` likewise continues to accept a bare block, rather than
a path-keyed export. `watch` keeps its existing `{path, value}` event format.

To import all `*.json` files in a directory and its subdirectories:

```sh
mkdir -p parameters/machines
ose app parameter get '^machines/press_1/' > parameters/machines/press_1.json
ose app parameter get '^machines/press_2/' > parameters/machines/press_2.json
ose app parameter set --dir parameters
```

Every file must contain a path-keyed export, with one or several complete blocks.
Names of files and directories do not determine parameter paths. Files are read
in alphabetical order of relative paths, including hidden `*.json` files; other
extensions are ignored. Symlinked subdirectories are not traversed. A missing directory
or one containing no matching files is an error. Duplicate parameter paths across
files are rejected, even when values are identical, and the error names both files.
Malformed files and duplicate JSON keys are also errors. The whole tree is loaded
and all blocks are validated before the first write.

`--dir` is mutually exclusive with `--file` and a positional parameter path.
`--exact` is a selection option for `get` and `sync`, not an import option for `set`.
The ordinary CLI also supports
`ose parameter set --dir parameters --client operator`.

Imports describe and validate every block before the first write; empty imports
are valid no-ops. They replace complete blocks, and each path needs write access.
Reads do not form an atomic snapshot; writes are sequential, not transactional.
A service or network failure can leave a partially applied import. The error
names the failed block and lists acknowledged writes; a lost reply can leave the
failed block's state uncertain, so read current values before retrying. Grouped
imports use a 16 MiB limit per JSON input (including a grouped export or stdin).
Directory imports additionally limit the sum of selected file contents to 64 MiB.
Use `--max-json-size BYTES` and `--max-total-json-size BYTES` to override these
positive limits on `set`. All blocks are still validated before the first write.
These limits concern JSON text, not the encoded SDL frame size; the existing
transport/CH/2 frame limits and service storage budget remain in effect.

For `set`, `--file -` reads a complete JSON block from stdin instead of a file:

```sh
ose app parameter set machines/press_1/detection --file - <<'JSON'
{"enabled": true, "threshold": 85.0, "hysteresis": 5.0, "window_ms": 200}
JSON
# Pipes and redirected files work as well:
cat detection.json | ose app parameter set machines/press_1/detection --file -
```

The ordinary `ose parameter set ... --client operator --file -` command
supports the same input. File and stdin reads are bounded to 16 MiB by default and reject
duplicate JSON keys; the service applies the same SDL validation to both.

The CLI reads `build/endpoint.json` and finds SDL through `build/plan.json`,
`SDL_ROOT`/`--sdl-root`, or an installed Python runtime. Use `--host` and `--port`
for explicit endpoints. `--token-env NAME` selects another token environment
variable. Distributed access uses `--distributed --tls-ca ... --tls-cert ...
--tls-key ...`; the certificate CN must match `--client`. Explicit loopback
development may use `--distributed --insecure` with a configured token.

### Synchronize a tree of unit parameter files

On Linux/Termux, install the optional file watcher from the OSE project root:

```sh
python3 -m pip install '.[sync]'
ose parameter sync --dir ./parameters --client operator
ose parameter sync '^machines/press_1/' --dir ./parameters --client operator
ose parameter sync --exact machines/press_1/detection --dir ./parameters --client operator
```

Use `ose app parameter sync --dir ./parameters` for the application's default
client and token. The sync directory is resolved before entering the application,
so `--dir .` uses the terminal's current directory. Run it in a separate terminal
or under a supervisor; Ctrl+C and SIGTERM close watchers and connections.

To tie the synchronizer to an execution, use:

```sh
ose run --params-sync parameters --duration 60
ose app run --params-sync parameters --params-client maintenance --params-initial app
ose distributed-local --params-sync parameters --duration 60
ose app distributed --params-sync parameters PROFILE=cluster
```

These real-time launchers select the endpoint actually opened by the current
execution, including an automatically assigned port. The synchronizer starts
after component readiness; initial restoration can follow `on_start` and the
first callbacks. Existing files win by default; `--params-initial app` instead
exports application values. Invalid files retain the synchronizer's usual
per-file diagnostics and do not themselves terminate the application.
An unexpected synchronizer process exit stops the application; duration and
interruption stop the synchronizer and all other owned processes.

`--params-client` defaults to `OSE_PARAMETER_CLIENT` or `operator`.
`OSE_PARAMETER_TOKEN`, including an explicitly empty value, takes precedence
over the selected client's token in the current plan. `--params-token-env NAME`
selects an explicitly managed alternative variable. The sync directory follows
the launch directory, even with `ose app --root`. The managed option requires
the same optional watcher and is rejected in simulated mode.

Each path maps to one file: `machines/press_1/detection` becomes
`parameters/machines/press_1/detection.json`. The file must contain exactly one
path-keyed export, and the embedded path must match the filename. Complete SDL
blocks and the usual duplicate-key validation apply. Symlinks are not followed.
Unknown files are reported; files outside the selection are left alone.

By default (`--initial files`), every connection applies valid existing files
to OSE and creates missing files from OSE. This includes reconnecting after a
service restart, restoring the file configuration. `--initial app` explicitly
replaces the selected files with the application's current values on every
connection instead. The client needs read and write rights on the selected paths.

File changes are detected with inotify; events for a path are grouped for 150 ms
by default (`--debounce SECONDS`). A different `st_mtime_ns` triggers validation
and publication. OSE subscriptions supply initial values and later changes to
update files. Cached timestamps and decoded values prevent echo loops. Watching
directories handles editor saves by rename, newly created subdirectories and
directory move-ins. A deleted managed file is recreated from OSE.

There is no periodic tree scan or per-block `get` loop. A tree scan runs at
startup, on directory changes and after an inotify queue overflow. Subscriptions
use the existing 250 ms long polls and split selections into groups of at most
256 paths. One control connection is separate from the subscription connections.

Invalid files remain untouched until corrected; other paths continue to sync.
Transient file errors are retried, and disconnected sessions reconnect after
2 seconds. JSON files are limited to 16 MiB, adjustable with `--max-json-size`;
there is no aggregate import limit for this block-by-block daemon. File writes
use a temporary `.tmp` followed by replacement. Diagnostic logs go to stderr
without parameter values or tokens.

This simple service assumes one usual writer, without collision detection,
revision checks or persistent metadata. A local edit awaiting publication has
priority over an application notification. An edit preserving the exact previous
mtime may be ignored; `touch FILE` forces publication. Runtime values reach the
parameter service, without a guarantee that every component has consumed them or
that file restoration precedes component `on_start`.

See the [sync design](parameter-sync-design.md) for the event handling and
implementation details.

## External SDKs

Python exposes an async client independently of a model or checkout (put the SDL
Python runtime on `PYTHONPATH`):

```python
from ose.parameters.client import ParameterClient

async with await ParameterClient.connect("127.0.0.1", 7000, "operator", token=token) as client:
    block = await client.block("machines/press_1/detection", DetectionParams)
    value = await block.get()
    value.threshold = 85.0
    await block.set(value)
    await client.subscribe([block.path])
    update = await client.next()  # None after an empty bounded long-poll
```

Without `DetectionParams`, a Python block reads/writes JSON-shaped dictionaries.
Distributed Python clients pass a client SSL context from `ose.transport.secure.contexts`
and `distributed=True` to `connect`.

Rust: `sdl_ose_client::parameters::ParameterClient` supplies `connect`,
`connect_tls`, `list`, `describe`, `get`, `set`, `subscribe`, `next` and `unsubscribe`.
`describe` returns the root/catalogue; encode/decode raw SDL bytes with the
application's generated SDL runtime. The crate keeps its dependency-free Cargo
manifest. Native TLS loads OpenSSL 1.1/3 at runtime and verifies the CA and the
single exact `coordinator` certificate CN.

C: include `ose_parameters.h` and `ose_client.h`. Use
`cenv_parameters_connect` (NULL TLS paths for token-authenticated local TCP),
`cenv_parameter_block`, `cenv_parameter_get` and `cenv_parameter_set` with registered
SDL types. Release returned messages with `type_free`; free blocks before their
borrowed client. `cenv_parameters_request` exposes subscriptions and other RPCs;
`cenv_parameter_decode` decodes a returned update for its block. Packet strings and
bytes remain borrowed until `cenv_parameter_packet_free`; extracted text fields
are owned and use `cenv_string_free`. Link `ose_client.c` and the native Rust SDK.

Java: `org.sdlose.client.ParameterClient` uses the existing `client-java` JNI
library. `client.block(path, DetectionParams.class)` provides typed `get`/`set`;
`subscribe`/`next` deliver packets decoded with `block.decode(packet)`. The
seven-argument `connect` accepts token and CA/certificate/key paths for native TLS.

Ada: `Ose_Parameters` provides a controlled client, `Request`, `List`,
`Subscribe`, `Next` and `Unsubscribe`. Instantiate `Ose_Parameters.Typed` with
the SDL message type, root, catalogue and encode/decode functions to obtain typed
`Open`, `Get`, `Set` and `Decode_Update`. An empty `Next` packet has an empty path.
`client-ada` includes these units and the same native TCP/TLS engine.

The synchronous native/Java/Ada channels serialize requests. A long-poll is bounded
to 250 ms; clients drive their own polling loop. Finish calls before closing their
client. Reconnect explicitly and resubscribe to receive current state.

## Wire operations and CH/2

Parameter RPCs use CENV/2 with ordinary SDL payloads, without S2PS envelopes.
`parameters_open` selects an authenticated client session. List/describe/get/set,
subscribe/next/unsubscribe operations use exact paths; describe returns the
catalogue as binary UTF-8. Subscribe supplies a `paths` array; next returns one
`path` and SDL payload, or `empty: true`. Component sessions use existing identity
and worker-generation checks instead of `parameters_open`.

CH/2 shared services use `U` (engine → component: alias, catalogue, SDL payload),
`S` (component → engine: alias, SDL payload) and `A` (engine → component: byte 1
for success, or byte 0 plus UTF-8 error). Their layouts are specified in the
[component service protocol](../sdk/protocol.md). Unlike CH/2 business
publications, parameter SET has no application-ID field.

Initial readable updates follow INIT before the start hook; their count comes
from the bindings. Every CH/2 component sends READY byte 0x02, whether or not
it uses shared parameters. A setter waits for its RESULT during initialization
or an active input callback. Updates never interrupt a callback. Parameter
writes are outside the deferred inbox withdrawal journal.

## Simulation

The local simulation engine uses the same blocks and access rules. Initializers
run in stable graph order when shared parameters exist. All calculations in a wave
read the pre-wave state; component writes are collected and become visible after
the whole wave succeeds, in component order. Repeated writes to a block by one
component coalesce to its last value for that wave.

Scenario events may update complete blocks:

```json
{"version": 1, "events": [
  {"at": 1.0, "parameter": "machines/press_1/detection",
   "value": {"enabled": true, "threshold": 85.0, "hysteresis": 5.0, "window_ms": 200}}
]}
```

Events are validated before starting components and applied before calculations
at their timestamp. Traces include parameter paths and payload hashes, and status
counts parameter changes. Outputs remain reproducible across worker counts for
deterministic callbacks. Live recording/replay of parameter changes is a separate
extension; ordinary output recording retains its existing behavior.

See the runnable [shared parameter example](../examples/shared_parameters/README.md).
