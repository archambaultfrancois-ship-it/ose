# OSE Model Editor API

The local service serves one configured application project under `/api/v1`.
All API requests require `Authorization: Bearer <access-token>`; obtain the token
from the file printed by the CLI. Cross-origin browser API access is rejected.
The compiled English frontend is served from `/` without a CDN.

## Project and source reads

| Method/path | Response |
| --- | --- |
| `GET /project` | Revision, documents/projections, model/deployment selection, workspace state, diagnostics and current validation. |
| `GET /capabilities` | Editor/engine/source versions, formats and coverage qualification manifest. |
| `GET /document?file=ID` | Exact source text, format and project revision for a registered document. |
| `GET /changes` | Diffs between maintained sources on disk and the authoring draft. |
| `GET /usages?name=TEXT` | Bounded, explicitly labelled source occurrences; this search does not drive refactorings. |

File IDs identify registered sources, not arbitrary filesystem paths. A source
root can be added only through the CLI's `--source-root`. JSON projection values
outside JavaScript integer precision and exact decimal values use
`{"$number":"literal"}`; source/property operations pass numeric JSON literals
as strings. Syntax-invalid documents retain an explicitly stale last valid
projection where available.

## Shared commands

`POST /commands/prepare` accepts `{revision, command}` and returns a prepared
operation `{id, revision, changes, handlers_to_review, external_consumers}`.
Changes contain file diffs and SDL field identity changes when detected.
Preparation does not apply the edit or change maintained files.

`POST /commands/apply` accepts `{revision, id}` and returns the updated project.
`POST /undo` and `/redo` accept `{revision}`. Stale revisions return HTTP 409;
invalid commands return 422 and missing objects/previews return 404.

| Command `action` | Other properties |
| --- | --- |
| `source` | `file`, exact `text` (incomplete syntax allowed) |
| `set` | `file`, `path` array, exact JSON `literal`; use `-` to append an array item |
| `delete` | `file`, `path` |
| `rename_key` | `file`, `path`, `name`; local property rename only |
| `rename` | `kind`, `old`, `name`; kinds `component`, `port`, `subscription`, `parameter`; ports require `direction` |
| `rename` for SDL | `kind: "sdl"`, `file`, declaration `path`, `name`; scoped text-SDL references and roots |
| `add_component` | `name`, optional `copy` component; copied handlers/parameter references stay shared |
| `remove_component` | `name`; includes owned subscriptions and registered deployment entries |
| `connect` | `name` (subscription ID), `from`, `to`, optional `external` |
| `sdl_edit` | `file`, declaration `path`, `property`, string `value` |
| `sdl_add` | `file`, parent `path` or empty string, `kind`, `name`, optional `value` |
| `sdl_delete` | `file`, declaration `path` |
| `sdl_stabilize_ids` | SDL `file`; make current implicit IDs explicit before structural edits |
| `comment` | SDL `file`, comment `index`, complete comment `value` |
| `workspace` | Any of `positions`, `groups`, `views`, `pinned` |
| `deployment` | New relative filename `name`, optional `copy` deployment ID |
| `select_deployment` | Registered deployment `file` |
| `create_document` | New source filename `name`, optional exact `text` |
| `export_schema` | Registered schema `file`, `format` (`sdl`, `json`, `yaml`, `xsd`), new filename `name` outside active inputs; conversion is previewed and preserves the original |

JSON paths address structured properties, including descriptor fields. Text-SDL
declaration paths identify the current projection's tree nodes and are revision
bound. The source syntax tree remains distinct from resolved SDL catalogues.

## Validation

`POST /validation` accepts `{revision, deployment?, schema?}`. Omitting `schema`
checks the whole application with the selected deployment; supplying a schema
input name checks that source set independently. The response contains a job ID.
`GET /validation/ID` returns status/progress, immutable snapshot revision,
diagnostics, resolved descriptors, SDL catalogues, directional subscription
reports and initial-value codec previews. `DELETE /validation/ID` cancels it.

Full-application results also include `connection_contracts`: directional SDL
reports for distinct publisher/consumer schema-input pairs sharing a logical
root, including unconnected ports and external topics. Each item contains
`publisher_schema`, `consumer_schema`, `root`, `compatible`, `code`, `path` and
`message`. Candidate incompatibility does not itself fail application validation;
existing subscriptions are still checked independently. These reports apply only
to their exact project revision. The graph marks candidates unchecked when a
current report is unavailable. Preparing a connection rejects a known incompatible
pair from the current validation; it never reuses a stale report.

Jobs are serialized per project; a newer request cancels pending/active older
work. Subprocesses have bounded duration and cancellation terminates their
process group. Results for an older project revision are marked stale.
Temporary generation does not compile application handlers or run deployment
commands. It leaves application sources and ordinary build directories intact.

## Save and recovery

`POST /save` accepts `{revision, allow_invalid?: false}`. Known syntax/validation
errors require the explicit draft option. Saving verifies disk baselines and
returns HTTP 409 with `{message, conflicts}` on external changes. Each conflict
contains `file`, `baseline`, `draft` and `disk` for deliberate review.

Per-file atomic writes and a recovery journal protect compound saves. An
interrupted save blocks further mutations until `POST /recovery` receives
`{choice: "restore"}` or `{choice: "complete"}`. Recovery refuses to overwrite
files changed externally after the interruption. Successful save returns a new
project revision. No operation applies changes to a running OSE application.

Applied unsaved revisions are retained in a private server-side recovery file,
bounded to 64 MiB. When `/project` reports `draft_recovery`, use
`POST /draft-recovery` with `{revision, restore: true}` to restore it or
`{revision, restore: false}` to discard it. Restoration rechecks disk baselines
and changes the authoring draft only.
