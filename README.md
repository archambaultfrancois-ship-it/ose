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

New to OSE? Follow the **[beginner quick start](docs/quick-start.md)**
to run Vibration Watch, compare simulations, inspect live measurements and change
an alarm threshold. Optional workshops cover the web console, recording/replay
and distributed component replacement.

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

A [local autonomous CI watchdog](docs/ci-watchdog.md) can monitor GitHub Actions
and use Codex to diagnose, test and publish bounded repairs.

Each project has a separate GitHub Actions workflow. Vibration Watch uses this
checkout's SDL/OSE framework directly and is also tested when its framework or
web console dependencies change. From the repository root, run
`make -C vibration-watch validate` and `make -C vibration-watch test`.

## Local application environment

For Bash development, source the framework environment from your application
root in each terminal:

```sh
cd /path/to/application
source /path/to/ose/setenv.sh
ose app env
```

This selects the application, prepares source paths and enables CLI completion
in interactive Bash. The shortcuts are `app-cd`, `app-build` and `app-run`.
See the [application environment reference](docs/application-environment.md)
for interpreter selection, working directories, recording commands and parameter
import/export. The [quick start](docs/quick-start.md) demonstrates these commands
with Vibration Watch.

For installation outside the checkout, use the
[offline framework release guide](docs/release.md).

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
