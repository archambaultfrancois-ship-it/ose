"""Compound commands, disk isolation, conflicts, recovery and representative load."""
from pathlib import Path
from decimal import Decimal
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

    def test_duplicate_subscription_copies_qos_and_deployments_and_is_undoable(self):
        self.command({'action': 'set', 'file': self.project.model,
                      'path': ['components', 'analyzer_2', 'inputs', 'copy'],
                      'literal': '{"root":"SignalBlock"}'})
        before = self.project.snapshot()
        disk = {key: doc.path.read_bytes() for key, doc in self.project.documents.items()}
        prepared = self.command({'action': 'duplicate_subscription', 'copy': 'signal_1',
                                 'name': 'signal_copy', 'to': 'analyzer_2.copy'})
        subs = self.model()['subscriptions']
        self.assertEqual(subs[-1], {**subs[0], 'id': 'signal_copy', 'to': 'analyzer_2.copy'})
        cluster = loads(self.project.document('deployment.cluster.json').text)
        self.assertEqual(cluster['subscriptions']['signal_copy'], cluster['subscriptions']['signal_1'])
        self.assertNotIn('subscriptions', loads(self.project.document('deployment.local.json').text))
        self.assertEqual({change['file'] for change in prepared['changes']}, {self.project.model, 'deployment.cluster.json'})
        self.assertEqual(disk, {key: doc.path.read_bytes() for key, doc in self.project.documents.items()})
        self.project.undo(self.project.revision)
        self.assertEqual(self.project.snapshot(), before)
        self.project.undo(self.project.revision, redo=True)
        self.assertEqual(self.model()['subscriptions'][-1]['id'], 'signal_copy')

    def test_duplicate_subscription_rejects_invalid_destinations_without_partial_changes(self):
        cases = [
            ({'name': ''}, 'nonempty'),
            ({'name': 'signal_2'}, 'already exists'),
            ({'copy': 'missing'}, 'Unknown subscription'),
            ({'to': 'bad'}, 'component.port'),
            ({'to': 'analyzer_2.missing'}, 'Unknown destination'),
            ({'to': 'alarm.machine_1'}, 'same logical name'),
        ]
        self.command({'action': 'set', 'file': self.project.model,
                      'path': ['components', 'alarm', 'inputs', 'free'],
                      'literal': '{"root":"Analysis"}'})
        self.command({'action': 'set', 'file': self.project.model,
                      'path': ['components', 'sensor_1', 'inputs', 'loop'],
                      'literal': '{"root":"SignalBlock","activates":{"message":["signal"]}}'})
        cases.append(({'to': 'alarm.free'}, 'same logical name'))
        for overrides, message in cases:
            with self.subTest(overrides=overrides):
                before = self.project.snapshot()
                command = {'action': 'duplicate_subscription', 'copy': 'signal_1',
                           'name': 'copy', 'to': 'analyzer_2.input', **overrides}
                with self.assertRaisesRegex(ValueError, message):
                    self.project.prepare(command, self.project.revision)
                self.assertEqual(self.project.snapshot(), before)

    def test_duplicate_external_subscription_keeps_topic_and_qos(self):
        self.command({'action': 'set', 'file': self.project.model,
                      'path': ['external_topics'], 'literal': '{"outside":{"root":"SignalBlock"}}'})
        self.command({'action': 'set', 'file': self.project.model,
                      'path': ['subscriptions', '0'],
                      'literal': '{"id":"signal_1","from_topic":"outside","to":"analyzer_1.input","qos":{"capacity":8}}'})
        self.command({'action': 'set', 'file': self.project.model,
                      'path': ['components', 'analyzer_2', 'inputs', 'copy'],
                      'literal': '{"root":"SignalBlock"}'})
        self.command({'action': 'duplicate_subscription', 'copy': 'signal_1',
                      'name': 'outside_copy', 'to': 'analyzer_2.copy'})
        sub = self.model()['subscriptions'][-1]
        self.assertEqual(sub, {'id': 'outside_copy', 'from_topic': 'outside',
                               'to': 'analyzer_2.copy', 'qos': {'capacity': 8}})

    def test_connect_merges_producers_and_inherits_shared_reception_without_saving(self):
        from ose.delivery.policies import SubscriptionPolicy
        before = self.project.snapshot()
        disk = (self.root / 'model.json').read_bytes()
        self.command({'action': 'connect', 'name': 'merged', 'from': 'sensor_2.signal', 'to': 'analyzer_1.input'})
        links = [sub for sub in self.model()['subscriptions'] if sub['to'] == 'analyzer_1.input']
        self.assertEqual(len(links), 2)
        self.assertEqual(SubscriptionPolicy.resolve(links[0].get('qos')).reception(),
                         SubscriptionPolicy.resolve(links[1]['qos']).reception())
        self.assertEqual((self.root / 'model.json').read_bytes(), disk)
        self.project.undo(self.project.revision)
        self.assertEqual(self.project.snapshot()['documents'], before['documents'])

    def test_duplicate_to_connected_input_and_shared_edit_preserve_delivery_settings(self):
        from ose.delivery.policies import SubscriptionPolicy
        source_index = next(i for i, sub in enumerate(self.model()['subscriptions']) if sub['id'] == 'signal_2')
        self.command({'action': 'set', 'file': self.project.model, 'path': ['subscriptions', str(source_index), 'qos'],
                      'literal': '{"buffer":"latest","ack":"received","retry":{"max_retries":1}}'})
        self.command({'action': 'duplicate_subscription', 'copy': 'signal_2', 'name': 'merged', 'to': 'analyzer_1.input'})
        model = self.model()
        copied = model['subscriptions'][-1]
        target = next(sub for sub in model['subscriptions'] if sub['id'] == 'signal_1')
        self.assertEqual(SubscriptionPolicy.resolve(copied['qos']).reception(), SubscriptionPolicy.resolve(target['qos']).reception())
        self.assertEqual(copied['qos']['retry'], {'max_retries': 1})
        before = self.project.snapshot()
        self.command({'action': 'set_reception', 'to': 'analyzer_1.input', 'reception': {'buffer': 'latest'}})
        incoming = [sub for sub in self.model()['subscriptions'] if sub['to'] == 'analyzer_1.input']
        for sub in incoming:
            self.assertEqual(sub['qos']['buffer'], 'latest')
            self.assertEqual(sub['qos']['capacity'], 1)
            self.assertEqual(sub['qos']['overflow'], 'drop_oldest')
        self.assertEqual(incoming[-1]['qos']['retry'], {'max_retries': 1})
        self.project.undo(self.project.revision)
        self.assertEqual(self.project.snapshot()['documents'], before['documents'])

    def test_external_fanin_and_invalid_shared_udp_policy_are_atomic(self):
        self.command({'action': 'set', 'file': self.project.model, 'path': ['external_topics'],
                      'literal': '{"outside":{"root":"SignalBlock"}}'})
        self.command({'action': 'connect', 'name': 'outside', 'from': 'outside', 'external': True, 'to': 'analyzer_1.input'})
        self.command({'action': 'set', 'file': 'deployment.local.json', 'path': ['subscriptions'],
                      'literal': '{"outside":{"transport":"udp"}}'})
        before = self.project.snapshot()
        with self.assertRaisesRegex(ValueError, 'UDP cannot wait'):
            self.project.prepare({'action': 'set_reception', 'to': 'analyzer_1.input',
                                  'reception': {'overflow': 'wait'}}, self.project.revision)
        self.assertEqual(self.project.snapshot(), before)

    def test_nested_visual_groups_survive_undo_rename_save_and_reopen(self):
        original = (self.root / 'model.json').read_bytes()
        groups = {
            'branch_1': {'members': ['sensor_1', 'analyzer_1'], 'collapsed': True},
            'branch_2': {'members': ['sensor_2', 'analyzer_2'], 'collapsed': True},
            'pipeline': {'members': [], 'groups': ['branch_1', 'branch_2'], 'collapsed': True},
        }
        self.command({'action': 'workspace', 'groups': groups})
        self.assertEqual(self.project.workspace['groups'], groups)
        self.assertEqual((self.root / 'model.json').read_bytes(), original)
        self.project.undo(self.project.revision)
        self.assertEqual(self.project.workspace['groups'], {})
        self.project.undo(self.project.revision, redo=True)
        self.assertEqual(self.project.workspace['groups'], groups)
        self.command({'action': 'rename', 'kind': 'component', 'old': 'sensor_1', 'name': 'renamed'})
        self.assertEqual(self.project.workspace['groups']['branch_1']['members'], ['renamed', 'analyzer_1'])
        self.assertEqual(self.project.workspace['groups']['pipeline']['groups'], ['branch_1', 'branch_2'])
        self.project.undo(self.project.revision)
        self.project.save(self.project.revision)
        self.project.close()
        self.project = Project(self.root)
        self.addCleanup(self.project.close)
        self.assertEqual(self.project.workspace['groups'], groups)
        self.assertEqual((self.root / 'model.json').read_bytes(), original)

    def test_port_and_subscription_renames(self):
        self.command({'action': 'rename', 'kind': 'port', 'direction': 'outputs', 'old': 'sensor_1.signal', 'name': 'samples'})
        self.assertEqual(self.model()['subscriptions'][0]['from'], 'sensor_1.samples')
        self.assertEqual(self.model()['components']['sensor_1']['inputs']['clock']['activates']['timeout'], ['samples'])
        self.command({'action': 'rename', 'kind': 'subscription', 'old': 'signal_1', 'name': 'samples_1'})
        self.assertEqual(self.model()['subscriptions'][0]['id'], 'samples_1')

    def test_new_v2_component_uses_a_timer_instead_of_a_trigger(self):
        self.command({'action': 'add_component', 'name': 'new_component'})
        component = self.model()['components']['new_component']
        self.assertNotIn('trigger', component)
        self.assertEqual(component['inputs']['clock'], {'kind': 'timer', 'timer': {'mode': 'periodic', 'delay': Decimal('0.1')}})

    def test_new_project_defaults_to_model_v2_and_deployment_v1(self):
        root = self.root / 'new-project'
        root.mkdir()
        project = Project(root, create=True)
        self.addCleanup(project.close)
        self.assertEqual(loads(project.document(project.model).text)['version'], 2)
        self.assertEqual(loads(project.document(project.deployments[0]).text)['version'], 1)

    def test_legacy_model_stays_editable_as_source_without_structured_fallback(self):
        self.command({'action': 'set', 'file': 'model.json', 'path': ['version'], 'literal': '1'})
        before = self.project.snapshot()
        state = self.project.projection()
        self.assertFalse(state['documents']['model.json']['stale'])
        self.assertTrue(any('Application model v2 is required' in item['message'] for item in state['diagnostics']))
        for command in ({'action': 'add_component', 'name': 'legacy'},
                        {'action': 'rename', 'kind': 'component', 'old': 'sensor_1', 'name': 'renamed'},
                        {'action': 'connect', 'subscription': {'id': 'legacy', 'from': 'sensor_1.signal', 'to': 'analyzer_1.input'}}):
            with self.subTest(action=command['action']), self.assertRaisesRegex(ValueError, 'Application model v2'):
                self.project.prepare(command, self.project.revision)
            self.assertEqual(self.project.snapshot(), before)
        with self.assertRaisesRegex(ValueError, 'Draft has errors'):
            self.project.save(self.project.revision)
        self.project.save(self.project.revision, allow_invalid=True)
        self.assertEqual(loads((self.root / 'model.json').read_text())['version'], 1)
        self.command({'action': 'set', 'file': 'model.json', 'path': ['version'], 'literal': '2'})
        self.command({'action': 'add_component', 'name': 'migrated'})
        self.assertIn('clock', self.model()['components']['migrated']['inputs'])

    def test_input_execution_configuration_is_atomic_reversible_and_preserves_other_sources(self):
        before = self.project.document(self.project.model).text
        disk = {key: doc.path.read_bytes() for key, doc in self.project.documents.items()}
        desc = {'kind': 'timer', 'timer': {'mode': 'one_shot', 'delay': .25, 'start': 'manual'},
                'activates': {'timeout': ['signal']}}
        prepared = self.command({'action': 'configure_input', 'component': 'sensor_1', 'port': 'clock', 'description': desc})
        self.assertEqual([change['file'] for change in prepared['changes']], ['model.json'])
        self.assertTrue(prepared['handlers_to_review'])
        timer = self.model()['components']['sensor_1']['inputs']['clock']
        self.assertEqual(timer['timer']['mode'], 'one_shot')
        self.assertEqual(timer['timer']['delay'], Decimal('0.25'))
        self.assertEqual(timer['activates'], {'timeout': ['signal']})
        self.assertEqual(disk, {key: doc.path.read_bytes() for key, doc in self.project.documents.items()})
        self.project.undo(self.project.revision)
        self.assertEqual(self.project.document(self.project.model).text, before)
        self.project.undo(self.project.revision, redo=True)
        self.assertEqual(self.model()['components']['sensor_1']['inputs']['clock']['timer']['start'], 'manual')

    def test_input_configuration_rejects_connected_timers_invalid_events_and_reserved_names(self):
        cases = [('analyzer_1', 'input', {'kind': 'timer', 'timer': {'mode': 'periodic', 'delay': .1}}, 'Disconnect'),
                 ('sensor_1', 'clock', {'kind': 'timer', 'timer': {'mode': 'periodic', 'delay': 0}}, 'positive'),
                 ('sensor_1', 'extra', {'root': 'SignalBlock', 'mode': 'pull', 'timeout': .1}, 'passive Pull'),
                 ('sensor_1', 'extra', {'root': 'SignalBlock', 'activates': {'message': ['missing']}}, 'invalid'),
                 ('sensor_1', 'tick', {'root': 'SignalBlock'}, 'reserved callback')]
        for component, port, desc, message in cases:
            with self.subTest(component=component, port=port):
                before = self.project.snapshot()
                with self.assertRaisesRegex(ValueError, message):
                    self.project.prepare({'action': 'configure_input', 'component': component, 'port': port,
                                          'description': desc}, self.project.revision)
                self.assertEqual(before, self.project.snapshot())

    def test_input_configuration_uses_event_dependencies_and_never_adds_implicit_permissions(self):
        self.command({'action': 'configure_input', 'component': 'sensor_1', 'port': 'feedback',
                      'description': {'root': 'Analysis', 'mode': 'pull'}})
        self.command({'action': 'connect', 'name': 'feedback', 'from': 'analyzer_1.result', 'to': 'sensor_1.feedback'})
        self.command({'action': 'configure_input', 'component': 'sensor_1', 'port': 'feedback',
                      'description': {'root': 'Analysis', 'mode': 'push', 'activates': {'message': ['signal']}}})
        self.assertEqual(self.model()['components']['sensor_1']['inputs']['feedback']['activates'],
                         {'message': ['signal']})
        self.command({'action': 'configure_input', 'component': 'sensor_1', 'port': 'feedback',
                      'description': {'root': 'Analysis', 'mode': 'push', 'timeout': .2, 'activates': {'timeout': ['signal']}}})
        desc = self.model()['components']['sensor_1']['inputs']['feedback']
        self.assertNotIn('message', desc['activates'])
        self.command({'action': 'configure_input', 'component': 'sensor_1', 'port': 'feedback', 'description': {'root': 'Analysis'}})
        self.assertNotIn('activates', self.model()['components']['sensor_1']['inputs']['feedback'])

    def test_v2_connections_accept_feedback_cycles_and_reject_timers(self):
        for port, desc in (('feedback', '{"root":"SignalBlock","mode":"pull"}'),
                           ('activating', '{"root":"SignalBlock","activates":{"message":["signal"]}}')):
            self.command({'action': 'set', 'file': self.project.model,
                          'path': ['components', 'sensor_1', 'inputs', port], 'literal': desc})
        self.command({'action': 'duplicate_subscription', 'copy': 'signal_1', 'name': 'feedback', 'to': 'sensor_1.feedback'})
        self.command({'action': 'duplicate_subscription', 'copy': 'signal_1',
                      'name': 'activating_return', 'to': 'sensor_1.activating'})
        for name, destination, message in (('bad_timer', 'sensor_1.clock', 'Timer inputs'),):
            before = self.project.snapshot()
            with self.assertRaisesRegex(ValueError, message):
                self.project.prepare({'action': 'duplicate_subscription', 'copy': 'signal_1',
                                      'name': name, 'to': destination}, self.project.revision)
            self.assertEqual(self.project.snapshot(), before)

    def test_prepare_failure_restores_all_documents(self):
        before = self.project.snapshot()
        with self.assertRaises(ValueError):
            self.project.prepare({'action': 'rename', 'kind': 'component', 'old': 'sensor_1', 'name': 'sensor_2'}, self.project.revision)
        self.assertEqual(self.project.snapshot(), before)

    def test_add_duplicate_remove_component(self):
        name = 'sensor_3'
        while name in self.model()['components']:
            name += '_copy'
        self.command({'action': 'add_component', 'name': name, 'copy': 'sensor_1'})
        self.assertEqual(self.model()['components'][name], self.model()['components']['sensor_1'])
        self.assertIn('    "' + name + '": {\n      "inputs": {\n        "clock": {', self.project.document(self.project.model).text)
        for key in self.project.deployments:
            self.assertIn('    "' + name + '": {\n      "container": {\n', self.project.document(key).text)
        self.command({'action': 'remove_component', 'name': 'sensor_1'})
        self.assertNotIn('sensor_1', self.model()['components'])
        self.assertFalse(any(sub.get('from', '').startswith('sensor_1.') for sub in self.model()['subscriptions']))

    def test_stale_revisions_and_concurrent_writer(self):
        old = self.project.revision
        self.command({'action': 'set', 'file': 'model.json', 'path': ['components', 'sensor_1', 'inputs', 'clock', 'timer', 'delay'], 'literal': '0.2'})
        with self.assertRaises(Conflict):
            self.project.prepare({'action': 'workspace', 'positions': {}}, old)
        with self.assertRaises(Conflict):
            Project(self.root)

    def test_external_file_change_keeps_disk_and_draft(self):
        self.command({'action': 'set', 'file': 'model.json', 'path': ['components', 'sensor_1', 'inputs', 'clock', 'timer', 'delay'], 'literal': '0.2'})
        changed = (self.root / 'model.json').read_text().replace('"delay": 0.1', '"delay": 0.3')
        (self.root / 'model.json').write_text(changed)
        with self.assertRaises(Conflict) as raised:
            self.project.save(self.project.revision)
        self.assertEqual(raised.exception.conflicts[0]['disk'], changed)
        self.assertEqual(self.model()['components']['sensor_1']['inputs']['clock']['timer']['delay'], Decimal('0.2'))
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
        self.command({'action': 'set', 'file': 'model.json', 'path': ['components', 'sensor_1', 'inputs', 'clock', 'timer', 'delay'], 'literal': '0.2'})
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
        self.command({'action': 'set', 'file': 'model.json', 'path': ['components', 'sensor_1', 'inputs', 'clock', 'timer', 'delay'], 'literal': '0.25'})
        self.project.close()
        self.project = Project(self.root)
        self.addCleanup(self.project.close)
        self.assertTrue(self.project.projection()['draft_recovery'])
        self.assertEqual(self.model()['components']['sensor_1']['inputs']['clock']['timer']['delay'], Decimal('0.1'))
        self.project.recover_draft(self.project.revision, True)
        self.assertEqual(self.model()['components']['sensor_1']['inputs']['clock']['timer']['delay'], .25)
        self.assertIn('"delay": 0.1', (self.root / 'model.json').read_text())
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
