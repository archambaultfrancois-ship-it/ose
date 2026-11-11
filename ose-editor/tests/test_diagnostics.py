"""Reliable diagnostic coordinates and file attribution across staged validation."""
from contextlib import closing
import json
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch

from ose_editor.diagnostics import source_diagnostic
from ose_editor.project import Project
from ose_editor.sdl_source import SdlSource
from ose_editor.validation import Validator

ROOT = Path(__file__).resolve().parents[2]


class DiagnosticTests(unittest.TestCase):
    def test_json_and_sdl_locations_are_one_based_code_points(self):
        text = '{"label":"🔎", "bad": }'
        try:
            json.loads(text)
        except json.JSONDecodeError as error:
            result = source_diagnostic(error, 'model.json', text)
        self.assertEqual(result['column'], text.index('}') + 1)
        self.assertEqual(result['line'], 1)
        sdl = '// 🔎\nmessage Reading { uint32 value }'
        try:
            SdlSource(sdl)
        except ValueError as error:
            result = source_diagnostic(error, 'types.sdl', sdl)
        self.assertEqual(result['line'], 2)
        self.assertEqual(result['column'], sdl.rindex('}') - sdl.index('\n'))
        self.assertEqual(source_diagnostic(ValueError('Unknown failure'), 'model.json')['message'], 'Unknown failure')
        self.assertNotIn('line', source_diagnostic(ValueError('Unknown failure'), 'model.json'))

    def test_projection_exposes_syntax_location_and_revision(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'model.json').write_text('{\n  "version":\n}')
            with closing(Project(root)) as project:
                state = project.projection()
                diagnostic = next(item for item in state['diagnostics'] if item['origin'] == 'syntax')
                self.assertEqual((diagnostic['file'], diagnostic['line'], diagnostic['column']), ('model.json', 3, 1))
                self.assertEqual(diagnostic['revision'], state['revision'])

    def test_reception_correction_is_previewed_and_undoable(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            model = {'version': 2, 'schemas': 'types.sdl', 'components': {
                'source': {'inputs': {}, 'outputs': {'out': {'root': 'Reading'}}},
                'target': {'inputs': {'input': {'root': 'Reading'}}, 'outputs': {}}},
                'subscriptions': [{'id': 'link', 'from': 'source.out', 'to': 'target.input',
                                   'qos': {'buffer': 'latest', 'capacity': 9, 'overflow': 'reject_new', 'ack': 'received'}}]}
            original = json.dumps(model, indent=2)
            (root / 'model.json').write_text(original)
            (root / 'types.sdl').write_text('message Reading { uint32 value; }')
            with closing(Project(root)) as project:
                revision = project.revision
                preview = project.prepare({'action': 'set_reception', 'to': 'target.input', 'reception': {
                    'buffer': 'latest', 'capacity': 1, 'overflow': 'drop_oldest', 'wait_timeout_ms': 100}}, revision)
                self.assertEqual(project.document('model.json').text, original)
                self.assertIn('drop_oldest', preview['changes'][0]['diff'])
                project.apply(preview['id'], revision)
                policy = json.loads(project.document('model.json').text)['subscriptions'][0]['qos']
                self.assertEqual((policy['capacity'], policy['overflow'], policy['ack']), (1, 'drop_oldest', 'received'))
                self.assertEqual((root / 'model.json').read_text(), original)
                project.undo(project.revision)
                self.assertEqual(project.document('model.json').text, original)

    def test_validation_maps_staged_sdl_errors_back_to_the_exact_project_file(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'model.json').write_text(json.dumps({'version': 2, 'schemas': 'types.sdl', 'components': {}, 'subscriptions': []}))
            (root / 'types.sdl').write_text('message Reading { uint32 value; }')
            with closing(Project(root)) as project:
                validator = Validator(project, ROOT / 'sdl')
                self.addCleanup(validator.close)
                def fail(job, command, env):
                    raise ValueError(str(command[-2]) + ': line 1, column 19: invalid field')
                with patch.object(validator, 'execute', side_effect=fail):
                    job = validator.start(project.revision, schema='types.sdl')
                    deadline = time.monotonic() + 5
                    while job['status'] in ('queued', 'running') and time.monotonic() < deadline:
                        time.sleep(.01)
                        job = validator.snapshot(job['id'])
                self.assertEqual(job['status'], 'failed', job)
                diagnostic = job['diagnostics'][0]
                self.assertEqual(diagnostic['file'], 'types.sdl')
                self.assertEqual((diagnostic['line'], diagnostic['column']), (1, 19))
                self.assertNotIn('ose-editor-validate-', diagnostic['message'])
                self.assertEqual(diagnostic['revision'], project.revision)
