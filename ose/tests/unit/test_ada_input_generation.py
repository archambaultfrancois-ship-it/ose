"""Public model/scaffold boundaries for Ada v2 without requiring a GNAT install."""
import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from ose.modeling.compiler import compile_plan
from ose.modeling.managed_containers import scaffold_managed


ROOT = Path(__file__).resolve().parents[3]
EXAMPLE = ROOT / 'ose/examples/feedback-pull-v2'


class AdaInputGenerationTests(unittest.TestCase):
    def compile(self, root, model=None, unit='App.Handler'):
        (root / 'value.sdl').write_text((EXAMPLE / 'value.sdl').read_text())
        model = copy.deepcopy(model or json.loads((EXAMPLE / 'model.json').read_text()))
        deployment = dict(version=1, components={name: dict(container=dict(language='ada',
            handler=f'handlers/{name}-handler.adb', unit=name + '.Handler')) for name in model['components']})
        if len(model['components']) == 1:
            name = next(iter(model['components']))
            deployment['components'][name]['container'].update(handler='handlers/' + unit.lower().replace('.', '-') + '.adb', unit=unit)
        (root / 'model.json').write_text(json.dumps(model)); (root / 'deployment.json').write_text(json.dumps(deployment))
        unavailable = {name: '/unavailable/ose-test-' + name.lower() for name in ('CC', 'GNATMAKE', 'CARGO', 'JAVAC')}
        with patch.dict('os.environ', unavailable):
            plan = compile_plan(root / 'model.json', root / 'deployment.json', ROOT / 'sdl', root / 'build')
        self.assertEqual(json.loads((root / 'model.json').read_text()), model)
        return plan

    def test_feedback_model_uses_typed_references_and_keeps_timer_sdl_free(self):
        with tempfile.TemporaryDirectory(prefix='ada v2 source ') as directory:
            root = Path(directory)
            plan = self.compile(root)
            for name, node in plan['components'].items():
                self.assertEqual(node['execution_model'], 2)
                self.assertEqual(node['container']['language'], 'ada')
                self.assertFalse(Path(node['container']['handler']).exists())
                spec = (root / f'build/components/{name}/ose_{name}.ads').read_text()
                self.assertNotIn('On_Tick', spec); self.assertNotIn('type Inputs is', spec)
            node = plan['components']['a']
            self.assertNotIn('catalogue', node['inputs']['clock'])
            spec = (root / 'build/components/a/ose_a.ads').read_text()
            self.assertIn('subtype Input_clock is Ose_Inputs.Metadata;', spec)
            self.assertIn('Message : SDL_value.T_Value;', spec)
            self.assertIn('function Pull_feedback', spec); self.assertNotIn('procedure On_feedback', spec)
            self.assertIn('Id : Ose_Inputs.Optional_Id := Ose_Inputs.No_Id', spec)
            self.assertIn('Id : Interfaces.Unsigned_64', spec)

    def test_child_scaffolds_preserve_user_code_and_reject_partial_pairs(self):
        with tempfile.TemporaryDirectory(prefix='ada v2 child ') as directory:
            root = Path(directory)
            model = dict(version=2, schemas='value.sdl', subscriptions=[], components=dict(probe=dict(inputs=dict(
                clock=dict(kind='timer', timer=dict(mode='one_shot', delay=.01)), feedback=dict(root='Value', mode='pull')), outputs={})))
            node = self.compile(root, model, 'App.Handlers.Main_Handler')['components']['probe']
            container = node['container']
            scaffold_managed(node, container)
            handler = Path(container['handler']); spec = handler.with_suffix('.ads')
            self.assertTrue((handler.parent / 'app.ads').is_file()); self.assertTrue((handler.parent / 'app-handlers.ads').is_file())
            self.assertIn('Input : Ose_probe.Input_clock', spec.read_text())
            self.assertNotIn('On_feedback', spec.read_text()); self.assertNotIn('On_Tick', spec.read_text())
            handler.write_text(handler.read_text() + '-- user-owned code\n')
            before = {p: p.read_bytes() for p in (handler, spec)}
            scaffold_managed(node, container)
            self.assertEqual({p: p.read_bytes() for p in before}, before)
            handler.unlink()
            with self.assertRaisesRegex(ValueError, 'partial pair'):
                scaffold_managed(node, container)
            self.assertFalse(handler.exists()); self.assertEqual(spec.read_bytes(), before[spec])

    def test_v2_generation_covers_passive_empty_and_parameter_only_components(self):
        for kind in ('empty', 'passive', 'parameters'):
            with self.subTest(kind=kind), tempfile.TemporaryDirectory(prefix='ada v2 edge ') as directory:
                root = Path(directory)
                node = dict(inputs={}, outputs={})
                model = dict(version=2, schemas='value.sdl', components=dict(probe=node), subscriptions=[])
                if kind == 'passive':
                    node['inputs']['feedback'] = dict(root='Value', mode='pull')
                if kind == 'parameters':
                    model['shared_parameters'] = {'settings/value': dict(root='Value', initial=dict(count=4))}
                    node['parameter_bindings'] = dict(settings=dict(path='settings/value', access='write'))
                plan = self.compile(root, model)
                component = plan['components']['probe']
                scaffold_managed(component, component['container'])
                handler = Path(component['container']['handler'])
                self.assertIn('pragma Elaborate_Body;', handler.with_suffix('.ads').read_text())
                self.assertIn('package body App.Handler is', handler.read_text())
                spec = (root / 'build/components/probe/ose_probe.ads').read_text()
                self.assertNotIn('On_Tick', spec)
                self.assertNotIn('procedure On_feedback', spec)
                if kind == 'parameters':
                    self.assertIn('Set_Parameters_settings', spec)
