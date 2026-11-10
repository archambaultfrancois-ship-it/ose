"""Compound graphical edits keep ports, permissions and deployments consistent."""
import copy
import json
import unittest

import test_project
from ose_editor.json_source import loads


class ComponentCommandTests(unittest.TestCase):
    setUp = test_project.ProjectTests.setUp
    command = test_project.ProjectTests.command
    model = test_project.ProjectTests.model

    def test_remove_output_cleans_activation_subscriptions_and_deployment_in_one_undo(self):
        before = self.project.snapshot()
        disk = {key: doc.path.read_bytes() for key, doc in self.project.documents.items()}
        prepared = self.command(dict(action='remove_port', component='sensor_1', direction='outputs', port='signal'))
        node = self.model()['components']['sensor_1']
        self.assertNotIn('signal', node['outputs'])
        self.assertEqual(node['inputs']['clock']['activates']['timeout'], [])
        self.assertFalse(any(sub.get('from') == 'sensor_1.signal' for sub in self.model()['subscriptions']))
        for key in self.project.deployments:
            self.assertNotIn('signal_1', loads(self.project.document(key).text).get('subscriptions', {}))
        self.assertTrue(prepared['handlers_to_review'])
        self.assertEqual(disk, {key: doc.path.read_bytes() for key, doc in self.project.documents.items()})
        self.project.undo(self.project.revision)
        self.assertEqual(before, self.project.snapshot())
        self.project.undo(self.project.revision, redo=True)
        self.assertNotIn('signal', self.model()['components']['sensor_1']['outputs'])

    def test_remove_input_cleans_all_producers_but_keeps_other_inputs(self):
        self.command(dict(action='connect', name='second_producer', **{'from': 'sensor_2.signal', 'to': 'analyzer_1.input'}))
        self.command(dict(action='remove_port', component='analyzer_1', direction='inputs', port='input'))
        self.assertNotIn('input', self.model()['components']['analyzer_1']['inputs'])
        self.assertFalse(any(sub['to'] == 'analyzer_1.input' for sub in self.model()['subscriptions']))
        self.assertIn('input', self.model()['components']['analyzer_2']['inputs'])

    def test_add_output_and_duplicate_never_grant_implicit_activation(self):
        node = copy.deepcopy(self.model()['components']['sensor_1'])
        self.command(dict(action='configure_output', component='sensor_1', port='extra', create=True, description={'root': 'SignalBlock'}))
        self.command(dict(action='duplicate_port', component='sensor_1', direction='outputs', source='signal', port='copy'))
        updated = self.model()['components']['sensor_1']
        self.assertEqual(updated['inputs'], node['inputs'])
        self.assertEqual(updated['outputs']['copy'], node['outputs']['signal'])
        self.assertFalse(any(sub.get('from') in ('sensor_1.extra', 'sensor_1.copy') for sub in self.model()['subscriptions']))

    def test_duplicate_input_preserves_explicit_permissions_and_timer_description(self):
        node = copy.deepcopy(self.model()['components']['sensor_1'])
        self.command(dict(action='duplicate_port', component='sensor_1', direction='inputs', source='clock', port='watchdog'))
        self.assertEqual(self.model()['components']['sensor_1']['inputs']['watchdog'], node['inputs']['clock'])

    def test_event_permissions_are_independent_and_replacing_a_link_is_atomic(self):
        self.command(dict(action='configure_input', component='analyzer_1', port='input',
                          description={'root': 'SignalBlock', 'timeout': .5, 'activates': {'message': ['result']}}))
        before = self.project.snapshot()
        self.command(dict(action='configure_activation', component='analyzer_1', input='input', event='timeout', output='result',
                          previous={'input': 'input', 'event': 'message', 'output': 'result'}, enabled=True))
        links = self.model()['components']['analyzer_1']['inputs']['input']['activates']
        self.assertEqual(links, {'message': [], 'timeout': ['result']})
        self.project.undo(self.project.revision)
        self.assertEqual(self.project.snapshot(), before)

    def test_activating_cycle_is_editable_and_does_not_change_reception(self):
        self.command(dict(action='configure_input', component='sensor_1', port='feedback', description={'root': 'Analysis'}))
        self.command(dict(action='connect', name='return', **{'from': 'analyzer_1.result', 'to': 'sensor_1.feedback'}))
        subscriptions = copy.deepcopy(self.model()['subscriptions'])
        self.command(dict(action='configure_activation', component='sensor_1', input='feedback', event='message', output='signal', enabled=True))
        self.assertEqual(self.model()['subscriptions'], subscriptions)
        from ose.modeling.activation import activation_order
        with self.assertRaisesRegex(ValueError, '--microsteps'):
            activation_order(json.loads(self.project.document(self.project.model).text))

    def test_rejections_leave_all_documents_and_workspace_unchanged(self):
        self.command(dict(action='configure_input', component='sensor_1', port='passive', description={'root': 'SignalBlock', 'mode': 'pull'}))
        commands = [
            dict(action='configure_activation', component='sensor_1', input='passive', event='message', output='signal', enabled=True),
            dict(action='configure_activation', component='sensor_1', input='clock', event='message', output='signal', enabled=True),
            dict(action='configure_activation', component='sensor_1', input='clock', event='timeout', output='missing', enabled=True),
            dict(action='configure_activation', component='sensor_1', input='clock', event='timeout', output='signal', enabled=True),
            dict(action='configure_activation', component='sensor_1', input='clock', event='message', output='signal', enabled=True,
                 previous={'input': 'clock', 'event': 'timeout', 'output': 'signal'}),
            dict(action='configure_output', component='sensor_1', port='signal', description={'root': 'Analysis'}),
            dict(action='configure_output', component='sensor_1', port='signal', create=True, description={'root': 'SignalBlock'}),
            dict(action='duplicate_port', component='sensor_1', direction='inputs', source='clock', port='tick'),
            dict(action='remove_port', component='sensor_1', direction='inputs', port='missing'),
        ]
        for command in commands:
            with self.subTest(command=command):
                before = self.project.snapshot()
                with self.assertRaises((ValueError, KeyError)):
                    self.project.prepare(command, self.project.revision)
                self.assertEqual(before, self.project.snapshot())

    def test_output_rename_updates_permissions_and_connected_endpoints(self):
        self.command(dict(action='rename', kind='port', old='sensor_1.signal', direction='outputs', name='samples'))
        node = self.model()['components']['sensor_1']
        self.assertEqual(node['inputs']['clock']['activates']['timeout'], ['samples'])
        self.assertTrue(any(sub.get('from') == 'sensor_1.samples' for sub in self.model()['subscriptions']))
        before = self.project.snapshot()
        with self.assertRaises(ValueError):
            self.project.prepare(dict(action='rename', kind='port', old='sensor_1.clock', direction='inputs', name='tick'), self.project.revision)
        self.assertEqual(self.project.snapshot(), before)

    def test_known_incompatible_schema_edits_are_rejected_without_partial_changes(self):
        self.project.last_validation = {'revision': self.project.revision, 'connection_contracts': [{
            'root': 'SignalBlock', 'publisher_schema': 'other.sdl',
            'consumer_schema': self.model()['schemas'], 'compatible': False,
            'code': 'type_mismatch', 'path': 'value'}]}
        before = self.project.snapshot()
        with self.assertRaisesRegex(ValueError, 'Incompatible SDL contract'):
            self.project.prepare(dict(action='configure_output', component='sensor_1', port='signal',
                                      description={'root': 'SignalBlock', 'schema': 'other.sdl'}), self.project.revision)
        self.assertEqual(before, self.project.snapshot())

    def test_model_without_deployments_still_rejects_invalid_port_declarations(self):
        self.project.deployments = []
        before = self.project.snapshot()
        with self.assertRaises(ValueError):
            self.project.prepare(dict(action='configure_output', component='sensor_1', port='extra',
                                      description={'root': ''}), self.project.revision)
        self.assertEqual(before, self.project.snapshot())
        self.command(dict(action='configure_output', component='sensor_1', port='extra', description={'root': 'SignalBlock'}))
