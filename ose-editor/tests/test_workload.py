"""Representative model fidelity and isolated authoritative validation."""
from pathlib import Path
import tempfile
import time
import unittest

from ose_editor.project import Project
from ose_editor.validation import Validator
from workload import create


class WorkloadTests(unittest.TestCase):
    def test_thirty_component_project_and_two_thousand_fields(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            model = create(root)
            start = time.perf_counter()
            project = Project(root)
            self.addCleanup(project.close)
            state = project.projection()
            elapsed = time.perf_counter() - start
            self.assertEqual(len(state['documents']['model.json']['value']['components']), 30)
            self.assertEqual(len(model['subscriptions']), 120)
            self.assertEqual(len(model['shared_parameters']), 50)
            self.assertGreaterEqual(sum(len(c['inputs']) + len(c['outputs']) for c in model['components'].values()), 240)
            self.assertEqual(len(project.deployments), 3)
            nodes = state['documents']['schemas/model.sdl']['nodes']
            self.assertGreaterEqual(sum(len(node['children']) for node in nodes), 2000)
            self.assertFalse(state['diagnostics'])
            # Broad regression ceiling, not a claim of desktop UI qualification.
            self.assertLess(elapsed, 10)
            baseline = (root / 'model.json').read_bytes()
            project.save(project.revision)
            self.assertEqual((root / 'model.json').read_bytes(), baseline)
            validator = Validator(project, Path(__file__).resolve().parents[2] / 'sdl')
            self.addCleanup(validator.close)
            job = validator.start(project.revision)
            deadline = time.monotonic() + 180
            while validator.jobs[job['id']]['status'] in ('queued', 'running') and time.monotonic() < deadline:
                time.sleep(.1)
            self.assertEqual(validator.jobs[job['id']]['status'], 'passed', validator.jobs[job['id']]['diagnostics'])


if __name__ == '__main__':
    unittest.main()
