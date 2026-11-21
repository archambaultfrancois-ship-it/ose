"""Executable subsystem qualification, using actual generated CH/2 processes."""
import asyncio
import json
import os
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest.mock import patch

from ose.modeling.compiler import build, compile_plan
from ose.modeling.composition import resolve
from ose.subsystems.cli import initialize
from ose.subsystems.harness import assemble, execute_case, validate_harness, write_json


SDL_ROOT = os.environ.get('SDL_ROOT')
EXAMPLE = Path(__file__).resolve().parents[2] / 'examples/subsystems'


@unittest.skipUnless(SDL_ROOT, 'set SDL_ROOT')
class SubsystemTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)

    def example(self):
        target = self.root / 'example'
        shutil.copytree(EXAMPLE, target, ignore=shutil.ignore_patterns('build', '__pycache__'))
        return target

    def test_scaffold_is_autonomous_reproducible_and_exports_actual_receptions(self):
        target = initialize(self.root / 'isolated')
        validate_harness(target, SDL_ROOT)
        results = [execute_case(target, 'standalone', 'nominal', self.root / 'artifacts', SDL_ROOT,
                                workers=workers) for workers in (1, 2)]
        for result in results:
            self.assertEqual(result['status'], 'passed', result)
            outputs = json.loads((Path(result['directory']) / 'outputs.json').read_text())
            self.assertEqual([r['time_ns'] for r in outputs], [0, 100000000, 200000000])
            self.assertEqual([r['value']['value'] for r in outputs], [20, 25, 90])
        self.assertEqual(results[0]['summary']['trace_sha256'], results[1]['summary']['trace_sha256'])

    def test_real_neighbor_nested_interfaces_and_public_parameter_updates(self):
        example = self.example()
        for target, profile, case in (
                ('detection', 'standalone', 'threshold'),
                ('detection', 'with-acquisition', 'integration'),
                ('monitoring', 'standalone', 'nominal')):
            with self.subTest(target=target, profile=profile):
                result = execute_case(example / target, profile, case, self.root / 'results',
                                      SDL_ROOT, workers=2)
                self.assertEqual(result['status'], 'passed', result)

    def test_assertion_failure_and_observation_without_expectations(self):
        target = initialize(self.root / 'isolated')
        path = target / 'tests/nominal/expectations.json'
        write_json(path, dict(version=1, assertions=[dict(output='out', count=0)]))
        result = execute_case(target, 'standalone', 'nominal', self.root / 'failed', SDL_ROOT)
        self.assertEqual(result['status'], 'failed', result)
        path.unlink()
        result = execute_case(target, 'standalone', 'nominal', self.root / 'observed', SDL_ROOT, verify=False)
        self.assertEqual(result['status'], 'passed', result)
        self.assertEqual(result['assertions'], [])

    def test_mixed_application_compiles_and_logical_component_selection_works(self):
        example = self.example()
        plan_path = build(example / 'model.json', example / 'deployment.json', SDL_ROOT, self.root / 'build',
                          component='monitoring.detection.processor')
        plan = json.loads(plan_path.read_text())
        paths = [v['path'] for v in plan['composition']['components'].values()]
        self.assertIn(['display'], paths)
        self.assertIn(['monitoring', 'detection', 'processor'], paths)
        self.assertEqual(len(plan['components']), 3)
        from ose.execution.simulation import run
        summary = asyncio.run(run(plan_path, .35))
        self.assertEqual(summary['stop_reason'], 'duration')
        self.assertEqual(summary['publications'], 6)
        cluster = compile_plan(example / 'model.json', example / 'deployment.json', SDL_ROOT,
                               self.root / 'cluster', profile='cluster')
        self.assertEqual(cluster['execution'], 'distributed')
        self.assertEqual(set(cluster['components']), set(plan['components']))

    def test_flat_equivalence_and_definition_relative_generated_substitution(self):
        example = self.example()
        model, deployment, metadata, _ = resolve(example / 'model.json', example / 'deployment.json')
        self.assertIsNotNone(metadata)
        flat_model = write_json(self.root / 'flat.model.json', model)
        flat_deployment = write_json(self.root / 'flat.deployment.json', deployment)
        composed = compile_plan(example / 'model.json', example / 'deployment.json', SDL_ROOT, self.root / 'same')
        flat = compile_plan(flat_model, flat_deployment, SDL_ROOT, self.root / 'same')
        composed.pop('composition')
        # Different root directories only affect provenance; all resolved execution
        # and delivery descriptions must agree for the same build destination.
        self.assertEqual(composed, flat)
        root_model_path = example / 'model.json'
        model = json.loads(root_model_path.read_text())
        (example / 'root.sdl').write_text('message Other { 1: required fl64 value; }\n')
        model['schemas'] = 'root.sdl'
        model['components']['display']['inputs']['input']['schema'] = 'detection/schemas/value.sdl'
        write_json(root_model_path, model)
        child_path = example / 'detection/deployment.json'
        child = json.loads(child_path.read_text())
        child['components']['processor']['env'] = {'SCHEMA_OUTPUT': '{generated}', 'SOURCE': '{project}'}
        write_json(child_path, child)
        plan = compile_plan(root_model_path, example / 'deployment.json', SDL_ROOT, self.root / 'contexts')
        name = next(k for k, v in plan['composition']['components'].items()
                    if v['path'] == ['monitoring', 'detection', 'processor'])
        self.assertNotEqual(plan['components'][name]['env']['SCHEMA_OUTPUT'],
                            str(self.root / 'contexts/generated/shared'))
        self.assertEqual(plan['components'][name]['env']['SOURCE'], str(example / 'detection'))

    @unittest.skipUnless(shutil.which('cc'), 'requires a C compiler')
    def test_native_component_inside_subsystem(self):
        target = initialize(self.root / 'native')
        write_json(target / 'deployment.json', dict(version=1, components={
            'processor': dict(container=dict(language='c', handler='components/processor.c'))}))
        (target / 'components/processor.c').write_text('''#include "component.h"
static int on_input(OseContext *ctx, const Input_input *input) {
    return publish_out(ctx, input->message);
}
const ComponentCallbacks component_callbacks = { .on_input = on_input };
''')
        result = execute_case(target, 'standalone', 'nominal', self.root / 'native-results', SDL_ROOT)
        self.assertEqual(result['status'], 'passed', result)

    def test_runtime_failure_is_distinct_from_assertion_failure(self):
        target = initialize(self.root / 'broken')
        (target / 'components/processor.py').write_text('def on_input(ctx, input):\n    raise RuntimeError("broken")\n')
        result = execute_case(target, 'standalone', 'nominal', self.root / 'broken-results', SDL_ROOT)
        self.assertEqual(result['status'], 'error', result)
        self.assertTrue((Path(result['directory']) / 'result.json').is_file())

    def test_collector_budget_exhaustion_cannot_pass_with_partial_outputs(self):
        target = initialize(self.root / 'bounded')
        def limited(*args, **kwargs):
            assembly = assemble(*args, **kwargs)
            for node in assembly['plan']['components'].values():
                if node.get('parameters', {}).get('output') == 'out':
                    node['parameters']['max_messages'] = 2
            write_json(assembly['plan_path'], assembly['plan'])
            return assembly
        with patch('ose.subsystems.harness.assemble', side_effect=limited):
            result = execute_case(target, 'standalone', 'nominal', self.root / 'bounded-results', SDL_ROOT)
        self.assertEqual(result['status'], 'error', result)
        self.assertNotIn('assertions', result)
