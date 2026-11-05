# Configuration reference

All configuration is JSON. Unknown keys are rejected. Paths in a configuration
file are relative to that file, except `state_dir`, which is relative to the
launch directory. `--working-directory PATH` selects another existing directory
as the base for relative state paths and captures; PATH itself is relative to
the launch directory. An absolute `state_dir` keeps its configured location.
`application.endpoint` is relative to the resolved `application.root`. Direct
CLI endpoint paths are relative to the calling directory, independently of
`--working-directory`. Precedence: CLI, the documented environment variables,
configuration file, defaults.

The only console configuration environment overrides are `OSE_WS_HOST`,
`OSE_WS_PORT`, and `OSE_WS_STATE_DIR`. The OSE parameter token is read from the
environment variable named in `parameters.token_env` (default
`OSE_PARAMETER_TOKEN`). It is never returned to the browser.
Relative `OSE_WS_STATE_DIR` values also use the runtime directory. Changing that
directory selects a different database and capture inventory; select the prior
directory to reopen its state. The access token remains relative to the
configuration file, including during `init`.

```json
{
  "listen": {"host": "127.0.0.1", "port": 8080},
  "application": {"root": ".", "endpoint": "build/endpoint.json", "name": "My application"},
  "state_dir": ".ose-ws",
  "collection": {"interval_ms": 500, "diagnostic_window_s": 5},
  "measurements": {
    "retention_s": 900,
    "max_series": 128,
    "max_total_samples": 250000,
    "memory_budget_mib": 32,
    "preconfigured": ["source.temperature"]
  },
  "recording": {"max_active": 4, "per_user": 2, "min_free_disk_mib": 1024},
  "flows": {"max_sources": 4, "per_session": 2, "lease_s": 60, "max_payload_bytes": 1048576},
  "parameters": {"client": "operator", "token_env": "OSE_PARAMETER_TOKEN"},
  "auth": {"mode": "local_token", "token_file": ".ose-ws/access.token"}
}
```

Watch limits count logical `component.measurement` names, not historical
generation segments. Each browser watch lasts 60 seconds and is renewed every
20 seconds while the browser session remains authenticated. The last expired
watch stops recording new observations; existing points expire normally.
Preconfigured names/globs remain followed within the configured series budget.
The first sample-count, retention or accounted-buffer limit evicts old points.
The response reports truncated history. This is not a total process RSS limit.
OSE `status` still transfers all latest measures; selection bounds console
history, not upstream RPC bandwidth.

The minimum status interval is 100 ms. Status has one owner and no overlapping
requests, regardless of browser count. Current report freshness follows the
same three-second threshold as `ose top`.

Live link previews share one source tap and one decoder per observed epoch/topic.
Defaults allow four distinct source topics globally and two watches per browser
session (including tabs sharing its cookie). Each source tap keeps at most one
message with a 1 MiB byte budget. Watches expire after 60 seconds and the UI
renews at one third of the lease. Configure `flows.max_sources` from 1–8,
`per_session` from 1–4, `lease_s` from 5–120 and `max_payload_bytes` from
1,024–1,048,576 bytes. The overall watch quota is `auth.max_sessions` times
`flows.per_session`. These bound retained buffers; in-flight frames, codec
allocations and per-watch projections add memory and do not form a process RSS
guarantee. One dedicated decode worker coalesces source updates without building
an unbounded task queue. Source taps also count against OSE's 16-session / 64 MiB
reserved-budget limits. An exhausted source refuses a new Live watch.

Payload access uses `flows.read` with source topic names/globs. For example,
grant `"flows.read":["analyzer_*.result"]` separately from `components.read`.
Reading a contract or component status does not grant access to message values.

## Distributed OSE

Set `endpoint` to `null` when using an explicit host/port:

```json
{
  "application": {
    "root": ".", "endpoint": null, "name": "Distributed application",
    "host": "coordinator.example.internal", "port": 7000,
    "distributed": true,
    "tls_ca": "certs/ca.pem",
    "tls_cert": "certs/operator.pem",
    "tls_key": "certs/operator.key"
  },
  "parameters": {"client": "operator", "token_env": "OSE_PARAMETER_TOKEN"}
}
```

The certificate identity must match the declared OSE parameter client. Discovery
comes from the coordinator; capture data travels directly from producer workers
to the console. The console must reach announced worker endpoints. Certificate
identities are verified against OSE's existing rules. `insecure: true` is only
valid in distributed mode with loopback endpoints and no TLS options.

## HTTPS proxy and user policies

An example policy fragment, merged with the defaults:

```json
{
  "listen": {"host": "127.0.0.1", "port": 8080},
  "auth": {
    "mode": "proxy",
    "trusted_proxies": ["127.0.0.1", "::1"],
    "users": {
      "reader@example.test": {
        "permissions": {
          "components.read": ["*"],
          "measurements.read": ["source.*"],
          "parameters.read": ["machine/settings"]
        }
      },
      "operator@example.test": {
        "permissions": {
          "components.read": ["*"],
          "measurements.read": ["*"],
          "recordings.create": ["source.*"],
          "recordings.read": ["*"],
          "recordings.stop": ["*"],
          "recordings.download": ["*"],
          "recordings.delete": ["*"],
          "parameters.read": ["machine/settings"],
          "parameters.write": ["machine/settings"]
        }
      },
      "admin@example.test": {"permissions": {"admin": ["*"]}}
    }
  }
}
```

