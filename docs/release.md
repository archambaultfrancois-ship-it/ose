# Generic OSE framework releases

The release installs OSE, SDL, the optional web console and the model editor for any compatible
application. It contains no application, models, application configuration,
credentials, captures or existing build plans. Applications remain outside the
installation and use the public OSE commands or their own Makefile.

The [OSE Model Editor](../ose-editor/README.md) is included in this bundle as an
independent `editor` feature. It works on application source files without a
running OSE application; the console remains the runtime observation tool.
Its [validation record](../ose-editor/docs/validation.md) describes the current
authoring milestone and remaining complete-release qualification.

## Application and protocol compatibility

This release requires the v2 execution model. Upgrade engines, component SDKs
and external clients together, then rebuild application plans and containers.
There is no v1 execution adapter or network fallback. Application sources follow
the [model migration guide](../ose/docs/model-v2-migration.md).

| Boundary | Supported form |
| --- | --- |
| Application models and compiled plans | Version 2; each compiled component uses execution model 2. |
| Component stdio protocol | CH/2; one activating input reference per callback, explicit timers and passive Pull access. |
| Engine/client network envelope | CENV/2 exclusively; CENV/1 is rejected. |
| SDL message bodies and contracts | S2PS/2 bodies unchanged; per-message catalogue exchange, discovery and compatibility checks stay dynamic. |
| Deployment and scenario documents | Their independent version 1 is retained. |
| Diagnostic observation metadata | Its independent version 1 is retained. |
| Existing recordings | Remain readable; absent application IDs remain absent. New IDs are optional uint64 values, including zero. |

All five component SDKs are qualified in local execution and parallel simulation.
Distributed qualification covers Python/C workers, broker/direct routes, TLS,
retries and generation fencing on loopback. It does not qualify physical
multi-machine execution or Ada-specific distributed worker runs. Linux checks use
GNAT 12 and JDK 17 targeting Java 7 APIs/bytecode; execution on an actual JVM 7
is deferred. Android has no GNAT in the qualification environment, and JDK 21
requires an explicit `OSE_JAVA_LOCAL_RELEASE=8` override for local Java builds;
that override does not qualify the default Java 7 target.

See the [transition qualification record](../ose/docs/cenv2-transition.md) for
tested environments, commands and remaining deferred work.

## Build an archive

From the repository root:

```sh
make release
```

The builder needs shared CPython 3.10+, pip, a C compiler, Python development
headers, `readelf` (binutils) and Rust/Cargo. Private interpreter packaging
currently supports Linux and Android/Termux. It collects runtime Python wheels,
vendors the Rust recording dependencies and builds both native services and the Python filter gateway for
this host. It also packages this CPython version, its standard library, headers
and linked libraries such as OpenSSL, SQLite, libffi and compression libraries,
including external native dependencies of the bundled Python wheels.
The committed
frontends must already be built; Node.js is only needed to rebuild them with
`make frontend-ws` and `make frontend-editor`.
An incomplete dependency repository or failed native build fails release
creation: the builder must deliver all resources advertised by the release.

The result is `build/releases/ose-<platform>-cp<python>.sh`, with a companion
`.sh.sha256` file. This executable shell bundle contains the complete archive;
it replaces the former `.tar.gz` distribution for the same target. Its manifest
records the Git revision, whether the source was
modified, target platform/Python, dependency requirements and file hashes.
There is no installer version-selection option; the archive defines the release.
The `cp310`, `cp313`, etc. suffix identifies the **bundled** Python and its native
dependencies. The target machine does not need the same system Python, or any
system Python at all. Build a separate bundle for each OS/architecture and use a
compatible target OS/ABI; the system C library and ELF loader are not bundled.

For an offline build, provide a directory containing all required wheels and
populate Cargo's cache beforehand:

```sh
make release RELEASE_OPTIONS="--offline --packages-dir /path/to/wheels"
```

The archive includes the engine's Python modules, component/client SDKs, SDL
generator and runtimes for Python/C/Rust/Java/Ada, recording/filter services,
compiled frontend, private CPython, dependency wheels and Cargo vendor sources.
CPython's license and available distribution copyright notices for bundled
libraries are retained. Application and framework tests, examples, frontend
development sources, Git data, development environments,
compiler caches and MATLAB gateways are excluded. Shared C/Rust runtime and
generator files may still be needed when selecting a single component language.

