"""Framed TCP reads preserve stream boundaries, ownership and flow control."""
import asyncio
import struct
import unittest

from ose.transport.protocol import encode
from ose.transport.tcp import TCP, _FrameReader, open_connection, start_server


def wire(header, body=b''):
    packet = encode(header, body)
    return struct.pack('>I', len(packet)) + packet


class FrameReaderTests(unittest.IsolatedAsyncioTestCase):
    async def test_coalesced_frames_keep_independent_immutable_payloads(self):
        reader = _FrameReader()
        tcp = TCP(reader, None)
        first, second = {'op': 'data', 'id': 1}, {'op': 'data', 'id': 2}
        reader.feed_data(wire(first, b'\x00\xfffirst') + wire(second, b'second'))
        header, payload = await tcp.receive()
        self.assertEqual((header, payload), (first, b'\x00\xfffirst'))
        self.assertIs(type(payload), bytes)
        self.assertEqual(await tcp.receive(), (second, b'second'))
        reader.feed_data(wire(second, b'new contents'))
        await tcp.receive()
        self.assertEqual(payload, b'\x00\xfffirst')
        self.assertEqual(len(reader._buffer), 0)

    async def test_every_split_of_a_frame_and_partial_next_prefix(self):
        header, payload = {'op': 'data', 'label': 'entrée'}, b'\x00\xffpayload'
        packet = wire(header, payload)
        following = wire({'op': 'ack'})
        for split in range(len(packet) + 1):
            with self.subTest(split=split):
                reader = _FrameReader()
                reader.feed_data(packet[:split])
                receiving = asyncio.create_task(reader.read_frame(1024))
                await asyncio.sleep(0)
                reader.feed_data(packet[split:] + following[:2])
                self.assertEqual(await asyncio.wait_for(receiving, 1), (header, payload))
                reader.feed_data(following[2:])
                self.assertEqual(await reader.read_frame(1024), ({'op': 'ack'}, b''))

    async def test_cancelled_partial_read_can_resume_at_the_same_frame(self):
        packet = wire({'op': 'data'}, b'payload')
        reader = _FrameReader()
        reader.feed_data(packet[:9])
        receiving = asyncio.create_task(reader.read_frame(1024))
        await asyncio.sleep(0)
        receiving.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await receiving
        reader.feed_data(packet[9:])
        self.assertEqual(await reader.read_frame(1024), ({'op': 'data'}, b'payload'))

    async def test_eof_reports_the_incomplete_prefix_or_body(self):
        for data, partial, expected in [(b'', b'', 4), (b'\x00\x00', b'\x00\x00', 4),
                                        (struct.pack('>I', 8) + b'CE', b'CE', 8)]:
            with self.subTest(data=data):
                reader = _FrameReader()
                reader.feed_data(data)
                reader.feed_eof()
                with self.assertRaises(asyncio.IncompleteReadError) as caught:
                    await reader.read_frame(1024)
                self.assertEqual((caught.exception.partial, caught.exception.expected), (partial, expected))
                self.assertEqual(len(reader._buffer), 0)

    async def test_invalid_lengths_fail_before_waiting_for_a_body(self):
        for size in (0, 7, 1025, 0xffffffff):
            with self.subTest(size=size):
                reader = _FrameReader()
                reader.feed_data(struct.pack('>I', size))
                with self.assertRaisesRegex(ValueError, 'invalid TCP frame length'):
                    await asyncio.wait_for(reader.read_frame(1024), 1)

    async def test_exact_maximum_frame_keeps_its_full_payload(self):
        limit = 1024 * 1024
        body = b'x' * (limit - len(encode({'op': 'data'})))
        packet = wire({'op': 'data'}, body)
        self.assertEqual(len(packet), limit + 4)
        reader = _FrameReader()
        reader.feed_data(packet)
        self.assertEqual(await reader.read_frame(limit), ({'op': 'data'}, body))
        reader.feed_data(packet)
        with self.assertRaisesRegex(ValueError, 'invalid TCP frame length'):
            await reader.read_frame(limit - 1)

    async def test_concurrent_waiting_readers_are_rejected(self):
        reader = _FrameReader()
        receiving = asyncio.create_task(reader.read_frame(1024))
        await asyncio.sleep(0)
        try:
            with self.assertRaisesRegex(RuntimeError, 'another coroutine'):
                await reader.read_frame(1024)
        finally:
            receiving.cancel()
            await asyncio.gather(receiving, return_exceptions=True)

    async def test_bad_metadata_cannot_read_into_the_next_frame(self):
        packets = [b'XX\x02\0' + struct.pack('>I', 0),
                   b'CE\x01' + struct.pack('>I', 2) + b'{}',
                   b'CE\x02\0' + struct.pack('>I', 65537),
                   b'CE\x02\0' + struct.pack('>I', 100),
                   b'CE\x02\0' + struct.pack('>I', 1) + b'\xff',
                   b'CE\x02\x7f' + struct.pack('>I', 0)]
        for packet in packets:
            with self.subTest(packet=packet):
                reader = _FrameReader()
                reader.feed_data(struct.pack('>I', len(packet)) + packet + wire({'op': 'ack'}))
                with self.assertRaises((ValueError, UnicodeError)):
                    await reader.read_frame(1024)
                self.assertEqual(await reader.read_frame(1024), ({'op': 'ack'}, b''))

    async def test_frame_larger_than_read_watermark_resumes_then_pauses(self):
        class Transport:
            paused = False
            def pause_reading(self): self.paused = True
            def resume_reading(self): self.paused = False
        transport = Transport()
        reader = _FrameReader(limit=16)
        reader.set_transport(transport)
        packet = wire({'op': 'data'}, b'x' * 200)
        reader.feed_data(packet[:40])
        self.assertTrue(transport.paused)
        receiving = asyncio.create_task(reader.read_frame(1024))
        await asyncio.sleep(0)
        self.assertFalse(transport.paused)  # Incomplete frames must not deadlock.
        reader.feed_data(packet[40:] + wire({'op': 'data'}, b'y' * 80))
        self.assertEqual(await asyncio.wait_for(receiving, 1), ({'op': 'data'}, b'x' * 200))
        self.assertTrue(transport.paused)  # The slow consumer still bounds read-ahead.
        self.assertEqual(await reader.read_frame(1024), ({'op': 'data'}, b'y' * 80))
        self.assertFalse(transport.paused)

    async def test_socket_round_trip_uses_framed_reader_in_both_directions(self):
        complete = asyncio.get_running_loop().create_future()
        async def echo(reader, writer):
            peer = TCP(reader, writer)
            try:
                self.assertIsInstance(reader, _FrameReader)
                for _ in range(3):
                    header, body = await peer.receive()
                    await peer.send(header, body)
                complete.set_result(None)
            except BaseException as error:
                if not complete.done():
                    complete.set_exception(error)
            finally:
                await peer.close()
        server = await start_server(echo, '127.0.0.1', 0)
        reader, writer = await open_connection('127.0.0.1', server.sockets[0].getsockname()[1])
        peer = TCP(reader, writer)
        try:
            self.assertIsInstance(reader, _FrameReader)
            for size in (0, 65536, 200000):
                await peer.send({'op': 'data'}, b'x' * size)
                self.assertEqual(await asyncio.wait_for(peer.receive(), 2), ({'op': 'data'}, b'x' * size))
            await asyncio.wait_for(complete, 2)
        finally:
            await peer.close()
            server.close()
            await server.wait_closed()