The proxy must overwrite `X-OSE-User`, `X-Forwarded-Proto` and `Host`, authenticate
the user's identity, terminate HTTPS, and forward WebSocket upgrades. It must
strip spoofed client-supplied identity headers. Disable any other path to the
console listener. Uvicorn's automatic proxy-header interpretation is disabled;
the console checks the actual peer against `trusted_proxies` itself.

Each proxy user signs in through the same page without a local token. Their
identity is checked on HTTP and WebSocket traffic. Cookies are Secure in proxy
mode. Changing the proxy identity requires signing in again.

Parameter scopes are **exact paths**. Branches do not imply descendants. `*`
means an explicit administrative all-path selection. Measurement and recording
creation scopes use documented case-sensitive shell globs. Capture read/stop/
download scopes are capture IDs or `*` for the owner's captures. An exact capture
ID can explicitly share that operation with another user; `*` does not share
other users' captures. Administrators can access all captures. Effective OSE rights are always checked
by OSE in addition to web policy. All web users use the configured technical OSE
client identity; the audit retains their separate web identities.

Deletion requires `recordings.delete` scoped to a capture ID or `*` for the
owner's captures. It cannot be delegated to another owner; administrators may
delete any inactive capture. Granting read or stop access does not grant delete
access. Local-token administrators already have this permission.

An API client with only parameter write permission can inspect its contract and
validate a supplied value, but cannot read/export the current value or use the
browser's read-based editor.

## Presentation metadata

Set `parameters.presentation` to a local JSON filename. The file is keyed by
root type, then JSON Pointer. A `*` segment matches sequence elements for
business validation. Metadata is separate from the SDL wire catalogue.

```json
{
  "Settings": {
    "/threshold": {"label": "Detection threshold", "unit": "mm/s", "required": true, "min": 0, "max": 100},
    "/label": {"help": "Operator-facing label", "widget": "textarea"}
  }
}
```

`required: true` on a metadata rule makes its `min`, `max` or `choices`
constraints mandatory on the server, before and after codec normalization.
It does not change SDL optional presence or grant permission. Widget preferences
are hints. Presentation content is rendered as text. Restart the console to
load changed metadata; a presentation digest participates in validation previews.

## Local persistence and limits

```text
.ose-ws/
  server.lock
  state.sqlite3
  captures/<recording_id>/capture[_NNN].pcapng
```

This runtime tree is under the launch directory by default. With the default
authentication settings, `access.token` is in `.ose-ws/` beside the configuration
file; it shares the runtime tree only when both base directories are the same.

SQLite uses a serialized worker and full synchronous commits. Disk writes and
large SDL conversion/validation work run outside the HTTP event loop. The
state directory must be private to its owner. Never share it between processes.

Log access uses `logs.read` permission patterns matching component names, for
example `"logs.read": ["sensor_*", "alarm"]`. Administrators can read all logs.
The runtime retains up to 10,000 entries within 8 MiB in memory; the web view
keeps up to 1,000 entries within 2 MiB. Logs are not stored in the state directory.

Defaults: 20 browser sessions; 4 active captures, 2 per user; 100 drafts/import
plans; 10,000 retained operations with at most 1,000 unresolved operations;
100,000 audit events or 30 days; 60-second
graceful stop; 300-second artifact validation deadline; 2 MiB ordinary JSON,
16 MiB explicit imports, 64 JSON levels;
256 KiB WebSocket frames, 16 inbound protocol messages; at most 4096 points per
live measurement batch before explicit resynchronization. Slow socket writes
time out independently of collection and recording.

Source admission remains limited by OSE: at most 32 endpoints per capture,
128 points per source session, 16 source sessions and 64 MiB total source
queue budgets per service, shared with other recorders. Default source queue:
256 messages / 8 MiB per endpoint. A source refusal remains possible even when
local quotas permit a capture.

Capture metadata is capped at 10,000 objects. Delete unused drafts and finished
captures to release their metadata quotas. Keeping files when deleting a capture
does not release disk space; those files require separate offline management.
The free-disk threshold blocks new captures and attempts to drain active ones.
ENOSPC may still occur before the periodic check.

Exit codes: `0` normal exit/check, `2` invalid configuration, `3` initialization
failure (including state lock, port and local storage). Transient OSE absence
does not fail startup.

`STATE_IN_USE` at startup means another console still owns the state directory;
the message includes its PID when available. Stop that console with Ctrl+C or
SIGTERM before restarting. A leftover `server.lock` file or old PID alone does
not block startup: the operating system releases the lock when its owner exits.
Do not delete the lock file to bypass an active server's ownership.