## Install

Run the self-extracting installer directly, or explicitly through `sh` on
Android/Termux and filesystems that do not permit execution:

```sh
sh ose-<platform>-cp<python>.sh

# All existing installer options are accepted.
sh ose-<platform>-cp<python>.sh --prefix /path/to/ose --bin-dir /path/to/bin
```

The bundle verifies its embedded archive, extracts it into a private temporary
directory, runs `ose-install` with the original arguments and working directory,
then removes the temporary files. Cleanup also runs after installer failures and
handled termination signals. `TMPDIR` selects the temporary parent directory.
It requires the usual shell utilities, `mktemp`, `tail` and gzip-capable `tar`.
SHA-256 checking uses sha256sum, shasum, OpenSSL or a host Python when available;
without any of these it emits a warning before extraction. The embedded installer
then verifies every resource using its bundled Python. The companion `.sh.sha256` file can additionally
be checked before execution with `sha256sum -c`.

Installation is offline, even without `--offline`: it never contacts an index,
downloads a compiler or invokes a system package manager. It starts the bundled
Python and installs a complete private Python environment, including the
interpreter and standard library. It bootstraps pip directly from a bundled
wheel; system Python, pip and ensurepip are not required. A native launcher finds
its private libraries relative to itself and keeps `sys.executable` usable by
child processes. It does not change shell
startup files. Add the command directory to PATH when it is not already there.

| Option | Default | Meaning |
| --- | --- | --- |
| `--prefix PATH` | `~/.local/opt/ose` | Framework resources and private Python environment. |
| `--bin-dir PATH` | `~/.local/bin` | Links to the installed commands. Existing unrelated commands are kept with a warning. |
| `--profile sdk\|runtime` | `sdk` | SDK includes compilation and execution resources. Runtime omits the SDL generator and SDK sources. |
| `--languages all\|LIST` | `all` | Python, C, Rust, Java, Ada. LIST is comma-separated. The engine always needs Python. |
| `--features all\|none\|LIST` | `all` | `web`, `editor`, `sync`. LIST is comma-separated. Editor requires SDK. Recording and filtering are always installed, including with `none`. |
| `--packages-dir PATH` | Bundled `packages/` | Alternate local wheel repository. No network fallback. |
| `--offline` | Always offline | Accepted for explicit invocation. |
| `--check-only` | Off | Verify the package and report prerequisites without writing the installation. |
| `--dry-run` | Off | Show the selection and diagnostics without installing. |

The old `recording` and `filter` feature names remain accepted for compatibility;
they no longer control service installation. The installation report lists both
under `services`, separately from selectable `features`.

The checkout's Python 3.10+ PATH requirement does not apply to the target machine:
the offline bundle includes its own compatible interpreter. Its installed
`.venv` directory contains that complete private runtime and is retained when
checkout virtual environments are removed from the development workflow.

The bundled interpreter takes precedence over system Python, `PYTHONHOME`, user
site-packages and `OSE_INSTALL_PYTHON`. The installer starts it in isolated mode,
so a previously sourced application environment cannot alter installation.
Application commands still support `PYTHONPATH` for their own modules, while
keeping their private interpreter home and disabling user site-packages.
`OSE_INSTALL_PYTHON` is a recovery override only if the bundled interpreter cannot
start (for example, an archive for the wrong architecture); prerequisite issues
remain warnings and can leave capabilities unavailable. An empty destination is accepted, as is an
existing installation carrying its installation report. Rerunning the installer
refreshes owned resources and the private environment; deselected components
are removed. It leaves application directories and runtime state alone. An
unrelated nonempty destination is rejected.

The default SDK/all selection includes the editor. `--features editor` installs
it without requiring the console's `web` feature. Runtime/all selections omit
the editor with a diagnostic; runtime/explicit-editor selection fails before
installation, since authoring validation needs SDL generator and SDK resources.

The installed layout is:

