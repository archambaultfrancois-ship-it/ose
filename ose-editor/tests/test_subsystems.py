"""Author subsystem boundaries through the revisioned editor, without disk writes."""
import asyncio
import json
from pathlib import Path
import shutil
import tempfile
import time
import unittest
from unittest.mock import patch

from ose.modeling.validation import validate_graph
from ose_editor.project import Project
from ose_editor.subsystems import flatten
from ose_editor.validation import Validator


ROOT = Path(__file__).resolve().parents[2]


class SubsystemTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name) / 'app'
        shutil.copytree(ROOT / 'ose/examples/subsystems', self.root,
                        ignore=shutil.ignore_patterns('build', '__pycache__', '.ose-editor'))
        self.project = Project(self.root)
        self.addCleanup(self.project.close)

    def command(self, command):
        preview = self.project.prepare(command, self.project.revision)
        self.project.apply(preview['id'], preview['revision'])
        return preview

    def test_recursive_interfaces_load_once_and_preserve_source_bytes(self):
        state = self.project.projection()
        self.assertEqual(state['diagnostics'], [])
        ports = state['subsystems']['model.json']['monitoring']
        self.assertEqual(ports['inputs']['measurements']['root'], 'Value')
        self.assertEqual(state['model_contexts']['detection/model.json']['definitions'], ['detection/subsystem.json'])
        before = {key: doc.path.read_bytes() for key, doc in self.project.documents.items()}
        self.project.save(self.project.revision)
        self.assertEqual(before, {key: doc.path.read_bytes() for key, doc in self.project.documents.items()})

    def test_instance_creation_preview_and_undo_do_not_change_other_instances(self):
        command = dict(action='instantiate_subsystem', name='backup', source='monitoring/subsystem.json')
        before = self.project.snapshot()
        preview = self.project.prepare(command, self.project.revision)
        self.assertEqual(self.project.snapshot(), before)
        self.assertEqual([change['file'] for change in preview['changes']], ['model.json'])
        self.project.apply(preview['id'], preview['revision'])
        self.assertIn('backup', self.project.projection()['subsystems']['model.json'])
        flat, deployment, metadata, _ = flatten(self.project)
        validate_graph(flat, deployment)
        self.assertEqual(len(flat['components']), 4)
        self.project.undo(self.project.revision)
        self.assertEqual(self.project.snapshot(), before)

    def test_connections_use_public_interfaces_and_cannot_access_private_ports(self):
        self.command(dict(action='instantiate_subsystem', name='backup', source='detection'))
        self.command(dict(action='connect', name='backup_measurements', **{'from': 'acquisition.measurements', 'to': 'backup.in'}))
        flat, deployment, _, _ = flatten(self.project)
        validate_graph(flat, deployment)
        before = self.project.snapshot()
        with self.assertRaises((KeyError, ValueError)):
            self.project.prepare(dict(action='connect', name='private', **{'from': 'acquisition.sensor', 'to': 'backup.in'}), self.project.revision)
        self.assertEqual(self.project.snapshot(), before)

    def test_shared_definition_edit_is_scoped_and_renames_public_targets(self):
        preview = self.command(dict(action='rename', model='detection/model.json', kind='component', old='processor', name='detector'))
        self.assertEqual({change['file'] for change in preview['changes']},
                         {'detection/model.json', 'detection/deployment.json', 'detection/subsystem.json'})
        self.assertEqual(self.project.model, 'model.json')
        flat, deployment, _, _ = flatten(self.project)
        validate_graph(flat, deployment)
        self.assertIn('monitoring', self.project.projection()['subsystems']['model.json'])
        self.assertIn('"processor"', (self.root / 'detection/model.json').read_text())

    def test_shared_sdl_rename_updates_every_loaded_model_in_its_schema_scope(self):
        preview = self.command(dict(action='rename', kind='sdl', file='detection/schemas/value.sdl', path='/0', name='Reading'))
        self.assertTrue({'model.json', 'acquisition/model.json', 'detection/model.json'}.issubset({change['file'] for change in preview['changes']}))
        state = self.project.projection()
        self.assertEqual(state['diagnostics'], [])
        self.assertEqual(state['subsystems']['model.json']['monitoring']['inputs']['measurements']['root'], 'Reading')
        flat, deployment, _, _ = flatten(self.project)
        validate_graph(flat, deployment)

    def test_public_port_exposure_is_checked_and_previewed(self):
        self.command(dict(action='expose_subsystem_port', model='detection/model.json', definition='detection/subsystem.json',
                          direction='outputs', name='events', target='processor.out'))
        self.assertIn('events', self.project.projection()['subsystems']['monitoring/model.json']['detection']['outputs'])
        before = self.project.snapshot()
        with self.assertRaisesRegex(ValueError, 'unknown or private'):
            self.project.prepare(dict(action='expose_subsystem_port', definition='detection/subsystem.json',
                                 direction='inputs', name='broken', target='processor.missing'), self.project.revision)
        self.assertEqual(self.project.snapshot(), before)

    def test_definition_profiles_are_registered_locally_and_reopened(self):
        self.command(dict(action='deployment', model='detection/model.json', name='alternate.json', copy='detection/deployment.json'))
        self.command(dict(action='select_deployment', model='detection/model.json', file='detection/alternate.json'))
        self.assertEqual(self.project.deployments, ['deployment.json'])
        self.assertIn('detection/alternate.json', self.project.model_contexts['detection/model.json']['deployments'])
        self.project.save(self.project.revision)
        self.project.close()
        self.project = Project(self.root)
        self.addCleanup(self.project.close)
        self.assertIn('detection/alternate.json', self.project.model_contexts['detection/model.json']['deployments'])
        self.assertEqual(self.project.workspace['models']['detection/model.json']['selected_deployment'], 'detection/alternate.json')
        self.assertEqual(self.project.projection()['diagnostics'], [])

    def test_new_definition_is_an_unsaved_undoable_draft(self):
        preview = self.command(dict(action='create_subsystem', name='new_branch', directory='new_branch'))
        self.assertEqual(len(preview['changes']), 4)
        self.assertFalse((self.root / 'new_branch').exists())
        self.assertIn('new_branch/model.json', self.project.projection()['model_contexts'])
        self.command(dict(action='add_component', model='new_branch/model.json', name='clock'))
        self.assertIn('clock', json.loads(self.project.document('new_branch/model.json').text)['components'])

    def test_cycles_and_outside_roots_are_rejected_atomically(self):
        before = self.project.snapshot()
        for command in (dict(action='instantiate_subsystem', model='monitoring/model.json', name='loop', source='subsystem.json'),
                        dict(action='instantiate_subsystem', name='outside', source='../../outside/subsystem.json')):
            with self.subTest(command=command), self.assertRaises(ValueError):
                self.project.prepare(command, self.project.revision)
            self.assertEqual(self.project.snapshot(), before)

    def test_extraction_preserves_deployments_connections_and_execution_paths(self):
        from ose.modeling.compiler import compile_plan
        self.command(dict(action='set', file='deployment.json', path=['components', 'display', 'env'],
                          literal='{"PROJECT":"{project}","SCHEMA":"{generated}"}'))
        before = self.project.snapshot()
        original_flat, original_deployment, _, _ = flatten(self.project)
        before_plan = compile_plan(self.root / 'model.json', self.root / 'deployment.json', ROOT / 'sdl', self.root / 'compiled',
                                   _documents=(original_flat, original_deployment, flatten(self.project)[2], None))
        original_display = original_deployment['components']['display']
        preview = self.command(dict(action='extract_subsystem', components=['display'], name='presentation', directory='presentation'))
        self.assertTrue(any(change['file'] == 'presentation/subsystem.json' for change in preview['changes']))
        flat, deployment, metadata, _ = flatten(self.project)
        validate_graph(flat, deployment)
        display = next(key for key, source in metadata['components'].items() if source['path'] == ['presentation', 'display'])
        self.assertEqual(Path(deployment['components'][display]['container']['handler']).resolve(), Path(original_display['container']['handler']).resolve())
        self.assertEqual(Path(deployment['components'][display]['cwd']).resolve(), Path(original_display['cwd']).resolve())
        self.assertEqual(len(flat['subscriptions']), len(original_flat['subscriptions']))
        after_plan = compile_plan(self.root / 'model.json', self.root / 'deployment.json', ROOT / 'sdl', self.root / 'compiled',
                                  _documents=(flat, deployment, metadata, None))
        self.assertEqual(after_plan['components'][display]['env']['SCHEMA'], before_plan['components']['display']['env']['SCHEMA'])
        self.assertEqual(Path(after_plan['components'][display]['env']['PROJECT']).resolve(), self.root.resolve())
        self.assertNotIn(str(self.root), self.project.document('presentation/deployment.json').text)
        self.assertFalse((self.root / 'presentation').exists())
        self.project.undo(self.project.revision)
        self.assertEqual(self.project.snapshot(), before)

    def test_extraction_migrates_internal_routes_and_each_registered_deployment(self):
        sensor = json.loads(self.project.document('acquisition/model.json').text)['components']['sensor']
        self.command(dict(action='set', file='model.json', path=['components', 'local_sensor'], literal=json.dumps(sensor)))
        self.command(dict(action='set', file='deployment.json', path=['components', 'local_sensor'],
                          literal='{"container":{"language":"python","handler":"acquisition/sensor.py"}}'))
        self.command(dict(action='connect', name='internal', **{'from': 'local_sensor.out', 'to': 'display.input'}))
        self.command(dict(action='set', file='deployment.json', path=['subscriptions'], literal='{"internal":{"transport":"udp"},"alarms":{"transport":"tcp"}}'))
        self.command(dict(action='deployment', name='deployment.alternate.json', copy='deployment.json'))
        self.command(dict(action='set', file='deployment.alternate.json', path=['components', 'display', 'env'], literal='{"MODE":"alternate"}'))
        self.command(dict(action='extract_subsystem', components=['local_sensor', 'display'], name='presentation', directory='presentation'))
        state = self.project.projection()
        self.assertEqual(state['diagnostics'], [])
        local = json.loads(self.project.document('presentation/deployment.json').text)
        alternate = json.loads(self.project.document('presentation/deployment-2.json').text)
        self.assertEqual(local['subscriptions']['internal']['transport'], 'udp')
        self.assertEqual(alternate['components']['display']['env']['MODE'], 'alternate')
        parent = json.loads(self.project.document('deployment.json').text)
        self.assertEqual(parent['subscriptions'], {'alarms': {'transport': 'tcp'}})
        flat, deployment, metadata, _ = flatten(self.project, deployment_key='deployment.alternate.json')
        validate_graph(flat, deployment)
        display = next(key for key, source in metadata['components'].items() if source['path'] == ['presentation', 'display'])
        self.assertEqual(deployment['components'][display]['env']['MODE'], 'alternate')

    def test_unsaved_recursive_model_is_validated_with_its_child_schemas(self):
        validator = Validator(self.project, ROOT / 'sdl')
        self.addCleanup(validator.close)
        self.command(dict(action='rename', model='detection/model.json', kind='component', old='processor', name='detector'))
        job = validator.start(self.project.revision)
        deadline = time.monotonic() + 45
        while time.monotonic() < deadline:
            result = validator.snapshot(job['id'])
            if result['status'] not in ('queued', 'running'):
                break
            time.sleep(.05)
        self.assertEqual(result['status'], 'passed', result)
        self.assertIn('measurements', result['contracts'])
        self.assertFalse((self.root / 'build').exists())

    def test_validation_snapshot_keeps_its_root_during_a_nested_edit_context(self):
        validator = Validator(self.project, ROOT / 'sdl')
        self.addCleanup(validator.close)
        with patch.object(validator.pool, 'submit') as queued:
            job = validator.start(self.project.revision)
        task = queued.call_args.args
        with patch.object(self.project, '_execute', side_effect=lambda command: task[0](*task[1:])):
            self.project.execute(dict(action='workspace', model='detection/model.json'))
        result = validator.snapshot(job['id'])
        self.assertEqual(result['status'], 'passed', result)
        self.assertEqual(result['deployment'], 'deployment.json')
        self.assertIn('measurements', result['contracts'])

    def test_invalid_model_source_still_produces_a_revisioned_validation_job(self):
        self.command(dict(action='source', file='model.json', text='{ unfinished'))
        validator = Validator(self.project, ROOT / 'sdl')
        self.addCleanup(validator.close)
        with patch.object(validator.pool, 'submit') as queued:
            job = validator.start(self.project.revision)
        task = queued.call_args.args
        task[0](*task[1:])
        result = validator.snapshot(job['id'])
        self.assertEqual(result['status'], 'failed')
        self.assertEqual(result['diagnostics'][0]['file'], 'model.json')

    def test_extraction_inside_a_definition_preserves_public_parameters_and_runs(self):
        from ose.modeling.compiler import build
        from ose.execution.simulation import run
        preview = self.command(dict(action='extract_subsystem', model='detection/model.json', components=['processor'],
                                    name='implementation', directory='implementation'))
        self.assertTrue(preview['handlers_to_review'])
        interface = json.loads(self.project.document('detection/subsystem.json').text)['interface']
        self.assertEqual(interface['inputs']['in']['to'], 'implementation.processor_input')
        flat, deployment, _, _ = flatten(self.project)
        validate_graph(flat, deployment)
        self.assertEqual(list(flat['shared_parameters']), ['monitoring/detection/settings'])
        self.project.save(self.project.revision)
        self.project.close()
        self.project = Project(self.root)
        self.addCleanup(self.project.close)
        plan = build(self.root / 'model.json', self.root / 'deployment.json', ROOT / 'sdl', self.root / 'build')
        summary = asyncio.run(run(plan, .35))
        self.assertEqual(summary['publications'], 6)
        self.assertEqual(self.project.projection()['diagnostics'], [])


if __name__ == '__main__':
    unittest.main()
