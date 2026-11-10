package org.sdlose;

import java.io.*;
import java.math.BigInteger;
import java.util.Arrays;

/** CH/2 fields and owned input references. All uint64 values retain their full range. */
public final class InputProtocol {
    private InputProtocol() {}
    static final BigInteger MAX_U64 = BigInteger.ONE.shiftLeft(64).subtract(BigInteger.ONE);
    public enum Event { MESSAGE, TIMEOUT }
    public enum Mode { PUSH, PULL, PERIODIC, ONE_SHOT }

    public static final class Reference<T> {
        public final String port, handle, source;
        public final Event event;
        public final T message;
        /** null is absence; zero is a valid explicit ID. */
        public final BigInteger appId;
        public final BigInteger elapsed, dueNs;
        Reference(String port, Event event, T message, String handle, String source,
                  BigInteger appId, BigInteger elapsed, BigInteger dueNs) {
            this.port = port; this.event = event; this.message = message;
            this.handle = handle; this.source = source; this.appId = appId;
            this.elapsed = elapsed; this.dueNs = dueNs;
        }
        public boolean timedOut() { return event == Event.TIMEOUT; }
        public <U> Reference<U> asType(Class<U> type) {
            return new Reference<U>(port, event, type.cast(message), handle, source, appId, elapsed, dueNs);
        }
    }

    /** Immutable selectors, combined by intersection. Empty selects the oldest record. */
    public static final class Selector {
        public final BigInteger appId;
        public final String handle, source;
        public Selector() { this(null, null, null); }
        private Selector(BigInteger id, String handle, String source) {
            this.appId = id; this.handle = handle; this.source = source;
        }
        public Selector id(BigInteger id) {
            uint64(id); return new Selector(id, handle, source);
        }
        public Selector handle(String handle) {
            if (handle == null || handle.length() == 0) throw new IllegalArgumentException("empty engine handle");
            return new Selector(appId, handle, source);
        }
        public Selector source(String source) {
            if (source == null || source.length() == 0) throw new IllegalArgumentException("empty source");
            return new Selector(appId, handle, source);
        }
    }

