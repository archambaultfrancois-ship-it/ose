"""The console follows the application's current endpoint and parameter identity."""
from contextlib import redirect_stdout
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
FRAMEWORK = ROOT.parent
spec = importlib.util.spec_from_file_location("vibration_web", ROOT / "tools/web_console.py")
web = importlib.util.module_from_spec(spec)
spec.loader.exec_module(web)


class WebConsoleTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(prefix="vibration web ")
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.patch = patch.object(web, "ROOT", self.root)
        self.patch.start()
        self.addCleanup(self.patch.stop)
        self.environment = patch.dict(os.environ, {"FRAMEWORK_ROOT": str(FRAMEWORK)})
        self.environment.start()
        self.addCleanup(self.environment.stop)
        os.environ.pop("OSE_PARAMETER_TOKEN", None)
        self.paths = patch.object(sys, "path", [str(FRAMEWORK / "ose/src"),
                                               str(FRAMEWORK / "ose-ws/src"), *sys.path])
        self.paths.start()
        self.addCleanup(self.paths.stop)
        from ose_ws import cli
        self.cli = cli

    def capture(self, arguments):
        self.arguments = arguments
        self.token = os.environ.get("OSE_PARAMETER_TOKEN")
        self.cwd = Path.cwd()
        return 0

    def invoke(self, arguments=()):
        with patch.object(self.cli, "main", side_effect=self.capture), redirect_stdout(io.StringIO()):
            return web.main(arguments)

    def test_first_launch_initializes_once_and_resolves_fresh_plan_credentials(self):
        (self.root / "deployment.local.json").write_text(json.dumps({
            "parameter_clients": {"operator": {"token": "deployment-test-token"}}}))
        self.assertEqual(self.invoke(), 0)
        self.assertEqual(self.token, "deployment-test-token")
        config = self.root / "ose-ws.json"
        original = config.read_bytes()
        login_token = self.root / ".ose-ws/access.token"
        original_token = login_token.read_bytes()
        self.assertNotEqual(original_token.decode().strip(), self.token)
        self.assertEqual(login_token.stat().st_mode & 0o777, 0o600)
        self.assertEqual(self.arguments, ["serve", "--config", str(config)])
        self.assertEqual(self.cwd, self.root)
        self.assertNotIn("OSE_PARAMETER_TOKEN", os.environ)
        (self.root / "build").mkdir()
        for token in ("first-plan-token", "replacement-plan-token"):
            (self.root / "build/plan.json").write_text(json.dumps({
                "parameter_clients": {"operator": {"token": token}}}))
            self.assertEqual(self.invoke(), 0)
            self.assertEqual(self.token, token)
        self.assertEqual(config.read_bytes(), original)
        self.assertEqual(login_token.read_bytes(), original_token)

    def test_explicit_credentials_and_caller_relative_endpoint_are_preserved(self):
        os.environ["OSE_PARAMETER_TOKEN"] = "manual-test-token"
        endpoint = Path("alternate endpoint.json").resolve()
        self.assertEqual(self.invoke(["--endpoint", "alternate endpoint.json", "--port", "8081"]), 0)
        self.assertEqual(self.token, "manual-test-token")
        self.assertEqual(self.arguments[-4:], ["--endpoint", str(endpoint), "--port", "8081"])
        self.assertEqual(os.environ["OSE_PARAMETER_TOKEN"], "manual-test-token")

    def test_check_and_explicit_config_do_not_initialize_missing_files(self):
        for arguments in (["check"], ["--config", str(self.root / "custom.json")]):
            with self.subTest(arguments=arguments), redirect_stdout(io.StringIO()), \
                    patch("sys.stderr", new_callable=io.StringIO):
                self.assertEqual(self.invoke(arguments), 3)
                self.assertFalse((self.root / "ose-ws.json").exists())
                self.assertFalse((self.root / "custom.json").exists())
                self.assertFalse((self.root / ".ose-ws").exists())
