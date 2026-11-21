import copy
import json
from pathlib import Path
import tempfile
import unittest

from ose.modeling.composition import resolve
from ose.modeling.validation import validate_graph


class CompositionTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.desc = {'root': 'Value'}
        self.node = dict(inputs={'input': dict(self.desc, activates={'message': ['out']})},
                         outputs={'out': self.desc})
        self.leaf = self.package('leaf', dict(version=2, schemas='value.sdl',
            components={'filter': self.node, 'detector': self.node}, subscriptions=[
                {'id': 'internal', 'from': 'filter.out', 'to': 'detector.input',
                 'qos': {'capacity': 4}}]),
            dict(inputs={'in': {'to': 'filter.input'}}, outputs={'out': {'from': 'detector.out'}}))
        self.model = dict(version=2, schemas='value.sdl', components={'local': self.node},
            subsystems={'left': {'source': str(self.leaf)}, 'right': {'source': str(self.leaf)}},
            subscriptions=[{'id': 'outside', 'from': 'left.out', 'to': 'right.in'},
                           {'id': 'local', 'from': 'local.out', 'to': 'left.in'}])
        self.deployment = {'version': 1, 'components': {'local': {'command': ['true']}}}

    def write(self, path, value):
        path.write_text(json.dumps(value))
        return path

    def package(self, name, model, interface):
        directory = self.root / name
        directory.mkdir()
        self.write(directory / 'model.json', model)
        self.write(directory / 'deployment.json', dict(version=1, components={
            key: {'command': ['{python}', '{project}/handler.py'], 'env': {'MODEL': '{project}'}}
            for key in model['components']}))
        return self.write(directory / 'subsystem.json', dict(version=1, model='model.json',
            deployment='deployment.json', interface=interface))

    def run_resolver(self):
        return resolve(self.write(self.root / 'model.json', self.model),
                       self.write(self.root / 'deployment.json', self.deployment))

    def test_mixed_instances_import_internal_connections_and_source_context(self):
        model, deployment, metadata, _ = self.run_resolver()
        validate_graph(model, deployment)
        self.assertEqual(len(model['components']), 5)
        self.assertEqual(len(model['subscriptions']), 4)
        internals = [s for s in model['subscriptions'] if s.get('qos') == {'capacity': 4}]
        self.assertEqual(len(internals), 2)
        self.assertNotEqual(internals[0]['from'], internals[1]['from'])
        for actual, origin in metadata['components'].items():
            if len(origin['path']) == 2:
                self.assertTrue(actual.isidentifier())
                self.assertEqual(model['components'][actual]['outputs']['out']['schema'],
                                 str(self.root / 'leaf/value.sdl'))
                self.assertEqual(deployment['components'][actual]['command'][1],
                                 str(self.root / 'leaf/handler.py'))
                self.assertEqual(deployment['components'][actual]['cwd'], str(self.root / 'leaf'))
        again = self.run_resolver()
        self.assertEqual(model, again[0])
        self.assertEqual(metadata, again[2])

    def test_recursive_interfaces_and_only_subsystems(self):
        parent = self.package('parent', dict(version=2, schemas='value.sdl', components={},
            subsystems={'child': {'source': str(self.leaf)}}, subscriptions=[]),
            dict(inputs={'in': {'to': 'child.in'}}, outputs={'out': {'from': 'child.out'}}))
        self.model['components'] = {}
        self.deployment['components'] = {}
        self.model['subsystems']['left']['source'] = str(parent)
        self.model['subscriptions'] = self.model['subscriptions'][:1]
        model, deployment, metadata, _ = self.run_resolver()
        validate_graph(model, deployment)
        self.assertIn('left.child', metadata['instances'])
        origin = next(v for v in metadata['components'].values()
                      if v['path'] == ['left', 'child', 'filter'])
        self.assertEqual(origin['model'], str(self.root / 'leaf/model.json'))

    def test_reject_private_access_cycles_and_name_collisions(self):
        original = copy.deepcopy(self.model)
        for endpoint in ('left.filter.out', 'left.filter', 'left.unknown'):
            self.model = copy.deepcopy(original)
            self.model['subscriptions'][0]['from'] = endpoint
            with self.subTest(endpoint=endpoint), self.assertRaisesRegex(ValueError, 'endpoint'):
                self.run_resolver()
        self.model = copy.deepcopy(original)
        self.model['subsystems']['local'] = self.model['subsystems']['left']
        with self.assertRaisesRegex(ValueError, 'collide'):
            self.run_resolver()
        self.model = original
        leaf_model = json.loads((self.leaf.parent / 'model.json').read_text())
        leaf_model['subsystems'] = {'recursive': {'source': 'subsystem.json'}}
        self.write(self.leaf.parent / 'model.json', leaf_model)
        with self.assertRaisesRegex(ValueError, 'reference cycle'):
            self.run_resolver()

    def test_isolated_public_parameters_and_explicit_parent_binding(self):
        leaf = json.loads((self.leaf.parent / 'model.json').read_text())
        leaf['shared_parameters'] = {'settings': dict(root='Value', initial={'value': 1})}
        leaf['components']['detector']['parameter_bindings'] = {
            'settings': {'path': 'settings', 'access': 'read'}}
        manifest = json.loads(self.leaf.read_text())
        manifest['interface']['parameters'] = {'threshold': {'path': 'settings'}}
        self.write(self.leaf.parent / 'model.json', leaf)
        self.write(self.leaf, manifest)
        self.model['subsystems']['left']['parameters'] = {'threshold': {'value': 2}}
        model, deployment, _, _ = self.run_resolver()
        validate_graph(model, deployment)
        self.assertEqual(model['shared_parameters']['left/settings']['initial'], {'value': 2})
        self.assertEqual(model['shared_parameters']['right/settings']['initial'], {'value': 1})
        self.model['shared_parameters'] = {'common': dict(root='Value', initial={'value': 3})}
        self.model['subsystems']['left'].pop('parameters')
        self.model['subsystems']['left']['parameter_bindings'] = {'threshold': 'common'}
        model, deployment, metadata, _ = self.run_resolver()
        validate_graph(model, deployment)
        self.assertNotIn('left/settings', model['shared_parameters'])
        self.assertEqual(metadata['parameter_contracts'][0]['target'], 'common')

    def test_flat_models_are_returned_unchanged(self):
        self.model.pop('subsystems')
        self.model['subscriptions'] = []
        model, deployment, metadata, _ = self.run_resolver()
        self.assertEqual(model, self.model)
        self.assertEqual(deployment, self.deployment)
        self.assertIsNone(metadata)

    def test_deployment_selection_and_local_overrides(self):
        other = dict(version=1, components={
            name: dict(command=['true'], env={'A': 'a'}) for name in ('filter', 'detector')})
        self.write(self.root / 'custom.json', other)
        self.deployment['subsystems'] = {'left': dict(source='custom.json',
            components={'filter': {'env': {'B': 'b'}}})}
        model, deployment, metadata, _ = self.run_resolver()
        validate_graph(model, deployment)
        actual = next(k for k, v in metadata['components'].items() if v['path'] == ['left', 'filter'])
        self.assertEqual(deployment['components'][actual]['env'], {'A': 'a', 'B': 'b'})
        self.assertEqual(deployment['components'][actual]['cwd'], str(self.root))

    def test_invalid_interfaces_parameters_and_nested_global_settings_are_rejected(self):
        manifest = json.loads(self.leaf.read_text())
        manifest['interface']['inputs'] = []
        self.write(self.leaf, manifest)
        with self.assertRaisesRegex(ValueError, 'interface inputs'):
            self.run_resolver()
        manifest['interface']['inputs'] = {'in': {'to': 'filter.input'}}
        self.write(self.leaf, manifest)
        self.model['subsystems']['left']['parameters'] = []
        with self.assertRaisesRegex(ValueError, 'must be objects'):
            self.run_resolver()
        self.model['subsystems']['left'].pop('parameters')
        deployment_path = self.leaf.parent / 'deployment.json'
        deployment = json.loads(deployment_path.read_text())
        deployment['broker'] = {'host': '127.0.0.1'}
        self.write(deployment_path, deployment)
        with self.assertRaisesRegex(ValueError, 'application-wide'):
            self.run_resolver()
