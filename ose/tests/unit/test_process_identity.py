import os
import unittest
from unittest.mock import patch

from ose.execution.identity import aliases_for, component_environment, name, validate
import ose.execution.identity as identity


class IdentityTests(unittest.TestCase):
    def test_names_fit_linux_and_distinguish_roles_and_components(self):
        components = dict.fromkeys(('sensor_1', 'analyzer_1', 'sensor1', 'é' * 30,
                                    'a_very_long_component_name', 'a_very_long_component_other'))
        aliases = aliases_for(components)
        plan = {'process_names': {'prefix': 'vw', 'components': aliases}, 'components': components}
        validate(plan['process_names'], components)
        with patch.dict(os.environ, {}, clear=True):
            self.assertEqual(name('c', plan, 'analyzer_1'), 'vw-c-analyzer1')
            labels = [name(role, plan, component) for role in ('a', 'c', 'w') for component in components]
            self.assertEqual(len(labels), len(set(labels)))
            self.assertTrue(all(label.isascii() and len(label) <= 15 for label in labels))
            self.assertEqual(aliases, aliases_for(reversed(list(components))))
        with patch.dict(os.environ, {'OSE_PROCESS_PREFIX': 'other'}):
            labels = [name('c', plan, component) for component in components]
            self.assertTrue(all(label.startswith('other-c-') and len(label) <= 15 for label in labels))
            self.assertEqual(len(labels), len(set(labels)))

    def test_reject_invalid_configuration_and_explicit_collisions(self):
        for config in (None, {'prefix': 'longprefix'}, {'prefix': 'VW'}, {'unknown': 1},
                       {'components': {'unknown': 'short'}},
                       {'components': {'a': 'same', 'b': 'same'}}):
            with self.subTest(config=config), self.assertRaises(ValueError):
                validate(config, {'a': {}, 'b': {}})
        with patch.dict(os.environ, {'OSE_PROCESS_PREFIX': 'bad prefix'}), self.assertRaises(ValueError):
            name('broker')

    def test_component_environment_uses_same_alias_and_runtime_prefix(self):
        node = {'process_identity': {'prefix': 'vw', 'component': 'sensor_1', 'alias': 'sensor1'}}
        with patch.dict(os.environ, {'OSE_PROCESS_PREFIX': 'vw2'}):
            self.assertEqual(component_environment(node),
                             {'OSE_PROCESS_NAME': 'vw2-c-sensor1', 'OSE_PROCESS_PREFIX': 'vw2'})

    def test_long_thread_owners_keep_the_instance_filter_prefix(self):
        for owner in ('vw-c-analyzer1', 'other-c-sensor1'):
            with patch.object(identity, '_label', owner):
                labels = [identity.thread_name(role) for role in ('io', 'cb', 'disk')]
                self.assertTrue(all(label.startswith(owner.split('-')[0] + '-') for label in labels))
                self.assertTrue(all(len(label) <= 15 for label in labels))
