# OSE — Orchestration, Simulation, Exécution

<p align="center">
  <img src="docs/branding/ose-banner.svg" alt="OSE — Orchestration, Simulation, Exécution" width="960">
</p>

The `ose` repository contains four projects that evolve together:

- **[SDL](sdl/README.md)** (Schema Design Language) generates message codecs
  for C99, Rust, Python, Java, Ada, and Matlab/Octave. It also provides catalogue filtering,
  recording/replay tools, and a Wireshark adapter.
- **[OSE](ose/README.md)** (Orchestration, Simulation, Exécution) orchestrates
  and executes component graphs with SDL message contracts, bounded RAM delivery, external clients,
  deterministic parallel simulation, and [distributed real-time deployment](ose/docs/distributed.md).
- **[OSE Web Console](ose-ws/README.md)** (`ose-ws`) provides an independent
  English-language web interface for diagnostics, scalar measurements, message
  captures and typed parameter editing in a running OSE application.
- **[Vibration Watch](vibration-watch/README.md)** is a runnable SDL/OSE application
  with C sensors, Rust analyzers, Python alarms, and local, simulated or distributed execution.

OSE depends on SDL's public generator and runtime APIs. SDL builds and
runs independently of OSE. Each project owns its sources, tests,
examples, documentation, dependencies, and build outputs.

See the shared [technical glossary](GLOSSARY.md) for terminology used across
SDL, OSE and the Web Console.
The shared [project logo and visual assets](docs/branding/README.md) reuse the
OSE Web Console's mark.

Clone the repository and enter its root:

```sh
git clone git@github.com:archambaultfrancois-ship-it/ose.git
cd ose
```

Paths below are relative to this repository root. The engine project is in
`ose/` inside the checkout (for example, `~/ose/ose`); SDL is in `sdl/`
(`~/ose/sdl`).

```text
sdl/                    SDL project root
  sdlc/                 SDL compiler and binding generators
  runtime/              Language runtimes and optional services
  schemas/              Shared source schemas
  tests/                Tests, fixtures, and benchmarks
  examples/             Runnable SDL examples
  tools/                Filter, recording, and Wireshark tools
  docs/                 SDL documentation
ose/                    OSE project root
  src/                  Modeling and execution engine
  sdk/                  Component and external client SDKs
  tests/                Unit and integration tests
  examples/             Component graphs and client examples
  docs/                 OSE documentation
.github/workflows/      Repository CI
ose-ws/                 Standalone Python web server and bundled Vue frontend
vibration-watch/         Application sources, scenarios, tests and deployment tools
```

Each project has a separate GitHub Actions workflow. Vibration Watch uses this
checkout's SDL/OSE framework directly and is also tested when its framework or
web console dependencies change. From the repository root, run
`make -C vibration-watch validate` and `make -C vibration-watch test`.

The optional web console is installed separately: `python -m pip install ./ose ./ose-ws`.
Run `ose-ws init --config ose-ws.json` in an application directory, then
`ose-ws --config ose-ws.json`. See its [setup and security documentation](ose-ws/README.md).

## Local application environment

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

This exports `FRAMEWORK_ROOT`, `SDL_ROOT`, `OSE_ROOT`, `OSE_APP_ROOT`, and
`OSE_PYTHON`, and adds the source runtimes and optional recording/filter modules
to `PYTHONPATH`. `OSE_PYTHON` defaults to `python3`; set it to an interpreter path
before sourcing to use a virtual environment. Sourcing again does not duplicate
the added paths. No dependencies are built or installed when sourcing.
Interactive Bash terminals also enable `ose` completion for commands,
options and option choices. Press Tab after `ose`, `ose app parameter`,
or an option such as `ose run --mode` followed by a space.
To switch applications in the same terminal, run `unset OSE_APP_ROOT`, enter the
other repository, and source the script again.

With Vibration Watch, run the application and recorder in separate terminals:

```sh
cd /path/to/ose/vibration-watch
source ./setenv.sh
app-run
# In another Bash terminal, source setenv.sh again, then:
ose app record
```

At launch, `ose app record` checks the recording and filter modules with `OSE_PYTHON`.
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

[`sdl-recording transform`](sdl/docs/recording_transform.md) combines filtering,
concatenation/rebasing and splitting, preserving PCAPNG or MCAP input formats:

```sh
sdl-recording transform capture-*.pcapng --concat --filter 'state == 1' \
  --topic 'alarm.*' --split-size 100MB --output 'alarms-{index:04d}.pcapng'
```

Add `--dry-run` to validate the inputs and preview output files without writing.

The only application shortcuts are `app-cd`, `app-build` and `app-run`.
`app-cd` changes the terminal directory; the others call `ose app build` and
`ose app run`. All workflow logic lives in Python and also works without sourcing
when the `ose` package is installed:

```sh
ose app validate
ose app build SCENARIO=normal
ose app run --duration 60 SCENARIO=normal
ose app simulate DURATION=10
ose app distributed --duration 300
ose app make -- -j4 build
ose app --root /path/to/application env
```

Application selection is `--root`, then `OSE_APP_ROOT`, then the current directory.
Build, validate, simulate and distributed use the application's Makefile targets.
`ose app run` builds first, then executes `build/plan.json` until Ctrl+C by default;
`app-run DURATION=60` remains accepted. The distributed demo defaults to 300 seconds.
`ose app env` shows paths and the Python interpreter without credentials.

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
Writes replace complete typed blocks. See the [parameter guide](ose/docs/parameters.md).

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

## Build and test

From the repository root:

```sh
make check-sdl
make check-ose
make check             # Both projects, sequentially
make test              # Both projects, verbose output
```

The root Makefile also forwards other SDL targets, such as `make test-java`
and `make test-recording`. Project-specific commands remain available:

```sh
make -C sdl test-c
make -C sdl test-recording-native
make -C ose test-java
make -C ose demo
```

See each project's README for its toolchains and optional dependencies.
OSE uses `sdl/` by default. Set `SDL_ROOT` or pass `--sdl-root` to select
another SDL project directory containing `sdlc/` and `runtime/`.
The root Makefile also accepts `SDL_ROOT=/absolute/path/to/ose/sdl` for OSE targets.
