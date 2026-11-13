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

The checkout requires Python 3.10+ in `PATH` (`python3` or a versioned command
such as `python3.12`, detected automatically). From the repository root:

```bash
make python-info
make setup
source vibration-watch/setenv.sh
ose-editor
```

`make setup` installs the Python dependencies for the application, editor, console
and backend tests into `build/python-deps`. The same PATH Python runs every tool.
The bundled frontend needs no Node.js build.

Alternatively, `ose-editor setup` prepares just the editor dependencies, and
`ose-editor` does this automatically if they are missing. Initial setup needs
pip/network access. Sourcing `setenv.sh`, `ose-editor --help`, and
`ose-editor --check` never install dependencies. All editor options are forwarded:

```sh
ose-editor --port 8083
ose-editor --root /path/to/application
```

Open `http://127.0.0.1:8082` and enter the access token from the file printed
by the command, normally `<application>/.ose-editor/access.token`. The token
is never placed in the URL. One editor process owns a project at a time.

`--model FILE`, repeated `--deployment FILE`, repeated `--source-root PATH`,
`--port PORT`, `--create` and `--check` customize project opening. Without
explicit deployment options the editor reopens registered profiles or discovers
`*deployment*.json` files. Missing remembered deployments are dropped; if none
remain, discovery runs again, so replacing split deployments with a single
`deployment.json` does not block startup. Explicit `--deployment` paths must
exist unless `--create` is supplied. `--create` starts model/deployment drafts in an
existing directory. Missing schema inputs remain visible as draft errors.

The existing compiled frontend is included. To rebuild it, use Node.js 22.12+
or 24 and `make frontend-editor`.

## Editing workflow

