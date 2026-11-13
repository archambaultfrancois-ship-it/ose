# Application environment and commands

For a first walkthrough with Vibration Watch, follow the
[beginner quick start](quick-start.md). This reference covers shell setup,
application commands, recording options and parameter import/export.

## Shell setup

For local application development in Bash, source the framework environment in
each terminal from the application repository root. The current directory is
the default application:

```sh
cd /path/to/application
source /path/to/ose/setenv.sh
# Or select another application before sourcing:
export OSE_APP_ROOT=/path/to/application
source /path/to/ose/setenv.sh
ose app env
```

This exports `FRAMEWORK_ROOT`, `SDL_ROOT`, `OSE_ROOT`, and `OSE_APP_ROOT`,
and prepares source and dependency paths. It detects Python 3.10+ in `PATH`,
preferring `python3` when compatible and otherwise using a versioned command
such as `python3.12`. The detected path is exported as `PYTHON`; every command
and Make uses this same interpreter. `make python-info` shows the selection.
Setup fails immediately if no compatible interpreter is found. Run `make setup` from the
repository root to install Python dependencies into `build/python-deps` using
that interpreter. Sourcing again does not duplicate paths or install anything.
Interactive Bash terminals also enable `ose` completion for commands,
options and option choices. Press Tab after `ose`, `ose app parameter`,
or an option such as `ose run --mode` followed by a space.
To switch applications in the same terminal, run `unset OSE_APP_ROOT`, enter the
other repository, and source the script again.

`ose-editor` opens the selected application's model editor at
`http://127.0.0.1:8082`, including when invoked from another directory:

```sh
ose-editor
ose-editor --port 8083
# Optional: prepare or repair dependencies without starting the editor.
ose-editor setup
```

The first launch installs missing editor dependencies into the checkout's
`build/python-deps`; subsequent launches reuse them. `ose-editor setup` prepares
or repairs those dependencies explicitly. `--help` and `--check` never install
anything. The editor, console, application and Make commands all use the same
detected Python from `PATH`. The bundled frontend needs no Node.js build.

`ose-ws` launches the selected application's web console through the framework's
`ose-ws/tools/console.py`, following the same checkout-launcher convention:

```sh
ose-ws
ose-ws --port 8081
ose-ws check
ose-ws setup
```

The first server launch prepares missing Python dependencies; `setup` also
builds the SDL recording/filter services. Help, `init` and `check` do not prepare
dependencies. The launcher supplies `--root "$OSE_APP_ROOT"`, so the default
configuration and login token remain in the selected application even when
launched elsewhere. Configuration is created automatically only for `serve`
without an explicit `--config`. Runtime state and captures follow the calling
directory or `--working-directory`. Parameter credentials are resolved afresh
from the configured application's current plan or local deployment, with
explicit environment credentials taking precedence. Applications need no
custom `web_console.py`; see the [console guide](../ose-ws/README.md).

## Recording commands

With Vibration Watch, run the application and recorder in separate terminals:

```sh
cd /path/to/ose/vibration-watch
source ./setenv.sh
app-run
# In another Bash terminal, source setenv.sh again, then:
ose app record
```

At launch, `ose app record` checks the recording and filter modules with the same PATH Python.
It automatically installs/builds only missing services, then opens the console.
Later launches reuse them; `--help` skips preparation. The first preparation
uses the existing Makefile targets and may require pip/network access for MCAP,
plus Cargo and a C compiler for the native filter extension. `ose app record deps`
remains available to manually rebuild/reinstall both services.

`ose app record` uses the application's `build/endpoint.json` when present and accepts all
record CLI options, including `--host`, `--port`, TLS settings, and `--command`.
In the recorder, enter `select alarm.event`, `filter alarm.event machine_id == 2`,
then `start` on separate lines. `start` generates a fresh timestamped filename.
Finish with `stop` and `quit`; use `ose app record info FILE`, `ose app record inspect FILE --limit 5`,
or `ose app record inspect FILE --check` afterwards. Recording paths are relative to the application
directory. Each invocation of `ose app record` opens an independent recorder.
Sessions have the informational label `recorder` by default; use
`ose app record --label moteur-1` (or `-l moteur-1`) to customize it. The console's
`label` command shows or changes it while stopped. Labels appear in capture
status and file metadata, and can be shared by independent sessions.
In the console, `max-size 100MiB` enables numbered file rotation and `cycle 3`
retains only the three latest files, with indices that always increase. Use
`cycle off` and `max-size off` to disable them while stopped.
`ose app record inspect` displays every selected message unless `--limit N` is supplied.
Use `--summary` for counts by topic, log-time bounds and `duration_seconds`, or `--check` to also
validate selected message bodies. Both modes accept `--topic`, `--from`, `--to`
and `--format text|json|table`; `--limit` only limits message display.
Time bounds are absolute timestamps (`1700000000s`) or offsets from the earliest
recording log time when prefixed with `+` (`+1s`). Use `--duration 4s` instead of
`--to` to select four seconds from `--from`, or from the recording beginning
when `--from` is omitted. Reported `duration_seconds` spans the selected messages
and is zero for an empty selection or a single message.
Options with values also accept `=`, such as `--from=+1s --duration=4s`.
Short example: `ose app record inspect FILE -cs -b +1s -d 4s -f table`.
Use `ose app record inspect FILE --check --format json` for a JSON validation report.

[`sdl-recording transform`](../sdl/docs/recording_transform.md) combines filtering,
concatenation/rebasing and splitting, preserving PCAPNG or MCAP input formats:

