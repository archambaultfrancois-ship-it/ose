"""Directional schema evolution preserves values, not source wire layouts."""
from pathlib import Path
import struct
import unittest
from unittest.mock import patch

from ose.codec.catalogue import CatalogueTypes
from ose.codec.sdl2 import SDL2
from ose.recording.schema_adapter import SchemaAdapter, SchemaAdapters


CAT = 'SDL2|Value{1:uint64:sequence;2:fl64:value;}'


class SchemaAdapterTests(unittest.TestCase):
    def setUp(self):
        self.codec = SDL2(Path(__file__).resolve().parents[3] / 'sdl')
        self.api = self.codec.api

    def payload(self, catalogue, root='Value', **fields):
        types = CatalogueTypes(self.api, catalogue)
        return self.api.encode(types.context, types.types[root](**fields))

    def convert(self, source, target, payload, root='Value'):
        envelope = SchemaAdapter(self.codec, source, target, root, 'compatible').adapt(payload)
        return self.codec.validate(target, root, envelope)

    def test_exact_path_preserves_all_bytes_and_rejects_other_catalogues(self):
        # Keep a NaN bit pattern and negative zero untouched on the exact path.
        for bits in (0x7ff8000000001234, 0x8000000000000000):
            payload = b'\x01' + struct.pack('>QQ', 2**64 - 1, bits)
            envelope = SchemaAdapter(self.codec, CAT, CAT, 'Value').adapt(payload)
            self.assertEqual(envelope, self.codec.wrap(CAT, payload))
        other = CAT.replace('value;', 'renamed;')
        with self.assertRaisesRegex(ValueError, 'schema policy exact'):
            SchemaAdapter(self.codec, other, CAT, 'Value')

    def test_field_ids_handle_renames_extra_fields_and_shifted_root_ids(self):
        source = 'SDL2|Earlier{1:bool:unused;}|Value{1:uint64:sequence;2:fl64:old_value;3:string:extra;}'
        payload = self.payload(source, sequence=2**64 - 1, old_value=-2.5, extra='ignored')
        self.assertEqual(payload[0], 2)
        value = self.convert(source, CAT, payload)
        self.assertEqual(value.fields, dict(sequence=2**64 - 1, value=-2.5))
        body = SchemaAdapter(self.codec, source, CAT, 'Value', 'compatible').adapt(payload)
        self.assertEqual(body[self.api.ENVELOPE_HEADER_SIZE], 1)

    def test_missing_optional_and_repeated_fixed_arrays_keep_cardinality(self):
        target = ('SDL2|Array{1:uint32[2]:values;}|Value{1:uint64:sequence;2:fl64:value;'
                  '3?:Array:optional_array;4*:Array:arrays;5+:Array:packed_arrays;}')
        value = self.convert(CAT, target, self.payload(CAT, sequence=9, value=1.25))
        self.assertIsNone(value.fields['optional_array'])
        self.assertEqual(value.fields['arrays'], [])
        self.assertEqual(value.fields['packed_arrays'], [])

    def test_nested_messages_enums_arrays_and_complex_values(self):
        source = ('SDL2|Inner{1:uint64:old_n;2:c64:gain;3*:Pair:pairs;4:Mode:mode;5:string:extra;}'
                  '|Pair{1:uint32[2]:values;}|Value{1:Inner:inner;}|#Mode{ON=1;}')
        target = ('SDL2|Inner{1:uint64:n;2:c64:gain;3*:Pair:pairs;4:Mode:mode;6?:bool:enabled;}'
                  '|Pair{1:uint32[2]:values;}|Value{1:Inner:inner;}|#Mode{ENABLED=1;OTHER=2;}')
        types = CatalogueTypes(self.api, source)
        inner = types.types['Inner'](old_n=2**64-1, gain=self.api.Complex64(-1.5, 2.25),
                                   pairs=[types.types['Pair'](values=[1, 2]), types.types['Pair'](values=[3, 4])],
                                   mode=types.types['Mode'](1), extra='ignored')
        payload = self.api.encode(types.context, types.types['Value'](inner=inner))
        value = self.convert(source, target, payload).fields['inner'].fields
        self.assertEqual(value['n'], 2**64-1)
        self.assertEqual((value['gain'].real, value['gain'].imag), (-1.5, 2.25))
        self.assertEqual([pair.fields['values'] for pair in value['pairs']], [[1, 2], [3, 4]])
        self.assertEqual(value['mode'].value, 1)
        self.assertEqual(value['mode'].name, 'ENABLED')
        self.assertIsNone(value['enabled'])

    def test_wire_representations_are_decoded_and_reencoded(self):
        source = 'SDL2|Value{1:fl64:value@b12,q0.25;}'
        target = 'SDL2|Value{1:fl64:value;}'
        payload = self.payload(source, value=2.5)
        self.assertEqual(self.convert(source, target, payload).fields['value'], 2.5)
        self.assertEqual(self.convert(target, source, self.payload(target, value=2.5)).fields['value'], 2.5)

    def test_unrelated_incompatible_declarations_do_not_restrict_the_root(self):
        source = 'SDL2|Aux{1:uint32:value;}|Value{1:uint64:sequence;2:fl64:value;}'
        target = 'SDL2|Aux{1:string:value;}|Value{1:uint64:sequence;2:fl64:value;3?:Aux:new_aux;}'
        value = self.convert(source, target, self.payload(source, sequence=3, value=1.25))
        self.assertEqual(value.fields, dict(sequence=3, value=1.25, new_aux=None))

    def test_incompatibilities_report_codes_and_field_paths(self):
        cases = [
            (CAT, CAT[:-1] + '3:bool:required_flag;}', 'REQUIRED_FIELD_MISSING.*Value.required_flag'),
            (CAT, CAT.replace('fl64', 'fl32'), 'FIELD_TYPE_MISMATCH.*Value.value'),
            ('SDL2|Value{1:uint32[2]:array;}', 'SDL2|Value{1:uint32[3]:array;}', 'FIELD_DIMENSIONS_MISMATCH'),
            ('SDL2|Value{1*:uint32:items;}', 'SDL2|Value{1?:uint32:items;}', 'FIELD_CARDINALITY_MISMATCH'),
            ('SDL2|Value{1:Mode:mode;}|#Mode{A=1;B=2;}',
             'SDL2|Value{1:Mode:mode;}|#Mode{A=1;}', 'ENUM_VALUE_UNKNOWN'),
            (CAT, 'SDL2|Other{1:bool:value;}', 'ROOT_UNKNOWN'),
        ]
        for source, target, diagnostic in cases:
            with self.subTest(diagnostic=diagnostic), self.assertRaisesRegex(ValueError, diagnostic):
                SchemaAdapter(self.codec, source, target, 'Value', 'compatible')

    def test_source_payload_is_validated_even_for_discarded_fields(self):
        source = 'SDL2|Value{1:uint64:sequence;2:fl64:value;3:bool:ignored;}'
        adapter = SchemaAdapter(self.codec, source, CAT, 'Value', 'compatible')
        with self.assertRaises(ValueError):
            adapter.adapt(b'\x01' + struct.pack('>Qd', 1, 2.) + b'\x02')
        with self.assertRaises(ValueError):
            adapter.adapt(b'\x01')
        other = 'SDL2|Earlier{1:bool:unused;}|Value{1:uint64:sequence;2:fl64:value;}'
        with self.assertRaisesRegex(ValueError, 'root'):
            SchemaAdapter(self.codec, other, CAT, 'Value', 'compatible').adapt(b'\x01\x00')

    def test_cache_is_bounded_reuses_contexts_and_compares_full_catalogues(self):
        adapters = SchemaAdapters(self.codec, 'compatible', capacity=2)
        source = CAT.replace('value;', 'old_value;')
        with patch.object(self.codec, 'compare', wraps=self.codec.compare) as compare:
            first = adapters.get(source, CAT, 'Value')
            self.assertIs(first, adapters.get(source, CAT, 'Value'))
            compare.assert_called_once()
        adapters.get(CAT, CAT, 'Value')
        adapters.get(CAT.replace('value;', 'another_value;'), CAT, 'Value')
        self.assertEqual(len(adapters.cache), 2)
        self.assertNotIn((source, CAT, 'Value'), adapters.cache)
        with patch.object(self.codec, 'identity', return_value=123):
            with self.assertRaisesRegex(ValueError, 'schema policy exact'):
                SchemaAdapters(self.codec).get(source, CAT, 'Value')

    def test_invalid_policy_and_capacity(self):
        for policy in (None, '', 'anything', True):
            with self.subTest(policy=policy), self.assertRaises(ValueError):
                SchemaAdapters(self.codec, policy)
        for capacity in (0, -1, True, 1.5):
            with self.subTest(capacity=capacity), self.assertRaises(ValueError):
                SchemaAdapters(self.codec, capacity=capacity)