Use **Project** and **Inspector** above the workspace to hide or restore the
side panels without losing the selection or unfinished property edits. Drag the
separators to resize panels, or focus a separator and use the arrow keys
(**Home**/**End** select its minimum/maximum size). Diagnostics starts as a
collapsed bar and opens when new diagnostics arrive; its tabs and **Expand** /
**Collapse** control the panel. **Display** in the graph toolbar contains link
labels, activation links, link style and dependency highlighting; **Groups**
contains grouping commands. The inspector uses collapsible sections for
General, Execution, Reception and Contract where applicable, with removal
commands under the initially collapsed **Advanced** section.

**Go to…** (**Ctrl+K** / **Cmd+K**) searches components, ports, subscription IDs,
parameters, external topics, SDL declarations and project files. Search terms
can include a filename or symbol kind. Use **Up/Down** then **Enter** to open a
result, **Shift+Enter** for its source, or **Escape** to close. Ports open in the
component editor; components hidden inside folded groups reveal their visible
ancestor without changing the saved layout. Stale projections are marked and
open as source. Search displays up to 80 results; refine the query for more.

Diagnostics retain the original validator message and add an explanation,
source/property links and, for recognized Pull timeout, latest reception and
retry/ACK errors, explicit **Preview** corrections. Corrections use the normal
diff, apply and undo workflow. No correction is inferred for an unknown error.
Source syntax diagnostics carry one-based line/column positions when available.
JSON paths resolve against the current source draft; coordinate-based and SDL
locations are withheld when unfinished source text may have shifted them.
Apply source drafts before preparing a diagnostic correction.

Select a component/link in Graph, an object in Tables or a declaration in the
explorer. Use the inspector to edit its properties, including explicit defaults,
SDL IDs/cardinalities, expressions, aliases, dimensions and wire clauses.
Subscriptions appear under their `id` in the explorer and inspector; the model
keeps its existing array format, with numeric paths used internally for edits.
New JSON objects and arrays are expanded across indented lines, following the
file's indentation and line endings while preserving exact scalar literals.
To duplicate a subscription, select it and choose **Duplicate**, then enter a
new ID and a destination input (`component.port`), including an already connected
input. New connections and duplicates inherit that input's shared reception
settings; duplicates preserve their source, ACK/retry and explicit deployment
settings. Selecting an input or subscription shows its incoming links and a
shared FIFO/latest reception editor. Applying it updates every incoming link in
one previewable, undoable operation. Incompatible policies (including UDP wait)
are rejected without partial changes. Graph ports show their link count and mode.

Model v2 components show their Push, passive Pull and timer inputs. Select an
input name on a component card to open **Input execution** in the inspector.
Choose message/Push/Pull or timer, edit inactivity or timer settings, and select
the allowed output activations for each event. **Message received** and
**Timeout** have independent output lists; both may select the same outputs.
An empty list authorizes no publication from that event. Pull inputs have no
activating callback or timeout, and timer inputs have no SDL message or incoming
subscription handles. FIFO/latest reception remains a separate input policy.

Use **Add input** in this panel to create a named input. **Preview input changes**
prepares one source-preserving, undoable operation, with component handlers listed
for review. Converting an input with incoming subscriptions to a timer is blocked
until those subscriptions are disconnected. Switching to Pull explicitly removes
its timeout and activation declarations in the preview. The authoritative OSE
validator rejects invalid events, outputs and callback names. Activation cycles
can be authored and compiled; execution requires simulated `--microsteps`.

The **Activation links** checkbox displays internal input-event → output arrows:
blue for received messages, amber dashed lines for timeouts. Display is optional
and does not alter the model. The **activations** table provides the same links
as selectable rows. Output renames update their activation references. Structured
editing supports application model v2 only. An old model remains accessible as
source for migration, with an explicit diagnostic; graph/table projections and
structured model commands do not apply v2 semantics to it. Save draft can preserve
that source. Deployment and scenario versions remain unchanged.

**Double-click a component** or choose its pencil button to enter component
editing. Inputs and timers appear on the left, outputs on the right. Select a
port to edit its declaration, rename, duplicate or remove it; the toolbar adds
new inputs, timers and outputs. Removal previews all affected subscriptions,
deployment overrides and activation permissions together. Duplicated ports do
not copy subscriptions; duplicating an output grants no activation permissions.
Drag a message or timeout anchor to an output to add an activation permission.
Alternatively, click or use Enter/Space on the event anchor, then the output
anchor. Select an arrow to change its endpoints/event or remove it. Pull inputs
have no activation anchor. Connected subscriptions remain accessible from the
port context. **Application** returns to the graph with its viewport and visual
groups retained. These edits use the normal preview, undo/redo and explicit save
workflow; application handler code still requires manual review.

In Graph, use **Shift**, **Ctrl**, or **Cmd + click** to add or remove components
from the selection, or **Shift**, **Ctrl**, or **Cmd + drag** on the empty canvas
to select an area. A plain drag on the canvas moves the view. The
caption shows the selected component and group counts. Choose **Group selection**
to create a named visual group containing the selected components and/or groups.
Select existing groups with **Shift**, **Ctrl**, or **Cmd + click** to combine
them into a parent group; multiple levels of grouping are supported. Group names
must be unique. Regrouping moves selected items out of their previous parent.
A collapsed group hides ports used only by internal links, while retaining
crossing connections and unused ports. **Double-click** a group to expand one
level: its subgroups keep their own collapsed state. **Collapse all groups**
restores the folded hierarchy. These groups are visual metadata; they do not
change the model's components or subscriptions.
Links are curved by default. Use **Link style** in the graph toolbar to switch
between curved, straight and right-angle links for the current view.
Dragging from an output or an input marks the opposite ports with **✓ Compatible**,
**? Contract unchecked** or **× Unavailable**. Hover a marked port for the reason:
different logical roots, conflicting shared reception settings,
or a directional SDL contract mismatch. Full **Validate** resolves candidate
contracts, including ports not yet connected, for the current applied revision.
Before validation, or after an edit, structural checks remain available and SDL
compatibility is explicitly unchecked. Unknown contracts may be connected and
checked later; known incompatible connections are blocked. A connection creating
an activation cycle remains available with a note requiring simulated `--microsteps`.
Use **Connect ports** for a keyboard-accessible publisher/destination selector,
including ports hidden inside groups and external topics. A completed drag opens
the same form. Enter a subscription ID and choose **Preview connection** to review
the source diff before applying the undoable change. Existing incoming connections
do not prevent another producer; the destination's shared reception is inherited.
Presentation changes (groups, positions and auto layout) apply immediately
without a review dialog and remain undoable with **Undo**.
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

The UI uses PrimeVue 4.5.5 and compatible PrimeUIX themes under the MIT license.
The lockfile and frontend tests keep this dependency family on MIT packages;
the editor requires no PrimeUI license key.

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
