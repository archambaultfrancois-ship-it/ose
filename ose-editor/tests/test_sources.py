"""Lossless source editing and language-shape regressions."""
from decimal import Decimal
from pathlib import Path
import unittest

from ose_editor.json_source import JsonSource, loads, web
from ose_editor.sdl_source import SdlSource
from ose_editor.formats import YamlSource, XsdSource


ROOT = Path(__file__).resolve().parents[2]


class JsonTests(unittest.TestCase):
    def test_exact_numbers_and_untouched_regions(self):
        text = '{\r\n "maximum" : 18446744073709551615, "q": 0.1000000000000000001, "name":"old"\r\n}\r\n'
        parsed = JsonSource(text)
        self.assertEqual(parsed.root.value['maximum'], 2 ** 64 - 1)
        self.assertEqual(parsed.root.value['q'], Decimal('0.1000000000000000001'))
        self.assertEqual(parsed.set(['name'], '"new"'), text.replace('"old"', '"new"'))
        self.assertEqual(web(parsed.root.value)['maximum'], {'$number': str(2 ** 64 - 1)})

    def test_nested_add_delete_rename(self):
        text = '{\n  "object": {"a": 1},\n  "list": [1, 2, 3]\n}\n'
        text = JsonSource(text).set(['object', 'b'], '{"nested":true}')
        text = JsonSource(text).rename(['object', 'b'], 'new')
        text = JsonSource(text).delete(['list', '1'])
        text = JsonSource(text).set(['list', '-'], '18446744073709551615')
        self.assertEqual(loads(text)['list'], [1, 3, 2 ** 64 - 1])
        self.assertEqual(loads(text)['object']['new'], {'nested': True})

    def test_delete_first_last_only_and_append_empty(self):
        for text in ('{"x":1,"y":2}', '[1,2]', '{\n"x":1\n}', '[1]'):
            parsed = JsonSource(text)
            key = next(iter(parsed.root.children))
            result = parsed.delete([key])
            self.assertEqual(len(loads(result)), len(parsed.root.children) - 1)
        self.assertEqual(loads(JsonSource('{}').set(['x'], '1')), {'x': 1})

    def test_reject_duplicate_nonfinite_and_invalid_literals(self):
        for text in ('{"a":1,"a":2}', '{"x":NaN}', '[Infinity]', '{'):
            with self.subTest(text=text), self.assertRaises(ValueError):
                JsonSource(text)


class SdlTests(unittest.TestCase):
    text = '''/** Acquisition schemas. */
package acquisition;
// Keep this ordinary comment.
const AXES = (2 + 1) * 2 / 2;
alias Sample = fl32;
enum State { READY = 0; FAILED = -1; }
structure Capture {
    Sample[AXES] samples; /** Stored values. */
    7: optional State state;
    packed int16 values wire(bits=12,align=1,pad_after=0);
    struct { uint64 sequence; struct { bool ok; } status; } metadata;
}
'''

    def test_full_syntax_projection_keeps_expressions_aliases_and_implicit_ids(self):
        source = SdlSource(self.text)
        self.assertEqual(source.nodes[1]['properties']['expression']['value'], '(2 + 1) * 2 / 2')
        fields = source.nodes[-1]['children']
        self.assertEqual([f['effective_id'] for f in fields], [1, 7, 8, 9])
        self.assertEqual(fields[0]['properties']['type']['value'], 'Sample')
        self.assertEqual(fields[0]['properties']['dimensions']['value'], '[AXES]')
        self.assertEqual(fields[-1]['children'][-1]['children'][0]['name'], 'ok')
        self.assertEqual(fields[0]['properties']['doc']['value'], 'Stored values.')

    def test_edits_preserve_unrelated_source_and_explicit_defaults(self):
        source = SdlSource(self.text)
        result = source.edit('/4/2', 'wire', 'bits=10,align=1,pad_after=0')
        self.assertEqual(result, self.text.replace('bits=12', 'bits=10'))
        result = SdlSource(result).edit('/4/0', 'id', '3')
        self.assertIn('3: Sample[AXES] samples;', result)
        self.assertIn('// Keep this ordinary comment.', result)

    def test_documentation_edit_does_not_remove_declaration(self):
        for text in ('/** Before */ message A { int32 value; }', 'message A { int32 value; /** After */ }'):
            source = SdlSource(text)
            path = '/0' if text.startswith('/**') else '/0/0'
            changed = source.edit(path, 'doc', 'New docs')
            self.assertIn('message A', changed)
            self.assertIn('int32 value;', changed)
            self.assertIn('/** New docs */', changed)
            SdlSource(changed)

    def test_add_and_delete_nested_structure(self):
        text = SdlSource('message Empty {}\n').add('/0', 'anonymous', 'nested')
        source = SdlSource(text)
        self.assertTrue(source.index['/0/0']['anonymous'])
        self.assertEqual(source.index['/0/0/0']['name'], 'value')
        self.assertEqual(SdlSource(source.delete('/0/0')).nodes[0]['children'], [])

    def test_real_schema_sources_parse_without_normalization(self):
        paths = [*ROOT.joinpath('vibration-watch').rglob('*.sdl'), *ROOT.joinpath('sdl/examples/multiformat/schemas/sdl').glob('*.sdl')]
        for path in paths:
            with self.subTest(path=path):
                text = path.read_text()
                self.assertEqual(SdlSource(text).text, text)

    def test_invalid_drafts_and_unclosed_comments(self):
        for text in ('message A {', 'message A { int32 ; }', '/* missing close'):
            with self.subTest(text=text), self.assertRaises(ValueError):
                SdlSource(text)


