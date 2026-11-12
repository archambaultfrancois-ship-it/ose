"""Optional parameter sync fails early and follows the application lifecycle."""
import argparse
import importlib.util
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("vibration_launch", ROOT / "tools/vibration.py")
workflow = importlib.util.module_from_spec(spec)
spec.loader.exec_module(workflow)


class LaunchTests(unittest.TestCase):
    def test_profiles_preserve_components_and_rights_from_single_source(self):
        source = (ROOT / "deployment.json").read_bytes()
        canonical = json.loads(source)
        for profile, task, sync, routing in (
                ("local", "build", None, "mixed"),
                ("local", "replay-build", None, "broker"),
                ("cluster", "build", None, "mixed"),
                ("cluster", "replay-build", None, "broker"),
                ("cluster", "distributed-local", "parameters", "mixed")):
            args = argparse.Namespace(scenario="normal", profile=profile, task=task,
                                      params_sync=sync, routing=routing)
            with self.subTest(profile=profile, task=task, routing=routing), \
                    patch.object(workflow, "write") as write:
                workflow.prepare(args)
                deployment = write.call_args_list[1].args[1]
                self.assertEqual(set(deployment["components"]), set(canonical["components"]))
                operator = deployment["parameter_clients"]["operator"]
                for permission in ("read", "write"):
                    self.assertEqual(operator[permission], canonical["parameter_clients"]["operator"][permission])
                self.assertEqual("token" in operator, profile == "local" or sync is not None)
                if profile == "local":
                    self.assertNotIn("execution", deployment)
                    self.assertNotIn("subscriptions", deployment)
                    self.assertTrue(all("placement" not in value for value in deployment["components"].values()))
                else:
                    self.assertEqual(deployment["execution"], "distributed")
                    for name, component in deployment["components"].items():
                        self.assertEqual(component["placement"], canonical["components"][name]["placement"])
                    self.assertEqual(deployment["subscriptions"]["signal_1"]["routing"],
                                     "broker" if routing == "broker" else "direct")
        self.assertEqual((ROOT / "deployment.json").read_bytes(), source)

    def test_operator_token_is_available_before_first_build(self):
        from ose.app import Application
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {}, clear=True):
            root = Path(directory)
            (root / "deployment.json").write_bytes((ROOT / "deployment.json").read_bytes())
            context = Application(argparse.Namespace(root=directory, app_sdl_root=str(ROOT.parent / "sdl")))
            context.parameter_token(argparse.Namespace(token_env="OSE_PARAMETER_TOKEN", client="operator"))
            self.assertEqual(os.environ["OSE_PARAMETER_TOKEN"], "vibration-watch-local-operator")

    def test_old_python_is_rejected_before_preparing_or_building(self):
        with patch.object(sys, "argv", ["vibration.py", "build"]), \
                patch.object(sys, "version_info", (3, 8, 20)), \
                patch.object(sys, "version", "3.8.20"), \
                patch.object(workflow, "compile_app") as build, \
                patch("sys.stderr", new_callable=io.StringIO) as stderr:
            self.assertEqual(workflow.main(), 1)
            self.assertIn("Python 3.10+ is required", stderr.getvalue())
            self.assertIn("is Python 3.8.20", stderr.getvalue())
            self.assertIn(sys.executable, stderr.getvalue())
            self.assertIn("Python 3.10+ interpreter from PATH", stderr.getvalue())
            build.assert_not_called()

    def test_supported_python_builds(self):
        for version in ((3, 10, 0), (3, 11, 0), (3, 12, 12)):
            with self.subTest(version=version), \
                    patch.object(sys, "argv", ["vibration.py", "build"]), \
                    patch.object(sys, "version_info", version), \
                    patch.object(workflow, "compile_app") as build:
                self.assertEqual(workflow.main(), 0)
                build.assert_called_once()

    def test_explicit_contract_paths_survive_preparing_and_replay_models(self):
        for task in ("prepare", "replay-build"):
            args = argparse.Namespace(scenario="normal", profile="local", task=task, routing="mixed")
            with self.subTest(task=task), patch.object(workflow, "write") as write:
                workflow.prepare(args)
                model = write.call_args_list[0].args[1]
                self.assertEqual(model["components"]["observer"]["inputs"]["input"]["schema"],
                                 str(ROOT / "components/observer/observer_alarm.sdl"))
                self.assertEqual(model["shared_parameters"]["observer/display"]["schema"],
                                 str(ROOT / "schemas/observer_settings.sdl"))

    def test_sync_is_rejected_for_simulation_before_building(self):
        with patch.object(sys, "argv", ["vibration.py", "simulate", "--params-sync"]), \
                patch.object(workflow, "compile_app") as build, patch("sys.stderr", new_callable=io.StringIO) as stderr:
            self.assertEqual(workflow.main(), 1)
            self.assertIn("run and distributed-local only", stderr.getvalue())
            build.assert_not_called()

    def test_sync_run_delegates_to_ose_supervisor(self):
        with patch.object(sys, "argv", ["vibration.py", "run", "--params-sync", "settings", "--port", "17001"]), \
                patch.object(workflow, "compile_app"), \
                patch.object(workflow, "read", return_value={"parameter_clients": {"operator": {"token": "test-plan"}}}), \
                patch("ose.execution.launch.require_sync"), \
                patch.object(workflow.os, "chdir"), \
                patch.object(workflow.os, "execve") as execute:
            self.assertEqual(workflow.main(), 0)
        command = execute.call_args.args[1]
        self.assertIn("--params-sync", command)
        self.assertIn("settings", command)
        self.assertIn("17001", command)
        self.assertEqual(command[1:4], ["-m", "ose.cli", "run"])