```sh
sdl-recording transform capture-*.pcapng --concat --filter 'state == 1' \
  --topic 'alarm.*' --split-size 100MB --output 'alarms-{index:04d}.pcapng'
```

Add `--dry-run` to validate the inputs and preview output files without writing.

## Application commands

The application shortcuts are `app-cd`, `app-build` and `app-run`.
`app-cd` changes the terminal directory; the others call `ose app build` and
`ose app run`. All workflow logic lives in Python and also works without sourcing
when the `ose` package is installed:

```sh
ose app validate
ose app build SCENARIO=normal
ose app run --duration 60 SCENARIO=normal
ose app run --params-sync parameters --duration 60
ose app simulate DURATION=10
ose app distributed --duration 300
ose app make -- -j4 build
ose app --root /path/to/application env
```

Application selection is `--root`, then `OSE_APP_ROOT`, then the current directory.

For a generic, offline installation of OSE, SDL and the web console outside the
checkout, see the [framework release guide](release.md). `make release`
builds a platform-specific archive with all five SDK languages and all features
selected by default; missing target compilers produce installation warnings.

Build, validate and simulate use the application's Makefile targets.
`ose app distributed` builds first, then OSE supervises a loopback coordinator
and the workers declared in the compiled distributed plan. This command selects
the application's `PROFILE=cluster` automatically; a local profile is rejected. Use
`--build-target TARGET` if it has a dedicated build target. The duration defaults
to 300 seconds after readiness. Application demonstrations remain available
through `ose app make demo-distributed`.
Coordinator, worker and synchronizer output appears live on stderr with the
process name in brackets. Original output also remains in
`build/distributed-logs/*.log`; the final JSON status goes to stdout.
`ose app run` builds first, then executes `build/plan.json` until Ctrl+C by default;
`app-run DURATION=60` remains accepted. Both real-time launchers accept
`--params-sync [DIR]` to own a parameter synchronizer (default: `parameters`),
using the current execution's endpoint. Its unexpected exit stops the
application; duration, Ctrl+C and SIGTERM stop the synchronizer too. Sync storage
follows the launch directory. See [parameter synchronization](../ose/docs/parameters.md#synchronize-a-tree-of-unit-parameter-files).
Components run in the directory where you launch `ose app run` (or `app-run`), so
relative file reads and writes in callbacks use that directory. Use
`--working-directory PATH` to select another component directory, relative to the
launch directory. Builds, plans, scenario inputs, endpoint files and shutdown
status stay in the application directory; relative `--trace` output follows the
launch directory. Low-level `ose run` uses each component's plan `cwd` unless
`--working-directory` overrides it.
`ose app env` shows paths and the Python interpreter without credentials.

## Parameter commands

Application parameter commands resolve the default plan and endpoint from the
application directory, even when invoked from another directory:

```sh
ose app parameter list
ose app parameter get machines/press_1/detection
ose app parameter get '/detection$' > detection-export.json
ose app parameter get > parameters.json
ose app parameter set --file parameters.json
ose app parameter set machines/press_1/detection --file detection.json
ose app parameter watch machines/press_1/detection --count 3
```

These commands use client `operator` by default. Set `OSE_PARAMETER_CLIENT` or
pass `--client NAME` to choose another identity. Each invocation resolves the
selected client's token from `build/plan.json`, or the first existing
`deployment.local.json` / `deployment.json` before a build. An existing plan is
authoritative even without a token. Explicit `OSE_PARAMETER_TOKEN` values,
including an empty value, take precedence; credentials are never printed or
exported back into the terminal. `--token-env` selects a manually managed variable.
Relative JSON files resolve from the application; `parameter sync --dir DIR`
resolves its storage directory from the caller's terminal instead. Plan/endpoint,
host/port and TLS options remain available. Use `ose app parameter --help` for actions.
`ose app parameter get` always exports a JSON object `{path: block}`, including for a single
selection. Its optional positional argument is a Python regular expression that
searches block paths; no argument selects every listed block. Use
`ose app parameter get --exact PATH` for a literal path. Quote patterns to avoid shell
expansion.
Every selected block must be readable. `ose app parameter set --file FILE` imports this format
without requiring a separate path or an `--all` option. Supplying an explicit path
to `ose app parameter set` still writes a bare block. The ordinary
`ose parameter get PATH` keeps its value-only output for existing scripts;
use `get --export [PATTERN]` for the same path-keyed pattern export as the application
commands, or `get --export --exact PATH` to export one literal path.
Writes replace complete typed blocks. See the [parameter guide](../ose/docs/parameters.md).

Exports can be sent directly back to the service:

```sh
ose app parameter get '/detection$' | ose app parameter set --file -
```

To restore exports spread across a directory tree, use
`ose app parameter set --dir ./parameters`. It reads every `*.json` file recursively in
relative filename order. Each file contains `{path: block}` exports; paths come
from JSON keys. Duplicate paths or invalid files fail before any write. `--dir`
cannot be combined with `--file` or a positional path.

All imported blocks are decoded and validated before writing. Reads and writes
are sequential, without an atomic snapshot or transaction across blocks. If a
service write fails, the error lists the blocks already updated. JSON input is
limited to 1 MiB in total, including the sum of file contents in directory imports.

Use `--file -` to read JSON from stdin, with the same validation and 1 MiB limit:

```sh
printf '%s\n' '{"enabled":true,"threshold":85.0,"hysteresis":5.0,"window_ms":200}' \
  | ose app parameter set machines/press_1/detection --file -
```

Supply every required field of the block; stdin also accepts redirection or a
heredoc.

