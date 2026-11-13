"""Stored editor selections survive replacement of deployment source files."""
import json
from pathlib import Path
import tempfile
import unittest

from ose_editor.project import Project


class DeploymentDiscoveryTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        (self.root / 'model.json').write_text(json.dumps(dict(
            version=2, schemas='schemas', components={}, subscriptions=[])))
        (self.root / 'schemas').mkdir()
        state = self.root / '.ose-editor'
        state.mkdir()
        self.workspace = state / 'workspace.json'
        self.workspace.write_text(json.dumps(dict(version=1,
            deployments=['deployment.cluster.json', 'deployment.local.json'],
            selected_deployment='deployment.cluster.json', positions={'source': {'x': 10, 'y': 20}})))

    def deployment(self, name):
        (self.root / name).write_text('{"version":1,"components":{}}\n')

    def open(self, **kwargs):
        project = Project(self.root, **kwargs)
        self.addCleanup(project.close)
        return project

    def test_single_deployment_replaces_missing_remembered_files_without_changing_sources(self):
        self.deployment('deployment.json')
        source = (self.root / 'deployment.json').read_bytes()
        project = self.open()
        self.assertEqual(project.deployments, ['deployment.json'])
        self.assertEqual(project.selected_deployment, 'deployment.json')
        self.assertEqual(project.workspace['positions'], {'source': {'x': 10, 'y': 20}})
        project.save(project.revision, allow_invalid=True)
        self.assertEqual((self.root / 'deployment.json').read_bytes(), source)
        saved = json.loads(self.workspace.read_text())
        self.assertEqual(saved['deployments'], ['deployment.json'])
        self.assertEqual(saved['selected_deployment'], 'deployment.json')
        project.close()
        self.assertEqual(self.open().deployments, ['deployment.json'])
        self.assertFalse((self.root / 'deployment.cluster.json').exists())
        self.assertFalse((self.root / 'deployment.local.json').exists())

    def test_existing_remembered_selection_is_preserved_and_missing_entry_is_removed(self):
        self.deployment('deployment.local.json')
        self.deployment('deployment.json')
        project = self.open()
        self.assertEqual(project.deployments, ['deployment.local.json'])
        self.assertEqual(project.selected_deployment, 'deployment.local.json')

    def test_explicit_selection_takes_precedence_over_stored_selection(self):
        self.deployment('deployment.cluster.json')
        self.deployment('deployment.json')
        self.assertEqual(self.open(deployments=['deployment.json']).deployments, ['deployment.json'])

    def test_explicit_missing_file_remains_an_error(self):
        self.deployment('deployment.json')
        with self.assertRaisesRegex(ValueError, 'Source file does not exist: deployment.cluster.json'):
            self.open(deployments=['deployment.cluster.json'])
        self.assertEqual(self.open().deployments, ['deployment.json'])

    def test_create_uses_the_single_default_instead_of_recreating_obsolete_files(self):
        project = self.open(create=True)
        self.assertEqual(project.deployments, ['deployment.json'])
        self.assertFalse((self.root / 'deployment.cluster.json').exists())
