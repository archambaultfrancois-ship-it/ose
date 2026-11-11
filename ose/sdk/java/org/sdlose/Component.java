package org.sdlose;

import java.io.*;
import java.math.BigInteger;
import java.nio.ByteBuffer;
import java.nio.charset.*;
import java.util.*;
import sdl.runtime.SdlCodec;

/** CH/2 component runtime on a private socketpair; standard streams are free. */
public final class Component {
    private static void nameProcess() {
        // The JVM's main Java thread need not be the Linux thread-group leader.
        // Name both, so top's process and thread views identify the component.
        try {
            String name = System.getenv("OSE_PROCESS_NAME");
            if (name == null || !name.matches("[a-z0-9-]{1,15}") ||
                !System.getProperty("os.name", "").equals("Linux")) return;
            for (String path : new String[]{"/proc/self/comm", "/proc/thread-self/comm"}) {
                try (FileOutputStream output = new FileOutputStream(path)) {
                    output.write(name.getBytes(StandardCharsets.US_ASCII));
                } catch (IOException ignored) { /* Naming must not stop execution. */ }
            }
        } catch (SecurityException ignored) { /* Optional on restricted runtimes. */ }
    }
    public static final int MAX_FRAME = 1024 * 1024;
    public enum LogLevel {
        DEBUG(10), INFO(20), WARNING(30), ERROR(40), CRITICAL(50), FATAL(60);
        final int code;
        LogLevel(int code) { this.code = code; }
    }
    private static void depositLog(OutputStream output, LogLevel level, String logger, String message) throws IOException {
        byte[] name = utf8(logger), text = utf8(message);
        if (name.length > 200 || text.length == 0 || logger.indexOf(0) >= 0 || message.indexOf(0) >= 0)
            throw new IllegalArgumentException("invalid log category/message");
        byte[] suffix = utf8("… [truncated]");
        int kept = text.length;
        boolean truncated = kept > 8192;
        if (truncated) {
            kept = 8192 - suffix.length;
            while (kept > 0 && (text[kept] & 0xc0) == 0x80) kept--;
        }
        ByteArrayOutputStream bytes = new ByteArrayOutputStream();
        DataOutputStream data = new DataOutputStream(bytes);
        data.writeByte(level.code); data.writeShort(name.length); data.write(name); data.write(text, 0, kept);
        if (truncated) data.write(suffix);
        send(output, 'L', bytes.toByteArray());
    }
    private static final Charset UTF8 = Charset.forName("UTF-8");
    /** Deposit a formatted log; use "%s", message for preformatted text. */
    public static void logf(OutputStream output, LogLevel level, String logger, String format, Object... arguments) throws IOException {
        depositLog(output, level, logger, String.format(Locale.ROOT, format, arguments));
    }
    public static String text(byte[] bytes) throws IOException {
        return UTF8.newDecoder().onMalformedInput(CodingErrorAction.REPORT)
            .onUnmappableCharacter(CodingErrorAction.REPORT).decode(ByteBuffer.wrap(bytes)).toString();
    }
    public static byte[] utf8(String text) throws IOException {
        ByteBuffer bytes = UTF8.newEncoder().onMalformedInput(CodingErrorAction.REPORT)
            .onUnmappableCharacter(CodingErrorAction.REPORT).encode(java.nio.CharBuffer.wrap(text));
        byte[] result = new byte[bytes.remaining()]; bytes.get(result); return result;
    }
    public static final class Frame {
        public final int op; public final byte[] body;
        Frame(int op, byte[] body) { this.op = op; this.body = body; }
    }
    public static Frame read(InputStream input) throws IOException {
        DataInputStream in = new DataInputStream(input);
        int size = in.readInt();
        if (size < 1 || size > MAX_FRAME) throw new IOException("invalid CH/2 frame size");
        int op = in.readUnsignedByte(); byte[] body = new byte[size - 1]; in.readFully(body);
        return new Frame(op, body);
    }
    public static void send(OutputStream output, int op, byte[] body) throws IOException {
        if (body.length >= MAX_FRAME) throw new IOException("CH/2 frame exceeds limit");
        DataOutputStream out = new DataOutputStream(output);
        out.writeInt(body.length + 1); out.writeByte(op); out.write(body); out.flush();
    }
    public static String resource(Class<?> owner, String path) throws IOException {
        InputStream in = owner.getResourceAsStream(path);
        if (in == null) throw new IOException("missing catalogue resource " + path);
        try {
            ByteArrayOutputStream out = new ByteArrayOutputStream(); byte[] buffer = new byte[4096]; int n;
            while ((n = in.read(buffer)) != -1) {
                if (out.size() + n > MAX_FRAME) throw new IOException("catalogue exceeds limit");
                out.write(buffer, 0, n);
            }
            return text(out.toByteArray());
        } finally { in.close(); }
    }
    public static final class Port {
        public final String name, root, catalogue;
        public final Class<? extends SdlCodec.Message> type;
        public final boolean timed;
        private String remote;
        private SdlCodec.Context decoder;
        final SdlCodec.Context encoder;
        public Port(String name, String root, String catalogue, Class<? extends SdlCodec.Message> type,
                    boolean timed) {
            this.name = name; this.root = root; this.catalogue = catalogue; this.type = type;
            this.timed = timed;
            encoder = SdlCodec.prepare(catalogue, type);
        }
        Object decode(String cat, byte[] bytes) throws IOException {
            if (!cat.equals(remote)) {
                if (!SdlCodec.compareCatalogues(cat, catalogue, root, true).compatible())
                    throw new IOException("incompatible input catalogue for " + name);
                decoder = SdlCodec.prepare(cat, type); remote = cat;
            }
            Object value = SdlCodec.decode(decoder, bytes);
            if (!type.isInstance(value)) throw new IOException("wrong message root for " + name);
            return value;
        }
    }
    public static final class InputSpec {
        public final String name;
        public final Port port; // null for SDL-free timers
        public final InputProtocol.Mode mode;
        private final Set<String> messageOutputs, timeoutOutputs;
        public InputSpec(String name, Port port, InputProtocol.Mode mode, String[] message, String[] timeout) {
            if (name == null || name.length() == 0 || mode == null ||
                ((mode == InputProtocol.Mode.PUSH || mode == InputProtocol.Mode.PULL) != (port != null)) ||
                (port != null && !name.equals(port.name))) throw new IllegalArgumentException("invalid input declaration");
            this.name = name; this.port = port; this.mode = mode;
            messageOutputs = Collections.unmodifiableSet(new HashSet<String>(Arrays.asList(message)));
            timeoutOutputs = Collections.unmodifiableSet(new HashSet<String>(Arrays.asList(timeout)));
        }
    }
    public static class Context {
        public final void logf(LogLevel level, String logger, String format, Object... arguments) throws IOException {
            Component.logf(stream, level, logger, format, arguments);
        }
        public final String name;
        public String configJson;
        private BigInteger time = BigInteger.ZERO, index = BigInteger.ZERO;
        private Thread owner;
        private final Port[] outputs;
        private final OutputStream stream;
        private final Map<String, Port> recordingPoints = new HashMap<>();
        private final Set<String> recordingActive = new HashSet<>();
        private final Set<String> measurements = new HashSet<>();
        private boolean initializing;
        private int recordingBytes;
        private InputStream inputStream;
        private Port[] parameters = new Port[0];
        private boolean[] parameterRead = new boolean[0], parameterWrite = new boolean[0];
        private byte[][] parameterValues = new byte[0][];
        private InputSpec[] inputSpecs;
        private InputSpec origin;
        private InputProtocol.Event originEvent;
        protected final void configureInputs(InputSpec[] inputs) {
            if (inputSpecs != null) throw new IllegalStateException("input declarations already configured");
            inputSpecs = inputs.clone();
            Set<String> names = new HashSet<String>();
            for (InputSpec spec : inputSpecs)
                if (!names.add(spec.name)) throw new IllegalArgumentException("duplicate input name");
        }
        protected final void configureParameters(Port[] ports, boolean[] readable, boolean[] writable) {
            parameters = ports; parameterRead = readable; parameterWrite = writable;
            parameterValues = new byte[ports.length][];
        }
        protected final Object parameter(int slot) throws IOException {
            if (slot < 0 || slot >= parameters.length || !parameterRead[slot] || parameterValues[slot] == null)
                throw new IOException("parameter is not readable");
            return parameters[slot].decode(parameters[slot].catalogue, parameterValues[slot]);
        }
        protected final void setParameter(int slot, SdlCodec.Message value) throws IOException {
            if (owner != Thread.currentThread()) throw new IllegalStateException("parameter writes require an active callback");
            if (slot < 0 || slot >= parameters.length || !parameterWrite[slot] || !parameters[slot].type.isInstance(value))
                throw new IOException("invalid writable parameter");
            Port port = parameters[slot];
            byte[] name = utf8(port.name), payload = SdlCodec.encode(port.encoder, value);
            ByteArrayOutputStream bytes = new ByteArrayOutputStream(); DataOutputStream data = new DataOutputStream(bytes);
            data.writeShort(name.length); data.write(name); data.write(payload);
            send(stream, 'S', bytes.toByteArray());
            Frame result = read(inputStream);
            if (result.op == 'A' && Arrays.equals(result.body, new byte[]{1})) return;
            if (result.op == 'A' && result.body.length > 0 && result.body[0] == 0)
                throw new IOException(text(Arrays.copyOfRange(result.body, 1, result.body.length)));
            throw new IOException("invalid parameter write result");
        }
        private void parameterUpdate(Frame event, boolean initial) throws IOException {
            if (event.op != 'U') throw new IOException("expected parameter update");
            DataInputStream data = new DataInputStream(new ByteArrayInputStream(event.body));
            int length = data.readUnsignedShort();
            if (length == 0 || length > data.available() - 4) throw new IOException("invalid parameter alias");
            byte[] name = new byte[length]; data.readFully(name);
            String alias = text(name); int slot = -1;
            for (int i = 0; i < parameters.length; ++i) if (parameters[i].name.equals(alias)) slot = i;
            if (slot < 0 || !parameterRead[slot] || (initial && parameterValues[slot] != null))
                throw new IOException("unknown or duplicate initial parameter");
            int catSize = data.readInt();
            if (catSize < 1 || catSize > data.available()) throw new IOException("invalid parameter catalogue");
            byte[] catalogue = new byte[catSize]; data.readFully(catalogue);
            byte[] payload = new byte[data.available()]; data.readFully(payload);
            if (!parameters[slot].catalogue.equals(text(catalogue))) throw new IOException("wrong parameter catalogue");
            parameters[slot].decode(parameters[slot].catalogue, payload);
            parameterValues[slot] = payload;
        }
        public Context(String name, Port[] outputs) { this(name, outputs, NativeTransport.instance().output()); }
        public Context(String name, Port[] outputs, OutputStream stream) {
            this.name = name; this.outputs = outputs; this.stream = stream;
        }
        public BigInteger timeNs() { return time; }
        public BigInteger tickIndex() { return index; }
        public BigInteger activationIndex() { return index; }
        public double now() { return time.doubleValue() / 1000000000.0; }
        public final void declareMeasurement(String name) throws IOException {
            if (!initializing || owner != Thread.currentThread() || measurements.contains(name) ||
                measurements.size() >= 128 || name == null || name.length() > 200 ||
                !name.matches("[A-Za-z_][A-Za-z0-9_]*(\\.[A-Za-z_][A-Za-z0-9_]*)*"))
                throw new IllegalArgumentException("measurement declarations require onStart and unique valid names (max 128)");
            ByteArrayOutputStream bytes = new ByteArrayOutputStream(); DataOutputStream out = new DataOutputStream(bytes);
            byte[] local = utf8(name); out.writeShort(local.length); out.write(local);
            send(stream, 'M', bytes.toByteArray()); measurements.add(name);
        }
        public final void measure(String name, double value) throws IOException {
            if (initializing || owner != Thread.currentThread() || !measurements.contains(name))
                throw new IllegalStateException("measurement requires a declared name and the active input callback");
            if (Double.isNaN(value) || Double.isInfinite(value)) throw new IllegalArgumentException("measurement requires a finite fl64");
            ByteArrayOutputStream bytes = new ByteArrayOutputStream(); DataOutputStream out = new DataOutputStream(bytes);
            byte[] local = utf8(name); out.writeShort(local.length); out.write(local); out.writeDouble(value);
            send(stream, 'V', bytes.toByteArray());
        }
        public final <T extends SdlCodec.Message> void declareRecordingPoint(
                String name, Class<T> type, String description) throws IOException {
            if (!initializing || owner != Thread.currentThread() || recordingPoints.containsKey(name) ||
                recordingPoints.size() >= 128 || name == null || name.length() > 200 ||
                !name.matches("[A-Za-z_][A-Za-z0-9_]*(\\.[A-Za-z_][A-Za-z0-9_]*)*"))
                throw new IllegalArgumentException("recording declarations require initialization and unique valid names");
            if (description == null || description.indexOf(0) >= 0 || utf8(description).length > 1024)
                throw new IllegalArgumentException("invalid recording description");
            String root = SdlCodec.messageName(type), catalogue = SdlCodec.description(type);
            Port point = new Port(name, root, catalogue, type, false);
            ByteArrayOutputStream bytes = new ByteArrayOutputStream(); DataOutputStream out = new DataOutputStream(bytes);
            byte[] local = utf8(name); out.writeShort(local.length); out.write(local);
            out.write(utf8(root)); out.writeByte(0); out.write(utf8(description)); out.writeByte(0); out.write(utf8(catalogue));
            send(stream, 'N', bytes.toByteArray()); recordingPoints.put(name, point);
        }
        public final boolean recordingEnabled(String name) {
            if (!recordingPoints.containsKey(name)) throw new IllegalArgumentException("unknown recording point");
            return recordingActive.contains(name);
        }
        public final RecordResult record(String name, SdlCodec.Message value) throws IOException {
            if (initializing || owner != Thread.currentThread())
                throw new IllegalStateException("recording requires the active input callback");
            Port point = recordingPoints.get(name);
            if (point == null || !point.type.isInstance(value)) throw new IllegalArgumentException("unknown point or wrong recording type");
            if (!recordingActive.contains(name)) return RecordResult.DISABLED;
            if (recordingBytes + name.length() + 2 >= 256 * 1024) return RecordResult.DROPPED;
            byte[] payload = SdlCodec.encode(point.encoder, value), local = utf8(name);
            int size = local.length + 2 + payload.length;
            if (size > 256 * 1024 - recordingBytes) return RecordResult.DROPPED;
            ByteArrayOutputStream bytes = new ByteArrayOutputStream(); DataOutputStream out = new DataOutputStream(bytes);
            out.writeShort(local.length); out.write(local); out.write(payload);
            send(stream, 'C', bytes.toByteArray()); recordingBytes += size;
            return RecordResult.QUEUED;
        }
        private void recordingUpdate(byte[] data) throws IOException {
            recordingActive.clear();
            if (data.length == 0) return;
            for (String name : text(data).split(String.valueOf((char)0), -1)) {
                if (!recordingPoints.containsKey(name) || !recordingActive.add(name))
                    throw new IOException("unknown or duplicate active recording point");
            }
        }
        protected final void publish(int index, SdlCodec.Message message) throws IOException {
            publish(index, message, null);
        }
        protected final void publish(int index, SdlCodec.Message message, BigInteger appId) throws IOException {
            if (owner != Thread.currentThread()) throw new IllegalStateException("publication requires the active input callback");
            Port port = outputs[index];
            if (origin == null || !(originEvent == InputProtocol.Event.MESSAGE ? origin.messageOutputs : origin.timeoutOutputs).contains(port.name))
                throw new IllegalStateException("input event cannot activate output " + port.name);
            if (!port.type.isInstance(message)) throw new IllegalArgumentException("wrong output type");
            byte[] payload = SdlCodec.encode(port.encoder, message);
            send(stream, 'P', InputProtocol.publication(port.name, payload, appId));
        }
        private InputSpec pullInput(int slot) {
            if (inputSpecs == null || initializing || owner != Thread.currentThread() || origin == null)
                throw new IllegalStateException("input access requires an active callback");
            if (slot < 0 || slot >= inputSpecs.length || inputSpecs[slot].mode != InputProtocol.Mode.PULL)
                throw new IllegalArgumentException("message access requires a Pull input");
            return inputSpecs[slot];
        }
        private byte[] inputRequest(int operation, String port, InputProtocol.Selector selector, BigInteger delay) throws IOException {
            send(stream, 'Q', InputProtocol.request(operation, port, selector, delay));
            return InputProtocol.reply(read(inputStream));
        }
        private Object decodeRecord(InputSpec spec, InputProtocol.Record record) throws IOException {
            if (record.receivedNs.compareTo(time) > 0) throw new IOException("input received in the future");
            SdlCodec.Envelope envelope = SdlCodec.unpackEnvelope(record.envelope);
            if (envelope.catalogueId != SdlCodec.catalogueId(record.catalogue))
                throw new IOException("input catalogue identity mismatch");
            return spec.port.decode(record.catalogue, envelope.payload);
        }
        protected final <T extends SdlCodec.Message> InputProtocol.Reference<T> getInput(
                int slot, InputProtocol.Selector selector, Class<T> type, boolean consume) throws IOException {
            InputSpec spec = pullInput(slot);
            if (spec.port.type != type) throw new IllegalArgumentException("wrong input reference type");
            InputProtocol.Record record = InputProtocol.record(inputRequest(consume ? 2 : 1, spec.name, selector, null));
            if (record == null) return null;
            T value = type.cast(decodeRecord(spec, record));
            return new InputProtocol.Reference<T>(spec.name, InputProtocol.Event.MESSAGE, value,
                record.handle, record.source, record.appId, BigInteger.ONE, record.receivedNs);
        }
        protected final boolean removeInput(int slot, InputProtocol.Selector selector) throws IOException {
            InputSpec spec = pullInput(slot);
            byte[] result = inputRequest(3, spec.name, selector, null);
            if (result.length != 1 || (result[0] != 0 && result[0] != 1)) throw new IOException("invalid removal reply");
            return result[0] == 1;
        }
        private void timerControl(int operation, String port, BigInteger delayNs) throws IOException {
            if (inputSpecs == null || owner != Thread.currentThread() || (!initializing && origin == null))
                throw new IllegalStateException("timer control requires initialization or an active callback");
            InputSpec timer = null;
            for (InputSpec spec : inputSpecs) if (spec.name.equals(port)) timer = spec;
            if (timer == null || (timer.mode != InputProtocol.Mode.PERIODIC && timer.mode != InputProtocol.Mode.ONE_SHOT))
                throw new IllegalArgumentException("timer control requires a declared timer");
            if (inputRequest(operation, port, null, delayNs).length != 0) throw new IOException("invalid timer reply");
        }
        public final void arm(String port) throws IOException { arm(port, null); }
        public final void arm(String port, BigInteger delayNs) throws IOException { timerControl(4, port, delayNs); }
        public final void disarm(String port) throws IOException { timerControl(5, port, null); }
        private void activate(Frame event, InputCallbacks callbacks) throws Exception {
            InputProtocol.Activation activation = new InputProtocol.Activation(event.body);
            if (!activation.index.equals(index.add(BigInteger.ONE)) || activation.timeNs.compareTo(time) < 0)
                throw new IOException("non-monotonic CH/2 activation");
            InputSpec selected = null;
            for (InputSpec spec : inputSpecs) if (spec.name.equals(activation.port)) selected = spec;
            if (selected == null || selected.mode == InputProtocol.Mode.PULL ||
                (activation.event == InputProtocol.Event.MESSAGE && selected.mode != InputProtocol.Mode.PUSH) ||
                (activation.event == InputProtocol.Event.TIMEOUT && selected.mode == InputProtocol.Mode.PUSH && !selected.port.timed) ||
                (selected.mode == InputProtocol.Mode.ONE_SHOT && !activation.elapsed.equals(BigInteger.ONE)))
                throw new IOException("invalid input activation");
            time = activation.timeNs; index = activation.index;
            InputProtocol.Record record = activation.record;
            Object value = record == null ? null : decodeRecord(selected, record);
            InputProtocol.Reference<Object> reference = new InputProtocol.Reference<Object>(selected.name, activation.event,
                value, record == null ? null : record.handle, record == null ? null : record.source,
                record == null ? null : record.appId, activation.elapsed, activation.dueNs);
            origin = selected; originEvent = activation.event;
            owner = Thread.currentThread(); recordingBytes = 0;
            try { callbacks.activate(reference); }
            finally { owner = null; origin = null; originEvent = null; }
        }
    }
    public enum RecordResult { QUEUED, DISABLED, DROPPED }
    public interface InputCallbacks {
        void start() throws Exception;
        void activate(InputProtocol.Reference<?> input) throws Exception;
        void stop() throws Exception;
    }
    public static void runInputs(Context ctx, InputCallbacks callbacks) throws Exception {
        try { runInputs(ctx, callbacks, NativeTransport.instance().input()); }
        finally { NativeTransport.instance().close(); }
    }
    public static void runInputs(Context ctx, InputCallbacks callbacks, InputStream stream) throws Exception {
        if (ctx.inputSpecs == null) throw new IllegalStateException("CH/2 input declarations are required");
        Frame init = read(stream);
        if (init.op != 'I') throw new IOException("expected INIT");
        ctx.configJson = text(init.body);
        ctx.inputStream = stream;
        // Validate JSON before exposing INIT to user code.
        if (!(Json.parse(ctx.configJson) instanceof Map)) throw new IOException("INIT must be a JSON object");
        nameProcess();
        boolean started = false; Throwable failure = null;
        try {
            for (int i = 0; i < ctx.parameters.length; ++i)
                if (ctx.parameterRead[i]) ctx.parameterUpdate(read(stream), true);
            started = true; ctx.owner = Thread.currentThread();
            ctx.initializing = true;
            try { callbacks.start(); } finally { ctx.owner = null; }
            ctx.initializing = false;
            send(ctx.stream, 'R', new byte[]{2});
            for (;;) {
                Frame event = read(stream);
                if (event.op == 'X') { if (event.body.length != 0) throw new IOException("invalid STOP"); break; }
                if (event.op == 'U') { ctx.parameterUpdate(event, false); continue; }
                if (event.op == 'E') { ctx.recordingUpdate(event.body); continue; }
                if (event.op != 'T') throw new IOException("unexpected CH/2 opcode");
                ctx.activate(event, callbacks); send(ctx.stream, 'F', new byte[0]);
            }
        } catch (Throwable error) { failure = error; throw error; }
        finally {
            ctx.owner = null; ctx.origin = null; ctx.originEvent = null; ctx.initializing = false;
            if (started) try { callbacks.stop(); }
            catch (Throwable stop) { if (failure != null) failure.addSuppressed(stop); else throw stop; }
        }
    }
}
