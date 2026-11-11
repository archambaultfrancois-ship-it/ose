# Private component transport

The engine creates one connected `AF_UNIX/SOCK_STREAM` socketpair for each
component process. CH/2 framing, SDL payloads and callback semantics are unchanged.
The local real-time adapter, parallel simulator and distributed worker all use
the same launcher. The socket is local to its worker; inter-machine CENV/2
connections are unchanged.

## Standard streams and ownership

stdin, stdout and stderr are inherited from the launcher, independently of the
protocol. Application code, constructors, third-party libraries and subprocesses
may use them normally. OSE does not capture or parse these streams. Their normal
terminal buffering and shell redirections still apply. Multiple components
inheriting the same stdin compete for input; OSE does not allocate separate
interactive terminals. SDK logs remain available through the structured LOG
service; ordinary stdout/stderr text does not become an OSE log automatically.

The launcher passes only the child's socket endpoint through `pass_fds` and sets
the internal `OSE_IPC_FD` environment variable. It closes its copy of that endpoint
immediately after spawning. Each SDK validates and claims its endpoint before
calling application handlers, and marks it close-on-exec (Rust duplicates it with
CLOEXEC and closes the inherited original). Do not configure this variable in
application deployments, close the SDK descriptor, or use it for application I/O.

Close-on-exec prevents programs executed by a component from retaining the IPC
connection. A bare POSIX `fork` still duplicates descriptors until the child
executes or closes them, as for other process resources. The launcher bounds
shutdown even if a broken component leaks its endpoint to another process.

Readers handle partial frames, and writers handle partial writes and interrupted
syscalls. A peer disconnect is an I/O failure, never an invitation to switch to
stdio. Native socket writes suppress SIGPIPE locally without changing the
application's global signal disposition. One callback is active at a time;
this transport does not extend SDK callback services to arbitrary user threads.

On shutdown the engine sends STOP, then allows 0.5 seconds for delivery and exit.
It sends SIGTERM on expiry, then SIGKILL after a further second if necessary,
reaps the process and closes its endpoint. Startup cancellation and failed
initialization also close the socket and reap the child. There is no pathname,
listener, authentication handshake or filesystem socket to clean up.

## Java native bridge

Java retains its Java 7 API/bytecode target. `NativeTransport` supplies InputStream
and OutputStream implementations to the existing Java CH/2 runtime. A dedicated
JNI library only adopts the inherited descriptor, transports bytes, shuts down
and closes the socket. It does not implement CH/2, SDL or the external client
network protocol, and does not use private JDK APIs or reflection.

`ose build` compiles `sdk/java/ose_transport_jni.c` into the application's generated
`build/components/<name>/native/` directory. The generated Java command selects
the library using its absolute path; users need no `LD_LIBRARY_PATH` setting or
manual `System.load` call. Source builds require a C compiler and JDK headers
(`JAVA_HOME/include/jni.h`); the JDK used for the headers follows JAVA_HOME or
the selected JAVAC. The bridge is independent of the external-client Rust/JNI
backend and has no Cargo dependency.

Native binaries are specific to the OS/ABI and architecture. Android/Termux and
glibc Linux need separate builds even on the same CPU. Wheel and release SDK
resources include the Java bridge source and its shared C transport header;
building an application produces its platform-specific library. Distributing
compiled Java applications must include that library along with their classes.
Regenerate the plan after relocating an application, as for other native paths.

The bridge copies bounded chunks without holding Java heap arrays across
blocking I/O. Closing the transport first calls socket shutdown to wake blocked
reads/writes. It defers descriptor close until active calls have returned, so a
concurrent operation cannot accidentally use a recycled descriptor. Interrupting
a Java thread alone does not cancel its native socket call; close the transport
or stop the component through the engine.

## Migration

Rebuild application plans, generated containers and native executables together:

```sh
ose build --model model.json --deployment deployment.json
```

Models, SDL contracts, deployments and handler signatures are unchanged. Existing
stdio-only binaries are incompatible with this launcher. There is no automatic
legacy fallback; missing/invalid descriptors fail initialization, and READY
timeouts explicitly request an SDK/container rebuild. Low-level integrations
which launch SDK executables themselves must provide the socketpair endpoint.
Explicit in-memory stream injection for Python/Java protocol tests remains
available; it is not a runtime stdio fallback.

The transport requires a POSIX environment supporting Unix socketpair and inherited
descriptors. Linux/Termux are supported targets. Windows would require a separate
launcher/transport implementation.
