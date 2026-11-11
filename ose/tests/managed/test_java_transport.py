"""JNI transport must preserve bytes and wake blocked I/O when closed."""
import os
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import tempfile
import unittest

from ose.modeling.java_transport_build import build as build_transport
from ose.modeling.managed_containers import tool
from ose.modeling.native_containers import sdk_root
from tests.component_driver import run_component


PROBE = '''import java.io.*;
import java.util.concurrent.CountDownLatch;
import org.sdlose.NativeTransport;
public final class TransportProbe {
    public static void main(final String[] args) throws Exception {
        final NativeTransport transport = NativeTransport.instance();
        if (args[0].equals("echo")) {
            byte[] buffer = new byte[65536]; int count;
            while ((count = transport.input().read(buffer)) != -1) transport.output().write(buffer, 0, count);
            transport.close();
        } else {
            final CountDownLatch entered = new CountDownLatch(1);
            final Throwable[] failure = new Throwable[1];
            Thread worker = new Thread(new Runnable() {
                public void run() {
                    entered.countDown();
                    try {
                        if (args[0].equals("read")) transport.input().read();
                        else transport.output().write(new byte[8 * 1024 * 1024]);
                    } catch (IOException expected) {
                        // Closing a transport may end reads with EOF or IOException.
                    } catch (Throwable error) { failure[0] = error; }
                }
            });
            worker.start(); entered.await(); Thread.sleep(100);
            transport.close(); transport.close();
            worker.join(3000);
            if (worker.isAlive()) throw new AssertionError("blocked native I/O survived close");
            if (failure[0] != null) throw new AssertionError(failure[0]);
            try { transport.output().write(1); throw new AssertionError("write after close"); }
            catch (IOException expected) {}
        }
        System.out.println("OK");
    }
}
'''


@unittest.skipUnless(shutil.which('javac') and shutil.which('cc'), 'requires JDK and C compiler')
class JavaTransportTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        temporary = tempfile.TemporaryDirectory(prefix='ose JNI transport ')
        cls.addClassCleanup(temporary.cleanup)
        cls.root = Path(temporary.name)
        bridge = build_transport(cls.root / 'native')
        source = cls.root / 'TransportProbe.java'
        source.write_text(PROBE)
        classes = cls.root / 'classes'
        subprocess.run([sys.executable, '-m', 'ose.modeling.java_build', str(classes),
                        str(sdk_root() / 'java/org/sdlose/NativeTransport.java'), str(source)], check=True)
        cls.command = tool('JAVA', 'java') + ['-Xcheck:jni', '-Dose.component.transport.library=' + str(bridge),
                                              '-cp', str(classes), 'TransportProbe']

    def test_large_binary_echo_and_standard_output_are_separate(self):
        payload = bytes(range(256)) * 8192
        result = run_component(self.command + ['echo'], input=payload, timeout=20)
        self.assertEqual(result.returncode, 0, result.stderr.decode())
        self.assertEqual(result.stdout, payload)
        self.assertEqual(result.application_stdout, b'OK\n')
        self.assertEqual(result.stderr, b'')

    def check_close(self, operation):
        parent, child = socket.socketpair()
        environment = dict(os.environ, OSE_IPC_FD=str(child.fileno()))
        try:
            process = subprocess.Popen(self.command + [operation], env=environment,
                                       pass_fds=(child.fileno(),), stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            child.close()
            try:
                out, err = process.communicate(timeout=10)
            finally:
                if process.poll() is None:
                    process.kill(); process.communicate()
            self.assertEqual(process.returncode, 0, err.decode())
            self.assertEqual(out, b'OK\n')
            self.assertEqual(err, b'')
        finally:
            child.close(); parent.close()

    def test_close_wakes_blocked_read(self): self.check_close('read')
    def test_close_wakes_blocked_write(self): self.check_close('write')

    def test_no_legacy_stdio_fallback_without_descriptor(self):
        environment = dict(os.environ)
        environment.pop('OSE_IPC_FD', None)
        result = subprocess.run(self.command + ['echo'], env=environment, capture_output=True, timeout=10)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn(b'OSE_IPC_FD', result.stderr)
        self.assertEqual(result.stdout, b'')
