"""Check source composition, parameter sharing and prepared load profiles."""
import json
from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'tools'))
sys.path.insert(0, str(ROOT.parent / 'ose/src'))

from workshop import prepare, PROFILES
from ose.modeling.composition import resolve


class CompositionTests(unittest.TestCase):
    def check_graph(self, model, deployment, metadata):
        self.assertEqual(len(model['components']), 20)
        self.assertEqual(len(model['subscriptions']), 28)
        subscriptions = {sub['id'] for sub in model['subscriptions']}
        self.assertEqual(subscriptions, {
            *(f'analysis_{i}' for i in range(1, 5)),
            *(f'policy_{i}' for i in range(1, 5)),
            'report', 'policy_recording', 'supervision.report', 'recording.archive',
            *(f'cell_{i}.{flow}' for i in range(1, 5) for flow in ('frame', 'analysis', 'command', 'feedback')),
        })
        self.assertEqual(set(metadata['subscriptions']), subscriptions)
        self.assertEqual(set(metadata['instances']), {
            'cell_1', 'cell_2', 'cell_3', 'cell_4', 'supervision', 'recording'})
        self.assertEqual(set(model['shared_parameters']), {
            'workload', *(f'cells/{i}/settings' for i in range(1, 5))})
        ids = {'.'.join(source['path']): name for name, source in metadata['components'].items()}
        self.assertEqual(len({metadata['instances'][f'cell_{i}']['source'] for i in range(1, 5)}), 1)
        for i in range(1, 5):
            for name in ('plant', 'analyzer', 'controller', 'actuator'):
                component = ids[f'cell_{i}.{name}']
                bindings = model['components'][component]['parameter_bindings']
                self.assertEqual(bindings['workload']['path'], 'workload')
                self.assertEqual(bindings['cell']['path'], f'cells/{i}/settings')
                self.assertTrue(Path(deployment['components'][component]['container']['handler']).is_file())
                self.assertEqual(Path(deployment['components'][component]['cwd']).resolve(), ROOT)
                if name in ('plant', 'controller'):
                    self.assertEqual(deployment['components'][component]['env']['THERMAL_CELL'], str(i))
            self.assertEqual(model['components'][ids['supervision.supervisor']]['parameter_bindings'][f'cell_{i}']['path'],
                             f'cells/{i}/settings')
        self.assertEqual(deployment['parameter_clients']['operator']['read'],
                         deployment['parameter_clients']['operator']['write'])
        self.assertEqual(set(deployment['parameter_clients']['operator']['write']), set(model['shared_parameters']))
        return ids

    def test_authored_graph_reuses_cells_without_changing_shared_paths(self):
        self.check_graph(*resolve(ROOT / 'model.json', ROOT / 'deployment.json')[:3])

    def test_all_profiles_retain_hierarchy_and_select_analyzer_without_editing_sources(self):
        sources = {path: path.read_bytes() for path in ROOT.rglob('*.json')
                   if 'build' not in path.relative_to(ROOT).parts and not any(part.startswith('.') for part in path.relative_to(ROOT).parts)}
        for load, profile in PROFILES.items():
            for analyzer in ('c', 'python'):
                with self.subTest(load=load, analyzer=analyzer), tempfile.TemporaryDirectory() as temp:
                    directory = Path(temp)
                    native = directory / 'native'
                    documents = prepare(load, analyzer, native, directory=directory)
                    for name, value in zip(('model', 'deployment'), documents):
                        (directory / f'{name}.json').write_text(json.dumps(value))
                    model, deployment, metadata, _ = resolve(directory / 'model.json', directory / 'deployment.json')
                    ids = self.check_graph(model, deployment, metadata)
                    self.assertEqual(model['shared_parameters']['workload']['initial']['label'], load)
                    for i in range(1, 5):
                        plant = model['components'][ids[f'cell_{i}.plant']]
                        self.assertEqual(plant['inputs']['clock']['timer']['delay'], profile['period'])
                        execution = deployment['components'][ids[f'cell_{i}.analyzer']]
                        self.assertEqual(execution['container']['language'], analyzer)
                    for name, factor in (('supervision.fusion', 2), ('recording.historian', 5)):
                        self.assertEqual(model['components'][ids[name]]['inputs']['clock']['timer']['delay'], profile['period'] * factor)
                    for execution in deployment['components'].values():
                        self.assertEqual(execution['env']['PYTHONPATH'].split(':')[0], str(native))
        self.assertEqual(sources, {path: path.read_bytes() for path in sources})


if __name__ == '__main__':
    unittest.main()
