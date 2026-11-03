"""Prepared validation contexts are reusable without weakening per-message checks."""
from pathlib import Path
import struct
import unittest
from unittest.mock import patch

from ose.codec.runtime import Runtime
from ose.codec.sdl2 import SDL2


SDL_ROOT = Path(__file__).resolve().parents[3] / 'sdl'
CATALOGUE = 'SDL2|Value{1:uint32:count;}'


class RuntimeValidationTests(unittest.TestCase):
    def setUp(self):
        self.codec = SDL2(SDL_ROOT)
        self.api = self.codec.api
        preparing = patch.object(self.api, 'prepare', wraps=self.api.prepare)
        self.prepare = preparing.start()
        self.addCleanup(preparing.stop)
        self.runtime = Runtime(self.api)

    def envelope(self, catalogue=CATALOGUE, payload=b'\x01\x00\x00\x00\x01'):
        return self.codec.wrap(catalogue, payload)

    def test_reuses_preparation_but_decodes_fresh_values(self):
        first = self.runtime.validate(CATALOGUE, 'Value', self.envelope())
        first.fields['count'] = 99
        second = self.runtime.validate(CATALOGUE, 'Value', self.envelope())
        third = self.runtime.validate(CATALOGUE, 'Value', self.envelope(payload=b'\x01\x00\x00\x00\x02'))
        self.assertIsNot(first, second)
        self.assertEqual(second.fields, {'count': 1})
        self.assertEqual(third.fields, {'count': 2})
        self.prepare.assert_called_once_with(CATALOGUE)

    def test_rechecks_expected_root_for_each_message(self):
        catalogue = 'SDL2|First{1:uint32:count;}|Second{1:uint32:count;}'
        first = self.envelope(catalogue)
        second = self.envelope(catalogue, b'\x02\x00\x00\x00\x02')
        self.runtime.validate(catalogue, 'First', first)
        with self.assertRaisesRegex(ValueError, 'unexpected root message'):
            self.runtime.validate(catalogue, 'Second', first)
        self.assertEqual(self.runtime.validate(catalogue, 'Second', second).fields, {'count': 2})
        self.prepare.assert_called_once_with(catalogue)

    def test_warm_context_still_rejects_invalid_payloads_and_envelopes(self):
        catalogue = 'SDL2|Value{1:bool:enabled;2:Mode:mode;3?:string:label;}|#Mode{OFF=0;ON=1;}'
        valid = b'\x01\x01' + struct.pack('>i', 1) + b'\x00'
        self.runtime.validate(catalogue, 'Value', self.envelope(catalogue, valid))
        payloads = (
            valid[:-1],                       # Missing optional presence.
            b'\x01\x02' + valid[2:],          # Invalid boolean.
            b'\x01\x01' + struct.pack('>i', 3) + b'\x00',
            valid[:-1] + b'\x02',             # Invalid optional counter.
            valid + b'\x00',                  # Trailing bytes.
            b'\x02' + valid[1:],              # Unknown root ID.
        )
        frames = [self.envelope(catalogue, payload) for payload in payloads]
        frames.extend((self.envelope(CATALOGUE, valid), b'S2PS\x01' + b'\x00' * 8))
        for frame in frames:
            with self.subTest(frame=frame), self.assertRaises(ValueError):
                self.runtime.validate(catalogue, 'Value', frame)
        self.prepare.assert_called_once_with(catalogue)

    def test_full_catalogues_are_distinct_even_when_wire_ids_collide(self):
        other = 'SDL2|Value{1:bool:count;}'
        with patch.object(self.api, 'catalogue_id', return_value=7):
            integer = self.runtime.validate(CATALOGUE, 'Value', self.envelope())
            boolean = self.runtime.validate(other, 'Value', self.envelope(other, b'\x01\x01'))
        self.assertIs(type(integer.fields['count']), int)
        self.assertIs(boolean.fields['count'], True)
        self.assertEqual(self.prepare.call_count, 2)

    def test_prepared_contexts_are_bounded_and_recently_used_ones_survive(self):
        def validate(number):
            catalogue = f'SDL2|Value{number}{{1:uint32:count;}}'
            self.runtime.validate(catalogue, f'Value{number}', self.envelope(catalogue))
        for number in range(64):
            validate(number)
        self.assertEqual(self.prepare.call_count, 64)
        validate(0)
        validate(64)
        validate(0)
        self.assertEqual(self.prepare.call_count, 65)
        validate(1)
        self.assertEqual(self.prepare.call_count, 66)

    def test_runtime_instances_do_not_share_prepared_contexts(self):
        other = Runtime(self.api)
        for runtime in (self.runtime, other, self.runtime, other):
            runtime.validate(CATALOGUE, 'Value', self.envelope())
        self.assertEqual(self.prepare.call_count, 2)

    def test_invalid_catalogues_are_not_cached(self):
        self.runtime.validate(CATALOGUE, 'Value', self.envelope())
        invalid = 'SDL2|Value{'
        for _ in range(2):
            with self.assertRaises(ValueError):
                self.runtime.validate(invalid, 'Value', self.envelope(invalid))
        self.runtime.validate(CATALOGUE, 'Value', self.envelope())
        self.assertEqual(self.prepare.call_count, 3)

    def test_source_checkout_codec_initializes_the_validation_cache(self):
        codec = SDL2(SDL_ROOT)
        for _ in range(2):
            self.assertEqual(codec.validate(CATALOGUE, 'Value', self.envelope()).fields, {'count': 1})
        self.prepare.assert_called_once_with(CATALOGUE)
