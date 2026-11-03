"""Validation-only reading must reject everything the dynamic decoder rejects."""
import random
import struct
import unittest
from unittest.mock import patch

import sdl_runtime as runtime


class Flags(runtime.SdlMessage):
   _SDL_NAME = 'Flags'
   _SDL_DESCRIPTOR = 'SDL2|Flags{1+:bool:items;}'
   _SDL_FIELDS = ((1, 'items', 'packed', 'bool', ()),)


class ValidationTests(unittest.TestCase):
   def assert_same_acceptance(self, context, payloads):
      for payload in payloads:
         accepted = []
         for method in (runtime.decode_dynamic, runtime.validate_payload):
            try:
               method(context, payload)
               accepted.append(True)
            except runtime.CodecError:
               accepted.append(False)
         with self.subTest(payload=payload):
            self.assertEqual(accepted[0], accepted[1])

   def test_01_codec_validation_without_value_construction(self):
      text = ('SDL2|Data{1+:fl32:samples;2:fl64[2][3]:matrix;'
         '3:c32:gain;4:Mode:mode;}|#Mode{OFF=0;ON=1;}')
      context = runtime.prepare(text)
      payload = (b'\x01\x80\x80\x01' + struct.pack('>f', .5) * 16384
         + struct.pack('>6d2fi', float('nan'), float('inf'), -float('inf'), -0., 1., 2., 3., 4., 1))
      self.assertEqual(len(runtime.decode_dynamic(context, payload).fields['samples']), 16384)
      with patch.object(runtime, 'SdlDynamicMessage', side_effect=AssertionError('message allocated')), \
            patch.object(runtime, 'SdlDynamicEnum', side_effect=AssertionError('enum allocated')), \
            patch.object(runtime, 'Complex32', side_effect=AssertionError('complex allocated')):
         for data in (payload, bytearray(payload), memoryview(payload)):
            self.assertIsNone(runtime.validate_payload(context, data, root='Data'))
      self.assertIsNone(runtime.validate_payload(context, payload))
      for end in (0, 1, 3, 65539, len(payload) - 1):
         with self.subTest(end=end), self.assertRaises(runtime.CodecError):
            runtime.validate_payload(context, payload[:end], root='Data')

   def test_01_codec_empty_messages_and_multibyte_root_ids(self):
      context = runtime.prepare('SDL2|Empty{}')
      self.assertIsNone(runtime.validate_payload(context, b'\x01', root='Empty'))
      text = 'SDL2|' + '|'.join(f'M{n:03d}{{1:uint8:value;}}' for n in range(130))
      context = runtime.prepare(text)
      self.assertEqual(context.ids['M129'], 130)
      self.assertIsNone(runtime.validate_payload(context, b'\x82\x01\xff', root='M129'))
      for payload in (b'\x00', b'\x83\x01\x00', b'\x82\x81\x00\xff'):
         with self.subTest(payload=payload), self.assertRaises(runtime.CodecError):
            runtime.validate_payload(context, payload)

   def test_02_invalid_nested_fields_and_mutated_corpus(self):
      text = ('SDL2|Child{1:bool:enabled;2:Mode:mode;}|Root{1?:Child:child;'
         '2*:bool:flags;3+:Mode:modes;4:string:label;5:uint32[2][2]:grid;}'
         '|#Mode{OFF=0;ON=1;}')
      context = runtime.prepare(text)
      label = 'été\0'.encode()
      payload = (b'\x02\x01\x01' + struct.pack('>i', 1) + b'\x03\x00\x01\x01\x02'
         + struct.pack('>ii', 0, 1) + bytes([len(label)]) + label + struct.pack('>4I', 1, 2, 3, 4))
      self.assertIsNone(runtime.validate_payload(context, payload, root='Root'))
      payloads = [payload[:n] for n in range(len(payload))] + [payload, payload + b'\x00']
      for offset in range(len(payload)):
         for value in (0, 1, 2, 127, 128, 255):
            mutated = bytearray(payload)
            mutated[offset] = value
            payloads.append(bytes(mutated))
      rng = random.Random(4815)
      payloads.extend(rng.randbytes(rng.randrange(60)) for unused in range(200))
      self.assert_same_acceptance(context, payloads)

   def test_02_invalid_booleans_and_enums_in_every_shape(self):
      for name, valid, invalid, enum in (
            ('bool', b'\x01', b'\x02', ''),
            ('Mode', struct.pack('>i', 1), struct.pack('>i', 2), '|#Mode{OFF=0;ON=1;}')):
         for modifier, dimensions, prefix, suffix in (
               ('', '', b'', b''), ('?', '', b'\x01', b''),
               ('*', '', b'\x02', valid), ('+', '', b'\x02', valid),
               ('', '[2]', b'', valid)):
            context = runtime.prepare(f'SDL2|Root{{1{modifier}:{name}{dimensions}:items;}}{enum}')
            self.assertIsNone(runtime.validate_payload(context, b'\x01' + prefix + valid + suffix))
            with self.subTest(type=name, modifier=modifier, dimensions=dimensions), \
                  self.assertRaises(runtime.CodecError):
               runtime.validate_payload(context, b'\x01' + prefix + invalid + suffix)
         context = runtime.prepare(f'SDL2|Child{{1:{name}:value;}}|Root{{1+:Child:items;}}{enum}')
         with self.subTest(type=name, nested=True), self.assertRaises(runtime.CodecError):
            runtime.validate_payload(context, b'\x02\x02' + valid + invalid)
      # A typed context may have Packed/native fast paths. They must not bypass
      # validation of discarded booleans.
      context = runtime.prepare(Flags._SDL_DESCRIPTOR, Flags)
      self.assertIn(('bool', ()), context.packed_codecs)
      self.assertIsNone(runtime.validate_payload(context, b'\x01\x02\x00\x01'))
      with self.assertRaises(runtime.CodecError):
         runtime.validate_payload(context, b'\x01\x02\x00\x02')

   def test_02_invalid_utf8_counters_roots_and_trailing_bytes(self):
      context = runtime.prepare('SDL2|Root{1?:string:label;}')
      for payload in (b'', b'\x00', b'\x02', b'\x01\x02',
            b'\x01\x01\x01\xff', b'\x01\x01\x80\x00',
            b'\x01\x01\xff\xff\xff\xff\x10', b'\x01\x00\x00'):
         with self.subTest(payload=payload), self.assertRaises(runtime.CodecError):
            runtime.validate_payload(context, payload)
      with self.assertRaisesRegex(runtime.CodecError, 'unexpected root'):
         runtime.validate_payload(context, b'\x01\x00', root='Other')
      with self.assertRaises(runtime.CodecError):
         runtime.validate_payload(None, b'\x01')

   def test_02_invalid_representation_padding_and_quantized_values(self):
      context = runtime.prepare('SDL2|Root{1:bool:flag@b1;2:fl64:value@b12,q0.25;}')
      payload = bytes.fromhex('018010')  # true, 0.5, three zero padding bits.
      self.assertEqual(runtime.decode_dynamic(context, payload).fields['value'], .5)
      self.assertIsNone(runtime.validate_payload(context, payload, root='Root'))
      self.assert_same_acceptance(context, [payload[:n] for n in range(len(payload))]
         + [payload, payload + b'\x00', bytes.fromhex('018011')])
      with self.assertRaises(runtime.CodecError):
         runtime.validate_payload(context, bytes.fromhex('018011'))

   def test_03_limits_variable_sequences(self):
      context = runtime.prepare('SDL2|Root{1*:string:items;}')
      with self.assertRaisesRegex(runtime.CodecError, 'variable sequence too large'):
         runtime.validate_payload(context, b'\x01\x81\x80\x40')
