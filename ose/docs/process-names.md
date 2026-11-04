# Linux process identification

OSE names Linux processes and its SDK threads through the kernel `comm` field.
The names are ASCII and at most 15 bytes, including the instance prefix. Python
3.9 and 3.12 use the same mechanism. Naming is best effort on restricted systems;
other platforms continue to run without this Linux-specific diagnostic feature.

Set an application prefix in the deployment:

```json
"process_names": {
  "prefix": "vw",
  "components": {"sensor_1": "sensor1", "analyzer_1": "analyzer1"}
}
```

The object and its component aliases are optional. The default prefix is `ose`.
Prefixes are 1–5 lowercase ASCII letters/digits, starting with a letter. An alias
must fit `PREFIX-c-ALIAS` in 15 bytes and use lowercase letters, digits or hyphens.
OSE automatically generates aliases when omitted, removes punctuation, and uses
a deterministic hash for long or ambiguous names. Collisions that remain are
rejected; specify explicit, distinct aliases to resolve them. The generated
`plan.json` records the mapping in `process_names.components`, and each node
carries its `process_identity` for SDK initialization.

Use `OSE_PROCESS_PREFIX` to override the prefix at execution time. Export the
same value in terminals running the application, console and recording tools:

```bash
export OSE_PROCESS_PREFIX=vw2
```

Long aliases are shortened again if the override leaves less space. An instance
prefix is a human-selected identifier: use distinct prefixes for concurrent runs.
Rebuild existing plans to populate their component identities.

| Name | Role |
| --- | --- |
| `vw-launch` | Application workflow launcher |
| `vw-broker` | Local supervisor and broker in their shared process |
| `vw-a-sensor1` | Local component adapter |
| `vw-c-sensor1` | Business component |
| `vw-coord` | Distributed coordinator |
| `vw-w-sensor1` | Distributed component worker |
| `vw-sim` | Simulation coordinator |
| `vw-web` | Web console |
| `vw-record` | Recorder console |
| `vw-replay` | Live replay |
| `vw-sync` | Parameter directory synchronization |

The C, Rust, Python, Java and Ada component SDKs apply `OSE_PROCESS_NAME`, supplied
by the adapter/worker, after receiving INIT and before business initialization
callbacks. Ada uses the shared C protocol SDK. Java names both the thread-group
leader and the Java thread executing INIT. Custom executables must use an OSE
SDK's INIT reader or apply the supplied name themselves; a launcher cannot retain
a name across another executable's `exec` call. No callback changes are required
in generated containers, and names stay constant across ticks.

OSE-owned client network, subscription callback, recording I/O and web database
threads receive names based on their owning process, a function suffix and a
sequence number, such as `vw-web-db1`. The web console's default executor is also
named. Names of threads created by user code or third-party runtimes are outside
this convention. Asyncio tasks share their executing Linux thread and do not
appear as separate entries in `top`.

The native Rust client also names its control, UDP, admission, callback and data
reader threads. C, Java and Ada clients using its shared native implementation
inherit those thread names.

Local and distributed status snapshots include `process` metadata: name, PID,
parent PID and host, plus the child component's name and PID. Full component
names remain the keys in those snapshots; the shared diagnostics also expose
this process metadata. PIDs are relative to the reporting process's PID namespace.

In `top`, press `c` to show short names, `o` followed by `COMMAND=vw-` to filter
one instance, and `H` to display threads. The `c` key toggles between names and
command lines, so press it only if command lines are currently shown.

```bash
ps -eo pid,ppid,comm,args
top -H
```

The original executable and arguments remain available in the command-line view.
