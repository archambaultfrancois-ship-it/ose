# OSE Model Editor

The OSE Model Editor is the graphical authoring application for OSE
application models, deployment documents and SDL source schemas. It is an
independent subproject named `ose-editor`, alongside `ose`, `sdl` and `ose-ws`.

Its interface combines an editable component graph, a declaration explorer,
property inspectors, comparison tables and synchronized source editors. The
complete source language remains accessible through structured controls,
including advanced SDL declarations and deployment settings.

The editor works with application source files without requiring a running OSE
application. The separate [OSE Web Console](../ose-ws/README.md) continues to
provide runtime observation and live parameter changes.

## Current status

The initial implementation provides the project service, Vue workspaces,
source-preserving JSON/SDL edits, structured descriptor controls, previewed
commands, undo/redo, validation of unsaved snapshots and recoverable saves.
It is an authoring milestone, not yet the fully qualified initial release in
the specification. Advanced cross-format refactorings, comprehensive XSD
authoring and desktop browser performance qualification remain tracked in the
[validation record](docs/validation.md). Performance figures in the
specification are acceptance targets, not measured desktop results.

- [Functional and technical specification](docs/specification.md): approved
  design baseline, architecture, workflows, persistence and acceptance criteria.
- [Language coverage inventory](docs/language-coverage.md): OSE/SDL constructs,
  their structured editing surfaces and required qualification evidence.
- [Shared terminology](../GLOSSARY.md): SDL, OSE, contracts and execution concepts.

## Run from the checkout

From the repository root, select Python 3.10+ and start the editor against the
application. The first launch creates `ose-editor/.venv` and installs its runtime
dependencies using `requirements.lock`; subsequent launches reuse that environment:

```bash
export OSE_PYTHON=python3.12
source vibration-watch/setenv.sh
ose-editor
```

To prepare the environment separately, run `ose-editor setup` before launching.
Initial setup needs pip/network access. The bundled frontend needs no Node.js
build. `ose-editor --help` and `ose-editor --check` never install dependencies,
and sourcing `setenv.sh` does not create an environment or install anything.

For a manual installation with the optional backend test dependencies:

```sh
python3 -m venv ose-editor/.venv
ose-editor/.venv/bin/python -m pip install -c ose-editor/requirements.lock ./ose './ose-editor[test]'
ose-editor/.venv/bin/ose-editor --root ./vibration-watch --sdl-root ./sdl
```

In later Bash terminals, source the application environment and use the same command:

```bash
cd vibration-watch
source ./setenv.sh
ose-editor
```

It opens the selected `OSE_APP_ROOT` without changing the application interpreter.
Set `OSE_EDITOR_PYTHON` to override the editor's Python environment. That interpreter
is used unchanged during launch; `ose-editor setup` explicitly installs dependencies
into it. Setup and launch preserve the application's Python environment and the
calling directory.

Open `http://127.0.0.1:8082` and enter the access token from the file printed
by the command, normally `<application>/.ose-editor/access.token`. The token
is never placed in the URL. One editor process owns a project at a time.

`--model FILE`, repeated `--deployment FILE`, repeated `--source-root PATH`,
`--port PORT`, `--create` and `--check` customize project opening. Without
explicit deployment options the editor reopens registered profiles or discovers
`*deployment*.json` files. `--create` starts model/deployment drafts in an
existing directory. Missing schema inputs remain visible as draft errors.

The existing compiled frontend is included. To rebuild it, use Node.js 22.12+
or 24 and `make frontend-editor`. Runtime use needs no Node.js or network.

## Editing workflow

Select a component/link in Graph, an object in Tables or a declaration in the
explorer. Use the inspector to edit its properties, including explicit defaults,
SDL IDs/cardinalities, expressions, aliases, dimensions and wire clauses.
Every semantic change opens a concrete diff before application. Rename
references and compound component operations update indexed model/deployment
references together. Application handler code is listed for review and is never
executed or automatically rewritten.

Sources uses CodeMirror and keeps unfinished text locally while navigating.
Choose **Preview source changes**, then **Apply changes** to update the shared
draft. Apply source drafts before saving. A syntax error leaves the source
editable and marks its structured projection stale.

**Validate** checks the unsaved revision using the selected deployment and the
matching OSE/SDL tools in temporary directories. It reports contract compatibility,
resolved catalogues and initial values after SDL encoding. **Save** writes the
draft after checking for external file changes; **Save draft** explicitly allows
known errors. Validation does not build or launch application handlers.

Graph positions/groups and profile registration are saved in
`.ose-editor/workspace.json`. An interrupted multi-file save is recovered on
the next opening using the visible Restore/Complete actions. Removing editor
metadata leaves normal OSE/SDL application sources. Applied unsaved drafts are
retained in a bounded server-side recovery file and can be restored or discarded
explicitly after reopening; incomplete text not yet applied from CodeMirror
remains a browser-local draft.

## Test and package

```sh
make test-editor PYTHON=/path/to/python-with-editor-dependencies
make frontend-editor
make -C ose-editor wheel PYTHON=/path/to/python-with-build
make test-release
make release
```

[API reference](docs/api.md) documents the local revisioned service. The
[validation record](docs/validation.md) separates executed checks from remaining
release qualification.

## Selected software stack

| Responsibility | Technology |
| --- | --- |
| Browser application | Vue 3, TypeScript, Vite |
| Editable graph | Vue Flow |
| Automatic graph layout | ELK.js, initially ELK Layered |
| Forms, trees and tables | PrimeVue |
| Shared application state | Pinia, with application-owned command history |
| JSON and SDL source editors | CodeMirror 6, with SDL language support |
| Project and validation service | Python, FastAPI, Uvicorn |
| Authoritative language validation | Compatible OSE and SDL tools |

The reference workload is approximately thirty components, with explicit
qualification for port/link density and large SDL declaration sets. Model
sources remain ordinary OSE/SDL files; diagram layout and editor preferences
are stored separately.

## Planned project ownership

The implementation owns its Python package, frontend, tests,
documentation, dependency lockfiles and build outputs. Proposed locations and
command names are specified in the architecture document. This documentation
does not add runtime dependencies to the OSE engine or change the Web Console's
runtime responsibilities.

## Framework release integration

The standard offline framework bundle produced by `make release` includes the
editor backend, compiled frontend, dependencies, runtime license notices and
installed `ose-editor` launcher. The `editor` feature is independent of the
console's `web` feature and is included by default in the SDK profile.
Runtime-only `all` selections omit the editor; explicitly requesting it with
`--profile runtime` is rejected with instructions to select the SDK profile.

The [release requirements](docs/specification.md#17-framework-release-bundle)
identify packaging, installer, launcher, fallback and qualification changes.
The release smoke check exercises installed authoring against an external
application using the private bundled Python, without Node.js or a source
checkout. This packaging milestone does not claim full language/UI qualification.
