"""Obsolete application versions fail before codecs, subprocesses or listeners."""
import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import AsyncMock, patch

from ose.execution.adapter import Adapter
from ose.execution.distributed import Coordinator, Worker
from ose.execution.plan import read_plan, require_v2
from ose.execution.process import start_component
from ose.execution.simulation import Simulation
from ose.execution.supervisor import run
from ose.modeling.compiler import compile_plan


class PlanVersionTests(unittest.TestCase):
    def setUp(self):
        self.plan = dict(version=2, execution='distributed', components={
            'probe': dict(execution_model=2, inputs={}, outputs={})}, subscriptions=[])

    def test_plan_requires_explicit_integer_versions(self):
        self.assertIs(require_v2(self.plan), self.plan)
        for version in (None, 1, True, 2.0, '2', 3):
            plan = dict(self.plan, version=version)
            with self.subTest(version=version), self.assertRaisesRegex(ValueError, 'version 2 application plan'):
                require_v2(plan)
        for version in (None, 1, True, 2.0, '2', 3):
            plan = copy.deepcopy(self.plan)
            plan['components']['probe']['execution_model'] = version
            with self.subTest(component_version=version), self.assertRaisesRegex(ValueError, 'component probe.*model 2'):
                require_v2(plan)

    def test_every_engine_rejects_v1_before_loading_sdl(self):
        for plan in (dict(self.plan, version=1), dict(self.plan, components={'probe': dict(execution_model=1)})):
            for launch in (lambda: Adapter(plan, 'probe'), lambda: Worker(plan, 'probe', 'localhost', 1),
                           lambda: Coordinator(plan), lambda: Simulation(plan, .1)):
                with self.subTest(engine=launch), self.assertRaisesRegex(ValueError, 'version 2|model 2'):
                    launch()

    def test_plan_reader_and_compiler_reject_v1_without_writing_build_artifacts(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = root / 'plan.json'
            path.write_text(json.dumps(dict(self.plan, version=1)))
            with self.assertRaisesRegex(ValueError, 'version 2 application plan'):
                read_plan(path)
            model, deployment = root / 'model.json', root / 'deployment.json'
            model.write_text(json.dumps(dict(version=1)))
            deployment.write_text(json.dumps(dict(version=1)))
            with patch('ose.modeling.compiler.SDL2') as codec:
                with self.assertRaisesRegex(ValueError, 'version 2 document'):
                    compile_plan(model, deployment, root / 'no-sdl', root / 'build')
            codec.assert_not_called()
            self.assertFalse((root / 'build').exists())


class StartupVersionTests(unittest.IsolatedAsyncioTestCase):
    async def test_supervisor_refuses_v1_before_starting_a_broker(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'plan.json'
            path.write_text(json.dumps(dict(version=1)))
            with patch('ose.execution.supervisor.Broker') as broker:
                with self.assertRaisesRegex(ValueError, 'version 2 application plan'):
                    await run(path)
            broker.assert_not_called()

    async def test_component_version_and_input_services_are_checked_before_spawn(self):
        with patch('ose.execution.process.asyncio.create_subprocess_exec', new=AsyncMock()) as spawn:
            for node, services in ((dict(execution_model=1), object()), (dict(execution_model=2), None)):
                with self.subTest(node=node), self.assertRaisesRegex(ValueError, 'model 2|input services'):
                    await start_component(node, inputs=services)
            spawn.assert_not_awaited()
