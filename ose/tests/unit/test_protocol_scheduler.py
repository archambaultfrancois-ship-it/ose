import io
import unittest
from ose.execution.input_process import InputSession
from ose.execution.ipc import frame, unpack_port
from ose.transport.protocol import encode, decode
from tests.component_driver import message_frame, timer_frame


class ProtocolTests(unittest.TestCase):
    def test_binary_round_trip_and_limits(self):
        packet = encode({"op": "data", "id": "x"}, b"\0\xff")
        self.assertEqual(decode(packet), ({"op": "data", "id": "x"}, b"\0\xff"))
        for invalid in (b"", b"CE\x01\xff\xff\xff\xff", packet[:8]):
            with self.assertRaises((ValueError, UnicodeError)):
                decode(invalid)
        with self.assertRaises(ValueError):
            encode({"op": "data"}, b"x" * 1200, 1200)

    def test_sdk_lifecycle(self):
        reader = io.BytesIO(frame("I", b'{"parameters":{"gain":2}}') +
                            message_frame(1, "input", "SDL2|", b"wire", app_id=0) + frame("X"))
        writer = io.BytesIO()
        session = InputSession(reader, writer)
        self.assertEqual(session.config["parameters"]["gain"], 2)
        references = list(session.activations())
        self.assertEqual(len(references), 1)
        reference = references[0]
        self.assertEqual((reference.port, reference.event, reference.index), ("input", "message", 1))
        self.assertEqual((reference.record["catalogue"], reference.record["body"], reference.record["app_id"]),
                         ("SDL2|", b"wire", 0))
        self.assertEqual(writer.getvalue(), frame("R", b"\2") + frame("F"))
        with self.assertRaises(ValueError):
            unpack_port(b"\0\x04x", True)

    def test_ready_can_be_deferred_until_user_initialization_succeeds(self):
        writer = io.BytesIO()
        session = InputSession(io.BytesIO(frame("I", b"{}")), writer, auto_ready=False)
        self.assertEqual(writer.getvalue(), b"")
        session.ready()
        session.ready()
        self.assertEqual(writer.getvalue(), frame("R", b"\2"))

    def test_timeout_reference_and_duplicate_activation_detection(self):
        timeout = timer_frame(1, port="input")
        writer = io.BytesIO()
        session = InputSession(io.BytesIO(frame("I", b"{}") + timeout + frame("X")), writer)
        reference, = list(session.activations())
        self.assertEqual((reference.port, reference.event, reference.record), ("input", "timeout", None))
        self.assertEqual(writer.getvalue(), frame("R", b"\2") + frame("F"))
        for second in (timeout, message_frame(1, "input", "SDL2|", b"wire")):
            writer = io.BytesIO()
            session = InputSession(io.BytesIO(frame("I", b"{}") + timeout + second), writer)
            with self.assertRaisesRegex(ValueError, "non-monotonic"):
                list(session.activations())
            self.assertEqual(writer.getvalue(), frame("R", b"\2") + frame("F"))
