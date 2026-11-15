"""Native validation and framing compared with the Python fallback."""
import asyncio
import os
from pathlib import Path
import random
import struct
import unittest
from unittest.mock import patch
from types import SimpleNamespace
from ose.acceleration import native
from ose.codec.sdl2 import SDL2
from ose.parameters.values import Values
from ose.execution import ipc
from ose.execution import input_ipc as wire
SDL_ROOT = Path(os.environ.get('SDL_ROOT', Path(__file__).resolve().parents[3] / 'sdl'))

@unittest.skipIf(native is None, "build OSE native extension")
class NativeKernelTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.codec = SDL2(SDL_ROOT)

    def fixtures(self):
        catalogue = ('SDL2|Child{1:bool:flag;2:string:text;}|Packet{1:uint64:id;'
                     '2:Child:child;3+:fl32:samples;4*:Child:children;5?:string:note;6:Mode:mode;}'
                     '|#Mode{OFF=0;ON=1;}')
        values = Values(self.codec.api, catalogue, 'Packet')
        for size in (0, 1, 256, 8192):
            body = values.encode(dict(id=2**64-1, child=dict(flag=True, text='é水'),
                samples=[.25] * size, children=[dict(flag=False, text='hello')],
                note=None if size % 2 else 'optional', mode='ON'))
            yield catalogue, 'Packet', self.codec.wrap(catalogue, body)
        catalogue = ('SDL2|Leaf{1:bool[2]:flags;2:Mode:mode;}|Matrix{1+:Leaf:items;'
                     '2:uint16[2][2]:counts;3+:bool:bits;}|#Mode{OFF=0;ON=1;}')
        body = Values(self.codec.api, catalogue, 'Matrix').encode(dict(
            items=[dict(flags=[True, False], mode='ON')],
            counts=[[1, 2], [3, 4]], bits=[True, False, True]))
        yield catalogue, 'Matrix', self.codec.wrap(catalogue, body)

    def test_record_and_activation_bytes_and_limits_match_fallback(self):
        for app_id in (None, 0, 2**64 - 1):
            for length in (1, 65536, 1048500):
                record = dict(id='é', topic='source.out', catalogue='SDL2|X{}',
                              body=b'x' * length, received_ns=2**64 - 1)
                if app_id is not None:
                    record['app_id'] = app_id
                ref = SimpleNamespace(time_ns=2**64-1, due_ns=0, elapsed=1,
                                      port='input', event='message', record=record)
                with patch.object(wire, 'native', None):
                    expected_record = wire.record_data(record)
                    expected_activation = wire.activation_data(ref, 2**64-1)
                    try:
                        wire.check_record_size('input', record)
                        fits = True
                    except ValueError:
                        fits = False
                self.assertEqual(native.record_data(record), expected_record)
                self.assertEqual(native.activation_data(ref, 2**64-1), expected_activation)
                if fits:
                    native.check_record_size('input', record)
                else:
                    with self.assertRaises(ValueError):
                        native.check_record_size('input', record)

    def test_native_validators_match_python_on_valid_and_mutated_messages(self):
        rng = random.Random(421)
        comparisons = 0
        for catalogue, root, envelope in self.fixtures():
            plans = [native.prepare(catalogue, root) for backend in (0,)]
            cases = [envelope, b'', envelope[:9], envelope + b'\0', envelope[:-1],
                     b'S2PS\x01' + envelope[5:], envelope[:5] + b'\0' * 4 + envelope[9:]]
            # Tiny payloads: every truncation and every single-byte corruption.
            limit = min(len(envelope), 100)
            cases += [envelope[:n] for n in range(limit)]
            for _ in range(150):
                damaged = bytearray(envelope)
                position = rng.randrange(len(damaged))
                damaged[position] ^= rng.randrange(1, 256)
                cases.append(bytes(damaged))
            for body in cases:
                try:
                    with patch('ose.codec.runtime.native', None):
                        self.codec.validate_only(catalogue, root, body)
                    expected = True
                except ValueError:
                    expected = False
                for backend, plan in enumerate(plans):
                    with self.subTest(backend=backend, size=len(body)):
                        try:
                            native.validate(plan, body)
                            actual = True
                        except ValueError:
                            actual = False
                        self.assertEqual(actual, expected)
                        comparisons += 1
        self.assertGreater(comparisons, 1000)

class NativeReceiveTests(unittest.IsolatedAsyncioTestCase):
    async def test_fragmentation_coalescing_and_large_frames_preserve_order(self):
        frames = [('V', b'first'), ('Q', b'inbox'), ('P', b'x' * 70000), ('F', b'')]
        packet = b''.join(ipc.frame(op, body) for op, body in frames)
        for cut in (0, 1, 3, 4, 5, 9, 17, 65536, len(packet)-1, len(packet)):
            reader = asyncio.StreamReader()
            receive = ipc.receive
            async def collect():
                return [await receive(reader) for _ in frames]
            task = asyncio.create_task(collect())
            reader.feed_data(packet[:cut])
            await asyncio.sleep(0)
            reader.feed_data(packet[cut:])
            reader.feed_eof()
            self.assertEqual(await asyncio.wait_for(task, 1), frames)
            with self.assertRaises(asyncio.IncompleteReadError) as error:
                await receive(reader)
            self.assertEqual(error.exception.partial, b'')
            self.assertEqual(error.exception.expected, 4)

    async def test_truncated_and_invalid_frames_match_existing_receiver(self):
        packets = [b'', b'\0', b'\0\0\0', struct.pack('>I', 8) + b'Vhi',
                   struct.pack('>I', 0), struct.pack('>I', ipc.MAX_SIZE+1),
                   ipc.frame('V', b'ok') + struct.pack('>I', 0)]
        for packet in packets:
            outcomes = []
            for enabled in (False, True):
                original = ipc.native
                ipc.native = original if enabled else None
                receive = ipc.receive
                reader = asyncio.StreamReader()
                reader.feed_data(packet)
                reader.feed_eof()
                values = []
                try:
                    while True:
                        values.append(await receive(reader))
                except asyncio.IncompleteReadError as error:
                    outcomes.append((values, type(error), error.partial, error.expected))
                except ValueError as error:
                    outcomes.append((values, type(error), str(error)))
                ipc.native = original
            self.assertEqual(outcomes[0], outcomes[1])