```text
<prefix>/
  bin/                       # ose, sdl-recording; ose-ws, ose-editor, sdlc as selected
  .venv/                     # private Python executable, stdlib, native libraries and dependencies
    bin/python               # stable application entry point; also python3 and python3.x
    runtime.json             # bundled interpreter version and native library list
    licenses/                # distribution notices for copied native libraries
  framework/ose/             # engine and selected SDK files
  framework/sdl/             # generator, runtimes, native filtering and recording
  framework/ose-ws/          # optional server and embedded frontend
  framework/ose-editor/      # optional source editor and embedded frontend (SDK)
  packages/                  # offline wheel repository retained for repairs
  cargo-vendor/              # offline third-party Rust sources in SDK profile
  cargo-home/config.toml     # Cargo source replacement and offline mode
  install-report.json
  release-manifest.json
  installation.log
```

Commands select these resources automatically and preserve the calling
directory. Their child processes inherit the installed SDL paths and offline
Cargo repository. They do not require a checkout or sourcing `setenv.sh`.
Python dependencies of application handlers are the application's responsibility;
the bundled wheels and vendor tree cover the framework, not arbitrary user code.

```sh
# Compile and run a separate application whose Makefile has a build target.
ose app --root /path/to/application build
ose app --root /path/to/application run

# The direct model/deployment interface also uses the installed SDK.
ose build --model model.json --deployment deployment.json

# Observe an already-running application.
ose-ws init --config /path/to/application/web.json
ose-ws --config /path/to/application/web.json

# Edit application sources without starting OSE.
ose-editor --root /path/to/application
```

The application is compiled after installation. Existing plans refer to absolute
paths and must be regenerated when the application or framework moves. A runtime
installation therefore requires an application prepared with valid target paths;
it does not make an arbitrary developer build relocatable.

## Prerequisites are warnings

Missing or unusable compilers, missing Java runtime, an incompatible Java 7
compiler baseline, unsupported inotify synchronization or a different target
platform are warnings. A failed dependency installation is also
a warning: sources, SDKs and wheels remain installed, the report records the
incomplete capability, and installation returns success. An attempt to use that
capability can fail until the prerequisite is supplied. Native filter and Python
wheels are specific to the platform and the private Python ABI recorded in the archive.

If the bundled interpreter cannot start and no usable recovery/system Python is
available, a POSIX-shell fallback still copies the complete payload (including
private Python) and installs deferred launchers. It uses sha256sum
for integrity checks when available. Selection is deferred in this case and
reported as such; use a compatible bundle or rerun the installer with a usable
recovery Python to apply selection, verify the manifest and install dependencies. The fallback
requires normal shell utilities (`cp`, `mkdir`, `ln`, `chmod`, `readlink`, `rm`). Python
must be usable, either bundled or supplied for recovery, before any OSE command can run.

Corrupt resources, invalid options, a destination overlapping the extracted
release, or filesystem failures preventing resource installation are errors.
The installer never claims that an incomplete Python dependency setup succeeded:
read `python_dependencies`, `capabilities` and `warnings` in `install-report.json`
for the result. Capability probes check that the selected Python services import
successfully; they are not a complete application or toolchain qualification.

## Validation

```sh
make test-release
python3 tools/release/check.py build/releases/ose-<platform>-cp<python>.sh
```

The first command covers maximal defaults, warning-only prerequisites, resource
selection, integrity failures, path handling, command conflicts, reinstallation,
interpreter relocation, upgrades from an old system-backed environment and the
recovery fallback. The second installs a real archive outside the checkout with
no usable system Python and an unreachable pip index, verifies private
stdlib/native library loading, compiles two independent applications, executes
C/Rust/Python components in simulation and real time, authenticates to
the installed console and creates a filtered capture in the launch directory.
It also starts the installed editor, previews/applies a component rename in an
external source project, validates the unsaved revision and saves ordinary OSE
files using only installed framework resources.
The capture check verifies preservation of application ID zero, and installed
Rust component/client SDK unit tests run offline against the bundled resources.
Separately, `ose/tests/installed_sdk.py` checks the installed wheel outside the
source path, builds selected language SDKs and executes one CH/2 timer callback
per selected language. Its default selection includes all five languages.