    static void uint64(BigInteger value) {
        if (value == null || value.signum() < 0 || value.compareTo(MAX_U64) > 0)
            throw new IllegalArgumentException("expected uint64");
    }
    private static final class Reader {
        final DataInputStream input;
        Reader(byte[] bytes) throws IOException {
            if (bytes == null || bytes.length >= Component.MAX_FRAME) throw new IOException("CH/2 body exceeds limit");
            input = new DataInputStream(new ByteArrayInputStream(bytes));
        }
        int flag() throws IOException {
            int value = input.readUnsignedByte();
            if (value > 1) throw new IOException("invalid CH/2 presence flag");
            return value;
        }
        byte[] take(int size) throws IOException {
            if (size < 0 || size > input.available()) throw new IOException("truncated CH/2 field");
            byte[] bytes = new byte[size]; input.readFully(bytes); return bytes;
        }
        BigInteger number() throws IOException { return new BigInteger(1, take(8)); }
        BigInteger optional() throws IOException { return flag() == 0 ? null : number(); }
        String text(boolean wide) throws IOException {
            int size = wide ? input.readInt() : input.readUnsignedShort();
            if (size < 1) throw new IOException("empty or oversized CH/2 text");
            return Component.text(take(size));
        }
        void end() throws IOException { if (input.available() != 0) throw new IOException("trailing CH/2 bytes"); }
    }
    static final class Record {
        final String handle, source, catalogue;
        final BigInteger appId, receivedNs;
        final byte[] envelope;
        Record(Reader reader) throws IOException {
            handle = reader.text(false); source = reader.text(false); appId = reader.optional();
            receivedNs = reader.number(); catalogue = reader.text(true);
            envelope = reader.take(reader.input.available());
            if (envelope.length == 0) throw new IOException("empty CH/2 message envelope");
        }
    }
    static Record record(byte[] bytes) throws IOException { return record(new Reader(bytes)); }
    private static Record record(Reader reader) throws IOException {
        Record result = reader.flag() == 0 ? null : new Record(reader);
        reader.end(); return result;
    }
    static final class Activation {
        final BigInteger timeNs, index, dueNs, elapsed;
        final String port;
        final Event event;
        final Record record;
        Activation(byte[] bytes) throws IOException {
            Reader reader = new Reader(bytes);
            timeNs = reader.number(); index = reader.number(); dueNs = reader.number(); elapsed = reader.number();
            int code = reader.input.readUnsignedByte();
            if (code != 1 && code != 2) throw new IOException("invalid CH/2 event");
            event = code == 1 ? Event.MESSAGE : Event.TIMEOUT;
            port = reader.text(false); record = record(reader);
            if (index.signum() == 0 || elapsed.signum() == 0 || dueNs.compareTo(timeNs) > 0 ||
                (event == Event.MESSAGE) != (record != null) ||
                (record != null && (!elapsed.equals(BigInteger.ONE) || record.receivedNs.compareTo(timeNs) > 0)))
                throw new IOException("invalid CH/2 activation context");
        }
    }
    private static void number(DataOutputStream output, BigInteger value) throws IOException {
        uint64(value); byte[] bytes = value.toByteArray();
        byte[] field = new byte[8];
        int size = Math.min(8, bytes.length);
        System.arraycopy(bytes, bytes.length - size, field, 8 - size, size); output.write(field);
    }
    private static void optional(DataOutputStream output, BigInteger value) throws IOException {
        output.writeByte(value == null ? 0 : 1);
        if (value != null) number(output, value);
    }
    private static void text(DataOutputStream output, String value) throws IOException {
        if (value == null) throw new IllegalArgumentException("null CH/2 text");
        byte[] bytes = Component.utf8(value);
        if (bytes.length == 0 || bytes.length > 65535) throw new IOException("invalid CH/2 text size");
        output.writeShort(bytes.length); output.write(bytes);
    }
    static byte[] publication(String port, byte[] payload, BigInteger id) throws IOException {
        if (payload == null || payload.length == 0) throw new IOException("empty CH/2 publication");
        ByteArrayOutputStream bytes = new ByteArrayOutputStream(); DataOutputStream output = new DataOutputStream(bytes);
        text(output, port); optional(output, id); output.write(payload);
        if (bytes.size() >= Component.MAX_FRAME) throw new IOException("CH/2 publication exceeds limit");
        return bytes.toByteArray();
    }
    static byte[] request(int operation, String port, Selector selector, BigInteger delay) throws IOException {
        if (operation < 1 || operation > 5) throw new IllegalArgumentException("unknown input operation");
        ByteArrayOutputStream bytes = new ByteArrayOutputStream(); DataOutputStream output = new DataOutputStream(bytes);
        output.writeByte(operation); text(output, port);
        if (operation <= 3) {
            if (delay != null) throw new IllegalArgumentException("message access cannot arm a timer");
            if (selector == null) selector = new Selector();
            int flags = (selector.appId == null ? 0 : 1) | (selector.handle == null ? 0 : 2) | (selector.source == null ? 0 : 4);
            output.writeByte(flags);
            if (selector.appId != null) number(output, selector.appId);
            if (selector.handle != null) text(output, selector.handle);
            if (selector.source != null) text(output, selector.source);
        } else {
            if (selector != null || (operation == 5 && delay != null)) throw new IllegalArgumentException("invalid timer request");
            if (operation == 4) {
                if (delay != null && delay.signum() == 0) throw new IllegalArgumentException("timer delay must be positive");
                optional(output, delay);
            }
        }
        if (bytes.size() >= Component.MAX_FRAME) throw new IOException("CH/2 request exceeds limit");
        return bytes.toByteArray();
    }
    static byte[] reply(Component.Frame frame) throws IOException {
        if (frame.op != 'A' || frame.body.length == 0) throw new IOException("invalid CH/2 reply");
        if (frame.body[0] == 0) {
            if (frame.body.length > 4097) throw new IOException("CH/2 error exceeds limit");
            throw new IOException(Component.text(Arrays.copyOfRange(frame.body, 1, frame.body.length)));
        }
        if (frame.body[0] != 1) throw new IOException("invalid CH/2 reply status");
        return Arrays.copyOfRange(frame.body, 1, frame.body.length);
    }
}
