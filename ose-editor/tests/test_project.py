"""Compound commands, disk isolation, conflicts, recovery and representative load."""
from pathlib import Path
import shutil
import tempfile
import time
import unittest

from ose_editor.json_source import loads
from ose_editor.project import Project, Conflict, atomic_write


ROOT = Path(__file__).resolve().parents[2]


class ProjectTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="editor author's project ")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name) / 'application'
        shutil.copytree(ROOT / 'vibration-watch', self.root, ignore=shutil.ignore_patterns('build', '.ose-ws', '.ose-editor', '__pycache__'))
        self.project = Project(self.root)
        self.addCleanup(self.project.close)

    def command(self, command):
        before = self.project.revision
        prepared = self.project.prepare(command, before)
        self.assertEqual(self.project.revision, before)
        self.project.apply(prepared['id'], before)
        return prepared

    def model(self):
        return loads(self.project.document(self.project.model).text)

    def test_open_and_noop_save_preserve_every_source_byte(self):
        before = {key: doc.path.read_bytes() for key, doc in self.project.documents.items()}
        self.project.save(self.project.revision)
        self.assertEqual(before, {key: doc.path.read_bytes() for key, doc in self.project.documents.items()})

    def test_component_rename_updates_all_deployments_and_undo_redo(self):
        disk = (self.root / 'model.json').read_bytes()
        prepared = self.command({'action': 'rename', 'kind': 'component', 'old': 'sensor_1', 'name': 'renamed_sensor'})
        self.assertEqual(len(prepared['changes']), 3)
        self.assertIn('renamed_sensor', self.model()['components'])
        self.assertEqual(self.model()['subscriptions'][0]['from'], 'renamed_sensor.signal')
        self.assertEqual((self.root / 'model.json').read_bytes(), disk)
        for file in self.project.deployments:
            self.assertIn('renamed_sensor', loads(self.project.document(file).text)['components'])
        self.project.undo(self.project.revision)
        self.assertIn('sensor_1', self.model()['components'])
        self.project.undo(self.project.revision, redo=True)
        self.assertIn('renamed_sensor', self.model()['components'])

    def test_parameter_rename_updates_bindings_and_client_rights(self):
        self.command({'action': 'rename', 'kind': 'parameter', 'old': 'machines/1/sensor', 'name': 'machines/one/sensor'})
        self.assertEqual(self.model()['components']['sensor_1']['parameter_bindings']['signal']['path'], 'machines/one/sensor')
        text = self.project.document('deployment.local.json').text
        self.assertNotIn('machines/1/sensor', text)

    def test_port_and_subscription_renames(self):
        self.command({'action': 'rename', 'kind': 'port', 'direction': 'outputs', 'old': 'sensor_1.signal', 'name': 'samples'})
        self.assertEqual(self.model()['subscriptions'][0]['from'], 'sensor_1.samples')
        self.command({'action': 'rename', 'kind': 'subscription', 'old': 'signal_1', 'name': 'samples_1'})
        self.assertEqual(self.model()['subscriptions'][0]['id'], 'samples_1')

    def test_prepare_failure_restores_all_documents(self):
        before = self.project.snapshot()
        with self.assertRaises(ValueError):
            self.project.prepare({'action': 'rename', 'kind': 'component', 'old': 'sensor_1', 'name': 'sensor_2'}, self.project.revision)
        self.assertEqual(self.project.snapshot(), before)

    def test_add_duplicate_remove_component(self):
        self.command({'action': 'add_component', 'name': 'sensor_3', 'copy': 'sensor_1'})
        self.assertEqual(self.model()['components']['sensor_3'], self.model()['components']['sensor_1'])
        self.command({'action': 'remove_component', 'name': 'sensor_1'})
        self.assertNotIn('sensor_1', self.model()['components'])
        self.assertFalse(any(sub.get('from', '').startswith('sensor_1.') for sub in self.model()['subscriptions']))

    def test_stale_revisions_and_concurrent_writer(self):
        old = self.project.revision
        self.command({'action': 'set', 'file': 'model.json', 'path': ['components', 'sensor_1', 'trigger', 'period_ms'], 'literal': '200'})
        with self.assertRaises(Conflict):
            self.project.prepare({'action': 'workspace', 'positions': {}}, old)
        with self.assertRaises(Conflict):
            Project(self.root)

    def test_external_file_change_keeps_disk_and_draft(self):
        self.command({'action': 'set', 'file': 'model.json', 'path': ['components', 'sensor_1', 'trigger', 'period_ms'], 'literal': '200'})
        changed = (self.root / 'model.json').read_text().replace('"period_ms": 100', '"period_ms": 300')
        (self.root / 'model.json').write_text(changed)
        with self.assertRaises(Conflict) as raised:
            self.project.save(self.project.revision)
        self.assertEqual(raised.exception.conflicts[0]['disk'], changed)
        self.assertEqual(self.model()['components']['sensor_1']['trigger']['period_ms'], 200)
        self.assertEqual((self.root / 'model.json').read_text(), changed)

    def test_syntax_invalid_source_retains_stale_projection_and_can_save_draft(self):
        self.project.projection()
        self.command({'action': 'source', 'file': 'model.json', 'text': '{ broken'})
        state = self.project.projection()
        self.assertTrue(state['documents']['model.json']['stale'])
        self.assertIn('sensor_1', state['documents']['model.json']['value']['components'])
        with self.assertRaises(ValueError):
            self.project.save(self.project.revision)
        self.project.save(self.project.revision, allow_invalid=True)
        self.assertEqual((self.root / 'model.json').read_text(), '{ broken')

    def test_partial_save_recovery_restores_compound_rename(self):
        original = {key: doc.baseline for key, doc in self.project.documents.items()}
        self.command({'action': 'rename', 'kind': 'component', 'old': 'sensor_1', 'name': 'renamed'})
        written = []
        def fail_second(path, data, mode):
            if written:
                raise OSError('injected disk failure')
            written.append(path)
            atomic_write(path, data, mode)
        with self.assertRaises(OSError):
            self.project.save(self.project.revision, replace=fail_second)
        self.assertTrue(self.project.recovery)
        self.project.close()
        self.project = Project(self.root)
        self.addCleanup(self.project.close)
        self.project.recover('restore')
        for key, baseline in original.items():
            self.assertEqual(self.project.documents[key].path.read_bytes(), baseline)

    def test_recovery_never_overwrites_subsequent_external_changes(self):
        self.command({'action': 'set', 'file': 'model.json', 'path': ['version'], 'literal': '1'})
        self.command({'action': 'set', 'file': 'model.json', 'path': ['components', 'sensor_1', 'trigger', 'period_ms'], 'literal': '200'})
        with self.assertRaises(OSError):
            self.project.save(self.project.revision, replace=lambda *args: (_ for _ in ()).throw(OSError('disk')))
        (self.root / 'model.json').write_text('external data')
        with self.assertRaises(Conflict):
            self.project.recover('complete')
        self.assertEqual((self.root / 'model.json').read_text(), 'external data')

    def test_outside_paths_and_symlinks_are_rejected(self):
        with self.assertRaises(ValueError):
            self.project.resolve('../outside.json')
        outside = self.root.parent / 'external.sdl'
        outside.write_text('message A {}')
        link = self.root / 'schemas/link.sdl'
        link.symlink_to(outside)
        with self.assertRaises(ValueError):
            self.project.resolve(link)

    def test_schema_override_scopes_remain_separate(self):
        self.assertIn('components/observer/observer_alarm.sdl', self.project.schema_inputs)
        self.assertEqual(len(self.project.schema_inputs['components/observer/observer_alarm.sdl']), 1)
        self.assertNotIn('components/observer/observer_alarm.sdl', self.project.schema_inputs['schemas'])

    def test_sdl_identity_change_is_visible_in_preview(self):
        key = 'schemas/analysis.sdl'
        prepared = self.project.prepare({'action': 'sdl_edit', 'file': key, 'path': '/0/0', 'property': 'id', 'value': '20'}, self.project.revision)
        self.assertTrue(prepared['changes'][0]['identity_changes'])

    def test_cancelled_creation_can_be_prepared_again(self):
        command = {'action': 'create_document', 'name': 'schemas/new.sdl', 'text': 'message New {}\n'}
        self.project.prepare(command, self.project.revision)
        prepared = self.project.prepare(command, self.project.revision)
        self.project.apply(prepared['id'], self.project.revision)
        self.assertIn('schemas/new.sdl', self.project.projection()['documents'])

    def test_undo_saved_creation_deletes_only_the_editor_created_file(self):
        self.command({'action': 'create_document', 'name': 'schemas/new.sdl', 'text': 'message New {}\n'})
        self.project.save(self.project.revision)
        self.assertTrue((self.root / 'schemas/new.sdl').exists())
        self.project.undo(self.project.revision)
        self.project.save(self.project.revision)
        self.assertFalse((self.root / 'schemas/new.sdl').exists())
        self.assertTrue((self.root / 'schemas/analysis.sdl').exists())
        self.project.undo(self.project.revision, redo=True)
        self.project.save(self.project.revision)
        self.assertTrue((self.root / 'schemas/new.sdl').exists())

    def test_unsaved_draft_is_recovered_explicitly_without_changing_sources(self):
        self.command({'action': 'set', 'file': 'model.json', 'path': ['components', 'sensor_1', 'trigger', 'period_ms'], 'literal': '250'})
        self.project.close()
        self.project = Project(self.root)
        self.addCleanup(self.project.close)
        self.assertTrue(self.project.projection()['draft_recovery'])
        self.assertEqual(self.model()['components']['sensor_1']['trigger']['period_ms'], 100)
        self.project.recover_draft(self.project.revision, True)
        self.assertEqual(self.model()['components']['sensor_1']['trigger']['period_ms'], 250)
        self.assertIn('"period_ms": 100', (self.root / 'model.json').read_text())
        self.project.save(self.project.revision)
        self.assertFalse((self.root / '.ose-editor/draft.json').exists())

    def test_textual_package_and_alias_refactorings_use_scoped_references(self):
        self.command({'action': 'create_document', 'name': 'schemas/domain.sdl', 'text': 'package domain; const COUNT = 3; alias Sample = fl32; message Record { Sample[COUNT] values; }\n'})
        self.command({'action': 'rename', 'kind': 'sdl', 'file': 'schemas/domain.sdl', 'path': '/2', 'name': 'Value'})
        self.assertIn('Value[COUNT]', self.project.document('schemas/domain.sdl').text)
        self.command({'action': 'rename', 'kind': 'sdl', 'file': 'schemas/domain.sdl', 'path': '/0', 'name': 'acquisition'})
        self.assertIn('package acquisition;', self.project.document('schemas/domain.sdl').text)

    def test_stabilizing_ids_preserves_effective_identity(self):
        self.command({'action': 'create_document', 'name': 'schemas/ids.sdl', 'text': 'message IDs { int32 first; 8: bool second; string third; }\n'})
        self.command({'action': 'sdl_stabilize_ids', 'file': 'schemas/ids.sdl'})
        self.assertIn('1: int32 first;', self.project.document('schemas/ids.sdl').text)
        self.assertIn('9: string third;', self.project.document('schemas/ids.sdl').text)

    def test_anonymous_identity_changes_are_reported(self):
        old = 'message E { struct { int32 x; } first; struct { bool ok; } second; }'
        new = old.replace('message E {', 'message E { struct { int32 added; } inserted;')
        changes = Project.identity_changes('schema.sdl', old, new)
        self.assertTrue(any(change['kind'] == 'anonymous type' and change['before'] != change['after'] for change in changes))


if __name__ == '__main__':
    unittest.main()
