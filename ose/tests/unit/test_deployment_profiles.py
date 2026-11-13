"""Execution profiles preserve application definitions and leave their source intact."""
import copy
import unittest

from ose.modeling.deployment import deployment_profile


class DeploymentProfileTests(unittest.TestCase):
    def setUp(self):
        self.source = {'version': 1, 'execution': 'distributed', 'components': {
            'sensor': {'container': {'language': 'c', 'handler': 'sensor.c'},
                       'placement': {'node_selector': {'kubernetes.io/os': 'linux'}},
                       'cwd': '.', 'env': {'MACHINE': '1'}}},
            'subscriptions': {'signal': {'routing': 'direct', 'transport': 'tcp'}},
            'parameter_clients': {'operator': {'read': ['settings'], 'write': ['settings'],
                                               'token': 'development-only-token'}},
            'process_names': {'prefix': 'app'}}

    def test_local_profile_keeps_implementations_rights_and_source_paths(self):
        original = copy.deepcopy(self.source)
        local = deployment_profile(self.source, 'local')
        self.assertNotIn('execution', local)
        self.assertNotIn('subscriptions', local)
        self.assertNotIn('placement', local['components']['sensor'])
        self.assertEqual(local['components']['sensor']['container']['handler'], 'sensor.c')
        self.assertEqual(local['components']['sensor']['cwd'], '.')
        self.assertEqual(local['parameter_clients'], original['parameter_clients'])
        self.assertEqual(local['process_names'], original['process_names'])
        local['components']['sensor']['env']['MACHINE'] = '2'
        self.assertEqual(self.source, original)

    def test_cluster_profile_removes_tokens_and_can_force_broker_routes(self):
        for routing in (None, 'mixed', 'broker'):
            with self.subTest(routing=routing):
                cluster = deployment_profile(self.source, 'cluster', routing=routing)
                self.assertEqual(cluster['execution'], 'distributed')
                self.assertEqual(cluster['components'], self.source['components'])
                operator = cluster['parameter_clients']['operator']
                self.assertNotIn('token', operator)
                self.assertEqual(operator['read'], ['settings'])
                self.assertEqual(operator['write'], ['settings'])
                self.assertEqual(cluster['subscriptions']['signal']['routing'],
                                 'broker' if routing == 'broker' else 'direct')
        demo = deployment_profile(self.source, 'cluster', development_tokens=True)
        self.assertEqual(demo['parameter_clients'], self.source['parameter_clients'])

    def test_omitted_profile_preserves_existing_deployments(self):
        self.assertEqual(deployment_profile(self.source), self.source)
        local = {'version': 1, 'components': {'sensor': {'command': ['sensor']}}}
        self.assertEqual(deployment_profile(local), local)
        self.assertEqual(deployment_profile(local, 'cluster')['execution'], 'distributed')

    def test_local_profile_preserves_existing_local_transport_settings(self):
        local = {'version': 1, 'components': {'sensor': {'command': ['sensor']}},
                 'subscriptions': {'signal': {'transport': 'udp'}}}
        self.assertEqual(deployment_profile(local, 'local'), local)

    def test_invalid_options_fail_before_projection(self):
        for options in ({'profile': 'unknown'}, {'routing': 'unknown'}, {'routing': 'broker'},
                        {'development_tokens': True}):
            with self.subTest(options=options), self.assertRaises(ValueError):
                deployment_profile(self.source, **options)
