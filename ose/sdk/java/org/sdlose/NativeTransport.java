package org.sdlose;

import java.io.IOException;
import java.io.InputStream;
import java.io.OutputStream;

/** Private socketpair transport. No application code should need this class. */
public final class NativeTransport implements java.io.Closeable {
    private int fd;
    private int active;
    private boolean closing;

    private static final class Holder {
        static final NativeTransport INSTANCE = new NativeTransport();
    }

    private NativeTransport() {
        String library = System.getProperty("ose.component.transport.library");
        if (library == null || library.length() == 0)
            throw new IllegalStateException("missing OSE component transport library; rebuild the container");
        System.load(library);
        try { fd = open0(); }
        catch (IOException failure) { throw new IllegalStateException(failure.getMessage(), failure); }
    }

    public static NativeTransport instance() { return Holder.INSTANCE; }

    private synchronized int begin() throws IOException {
        if (closing) throw new IOException("component IPC closed");
        active++;
        return fd;
    }

    private synchronized void end() {
        active--;
        if (closing && active == 0) release();
    }

    private void release() {
        if (fd >= 0) { close0(fd); fd = -1; }
    }

    public synchronized void close() {
        if (closing) return;
        closing = true;
        // Wake blocked native I/O before waiting for its users to release fd.
        shutdown0(fd);
        if (active == 0) release();
    }

    private static void bounds(byte[] bytes, int offset, int length) {
        if (bytes == null) throw new NullPointerException();
        if (offset < 0 || length < 0 || offset > bytes.length - length)
            throw new IndexOutOfBoundsException();
    }

    private final InputStream input = new InputStream() {
        public int read() throws IOException {
            byte[] one = new byte[1];
            return read(one, 0, 1) == -1 ? -1 : one[0] & 255;
        }
        public synchronized int read(byte[] bytes, int offset, int length) throws IOException {
            bounds(bytes, offset, length);
            if (length == 0) return 0;
            int socket = begin();
            try { return read0(socket, bytes, offset, length); }
            finally { end(); }
        }
        public void close() { NativeTransport.this.close(); }
    };

    private final OutputStream output = new OutputStream() {
        public void write(int value) throws IOException { write(new byte[]{(byte)value}, 0, 1); }
        public synchronized void write(byte[] bytes, int offset, int length) throws IOException {
            bounds(bytes, offset, length);
            int socket = begin();
            try {
                while (length > 0) {
                    int count = write0(socket, bytes, offset, length);
                    offset += count; length -= count;
                }
            } finally { end(); }
        }
        public void close() { NativeTransport.this.close(); }
    };

    public InputStream input() { return input; }
    public OutputStream output() { return output; }

    private static native int open0() throws IOException;
    private static native int read0(int fd, byte[] bytes, int offset, int length) throws IOException;
    private static native int write0(int fd, byte[] bytes, int offset, int length) throws IOException;
    private static native void shutdown0(int fd);
    private static native void close0(int fd);
}