class FormatTests(unittest.TestCase):
    def test_yaml_scalar_edit_retains_comments_and_exact_values(self):
        text = '# keep\nformat: sdl-schema\nmaximum: 18446744073709551615\nlabel: ON\n'
        source = YamlSource(text)
        self.assertEqual(source.root.value['maximum'], 2 ** 64 - 1)
        self.assertEqual(source.root.value['label'], 'ON')
        result = source.set(['maximum'], '18446744073709551614')
        self.assertEqual(result, text.replace('18446744073709551615', '18446744073709551614'))

    def test_yaml_duplicate_recursive_and_merge_rejected(self):
        for text in ('a: 1\na: 2\n', 'a: &x [*x]', 'a: {<<: {x: 1}}'):
            with self.subTest(text=text), self.assertRaises(ValueError):
                YamlSource(text)

    def test_xsd_metadata_and_primitive_structure_update_together(self):
        path = ROOT / 'sdl/examples/multiformat/schemas/mixed/position.sdl.xsd'
        text = path.read_text()
        source = XsdSource(text)
        target = next(path for path, binding in source.bindings.items() if binding[0] == 'metadata' and binding[1]['name'] == 'sdl:field' and path[-1] == 'type')
        changed = source.set(target, '"fl32"')
        self.assertIn('type="xs:float"', changed)
        self.assertIn('"type": "fl32"', changed)
        self.assertIn('<!-- Maintained SDL annotated XSD source for multiformat.Position. -->', changed)
        self.assertIn('type="xs:double"', changed)  # Other field remains untouched.

    def test_xsd_dtd_rejected(self):
        with self.assertRaises(ValueError):
            XsdSource('<!DOCTYPE a><a/>')

    def test_invalid_descriptor_syntax_uses_recoverable_errors(self):
        for adapter, source in ((YamlSource, 'messages: ['), (XsdSource, '<xs:schema>')):
            with self.subTest(adapter=adapter), self.assertRaises(ValueError):
                adapter(source)

    def test_yaml_json_scalar_rules_match_sdl(self):
        source = YamlSource('date: 2026-11-07\nlabel: ON\nleading: 0012\nempty: ~\nvalue: 1.5e2\n')
        self.assertEqual(source.root.value, {'date': '2026-11-07', 'label': 'ON', 'leading': '0012', 'empty': '~', 'value': Decimal('1.5e2')})


if __name__ == '__main__':
    unittest.main()
