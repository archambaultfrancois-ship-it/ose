"""HTTP access boundaries and public OSE/SDL validation integration."""
from contextlib import ExitStack
from pathlib import Path
import shutil
import tempfile
import time
import unittest

from fastapi.testclient import TestClient

from ose_editor.api import create_app
from ose_editor.project import Project


ROOT = Path(__file__).resolve().parents[2]


class ApiTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='ose-editor-api-')
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name) / 'app'
        shutil.copytree(ROOT / 'vibration-watch', self.root, ignore=shutil.ignore_patterns('build', '__pycache__', '.ose-ws', '.ose-editor'))
        self.project = Project(self.root)
        self.app = create_app(self.project, ROOT / 'sdl', token='test-editor-token-that-is-never-a-project-secret')
        contexts = ExitStack()
        self.addCleanup(contexts.close)
        self.client = contexts.enter_context(TestClient(self.app))
        self.headers = {'Authorization': 'Bearer test-editor-token-that-is-never-a-project-secret'}

    def get(self, path):
        return self.client.get('/api/v1/' + path, headers=self.headers)

    def post(self, path, data):
        return self.client.post('/api/v1/' + path, json=data, headers=self.headers)

    def command(self, command):
        revision = self.get('project').json()['revision']
        preview = self.post('commands/prepare', {'revision': revision, 'command': command})
        self.assertEqual(preview.status_code, 200, preview.text)
        result = self.post('commands/apply', {'revision': revision, 'id': preview.json()['id']})
        self.assertEqual(result.status_code, 200, result.text)
        return result.json()

    def wait(self, job):
        deadline = time.monotonic() + 45
        while time.monotonic() < deadline:
            result = self.get('validation/' + job['id']).json()
            if result['status'] not in ('queued', 'running'):
                return result
            time.sleep(.05)
        self.fail('Validation job timed out')

    def test_access_token_and_origin_checks(self):
        self.assertEqual(self.client.get('/api/v1/project').status_code, 401)
        response = self.client.get('/api/v1/project', headers={**self.headers, 'Origin': 'https://untrusted.example'})
        self.assertEqual(response.status_code, 403)
        self.assertEqual(self.get('project').status_code, 200)

    def test_frontend_is_local_and_capabilities_are_explicit(self):
        response = self.client.get('/')
        self.assertEqual(response.status_code, 200)
        self.assertIn('<html lang="en">', response.text)
        self.assertNotIn('https://', response.text)
        capabilities = self.get('capabilities').json()
        self.assertFalse(capabilities['business_code_execution'])
        self.assertIn('sdl', capabilities['formats'])

    def test_full_authoring_journey_validates_unsaved_then_saved_sources(self):
        state = self.command({'action': 'rename', 'kind': 'component', 'old': 'sensor_1', 'name': 'new_sensor'})
        self.assertIn('"sensor_1"', (self.root / 'model.json').read_text())
        job = self.post('validation', {'revision': state['revision'], 'deployment': 'deployment.local.json'}).json()
        result = self.wait(job)
        self.assertEqual(result['status'], 'passed', result)
        saved = self.post('save', {'revision': state['revision']})
        self.assertEqual(saved.status_code, 200, saved.text)
        self.assertIn('"new_sensor"', (self.root / 'model.json').read_text())
        self.assertFalse((self.root / 'build').exists(), 'Validation must not generate application build outputs')

    def test_authoritative_validation_reports_invalid_qos(self):
        state = self.command({'action': 'set', 'file': 'model.json', 'path': ['subscriptions', '0', 'qos', 'buffer'], 'literal': '"latest"'})
        job = self.post('validation', {'revision': state['revision']}).json()
        result = self.wait(job)
        self.assertEqual(result['status'], 'failed')
        self.assertIn('latest', result['diagnostics'][0]['message'])

    def test_connection_guidance_checks_unconnected_scoped_contracts(self):
        self.command({'action': 'create_document', 'name': 'overrides/strict.sdl',
                      'text': 'message Alarm { 9: uint32 required_extra; }\n'})
        self.command({'action': 'set', 'file': 'model.json',
                      'path': ['components', 'observer', 'inputs', 'strict'],
                      'literal': '{"root":"Alarm","schema":"overrides/strict.sdl","required":false}'})
        # A command-based component may use distinct versions of one root;
        # generated wrappers additionally constrain their combined type set.
        self.command({'action': 'set', 'file': 'deployment.local.json', 'path': ['components', 'observer'],
                      'literal': '{"command":["unused-handler"]}'})
        state = self.command({'action': 'set', 'file': 'model.json', 'path': ['external_topics'],
                              'literal': '{"ambient.topic":{"root":"Alarm"}}'})
        result = self.wait(self.post('validation', {'revision': state['revision'], 'deployment': 'deployment.local.json'}).json())
        self.assertEqual(result['status'], 'passed', result)
        reports = {(item['publisher_schema'], item['consumer_schema'], item['root']): item
                   for item in result['connection_contracts']}
        self.assertTrue(reports['schemas', 'components/observer/observer_alarm.sdl', 'Alarm']['compatible'])
        self.assertFalse(reports['schemas', 'overrides/strict.sdl', 'Alarm']['compatible'])
        before = self.project.snapshot()
        rejected = self.post('commands/prepare', {'revision': state['revision'], 'command': {
            'action': 'connect', 'name': 'outside', 'from': 'ambient.topic', 'to': 'observer.strict', 'external': True}})
        self.assertEqual(rejected.status_code, 422, rejected.text)
        self.assertIn('Incompatible SDL contract', rejected.json()['message'])
        self.assertEqual(self.project.snapshot(), before)
        # A later revision must not reuse an old compatibility verdict.
        state = self.command({'action': 'source', 'file': 'overrides/strict.sdl', 'text': 'message Alarm {}\n'})
        self.assertIsNone(state['validation'])
        accepted = self.post('commands/prepare', {'revision': state['revision'], 'command': {
            'action': 'connect', 'name': 'outside', 'from': 'ambient.topic', 'to': 'observer.strict', 'external': True}})
        self.assertEqual(accepted.status_code, 200, accepted.text)

    def test_syntax_and_revision_errors_have_actionable_status_codes(self):
        response = self.post('commands/prepare', {'revision': 'stale', 'command': {'action': 'workspace'}})
        self.assertEqual(response.status_code, 409)
        self.command({'action': 'source', 'file': 'schemas/analysis.sdl', 'text': 'message Broken {'})
        state = self.get('project').json()
        self.assertTrue(state['documents']['schemas/analysis.sdl']['stale'])
        response = self.post('save', {'revision': state['revision']})
        self.assertEqual(response.status_code, 422)

    def test_unknown_and_outside_documents_are_not_readable(self):
        for name in ('../private.json', '/etc/passwd', '.ose-editor/access.token'):
            self.assertEqual(self.get('document?file=' + name).status_code, 422)

    def test_validation_of_changed_schema_uses_the_draft(self):
        state = self.command({'action': 'sdl_edit', 'file': 'schemas/analysis.sdl', 'path': '/0/0', 'property': 'type', 'value': 'UnknownType'})
        result = self.wait(self.post('validation', {'revision': state['revision'], 'schema': 'schemas'}).json())
        self.assertEqual(result['status'], 'failed')
        self.assertIn('UnknownType', result['diagnostics'][0]['message'])

    def test_application_build_commands_and_handlers_are_never_executed(self):
        marker = self.root / 'must-not-exist'
        command = '["' + str(Path(__import__('sys').executable)) + '", "-c", "open(\'' + str(marker) + '\',\'w\').write(\'executed\')"]'
        state = self.command({'action': 'set', 'file': 'deployment.local.json', 'path': ['components', 'sensor_1', 'build'], 'literal': '[' + command + ']'})
        result = self.wait(self.post('validation', {'revision': state['revision'], 'deployment': 'deployment.local.json'}).json())
        self.assertEqual(result['status'], 'passed', result)
        self.assertFalse(marker.exists())

    def test_export_is_previewed_outside_inputs_and_keeps_the_original(self):
        original = (self.root / 'schemas/analysis.sdl').read_bytes()
        self.command({'action': 'export_schema', 'file': 'schemas/analysis.sdl', 'format': 'json', 'name': 'exports/analysis.sdl.json'})
        document = self.get('document?file=exports/analysis.sdl.json').json()
        self.assertIn('"Analysis"', document['text'])
        self.assertNotIn('generated_by', document['text'])
        self.assertEqual((self.root / 'schemas/analysis.sdl').read_bytes(), original)
        self.assertFalse((self.root / 'exports/analysis.sdl.json').exists())
        response = self.post('commands/prepare', {'revision': self.project.revision, 'command': {
            'action': 'export_schema', 'file': 'schemas/analysis.sdl', 'format': 'json', 'name': 'schemas/analysis.sdl.json'}})
        self.assertEqual(response.status_code, 422)

    def test_job_responses_are_detached_from_worker_owned_state(self):
        state = self.get('project').json()
        job = self.post('validation', {'revision': state['revision']}).json()
        snapshot = self.app.state.validator.snapshot(job['id'])
        self.wait(job)
        self.assertEqual(snapshot['resolved'], {})


if __name__ == '__main__':
    unittest.main()
