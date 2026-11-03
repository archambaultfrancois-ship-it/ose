"""Optional parameter sync fails early and follows the application lifecycle."""
import argparse
import importlib.util
import io
from pathlib import Path
import subprocess
import sys
import unittest
from unittest.mock import Mock, call, patch

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("vibration_launch", ROOT / "tools/vibration.py")
workflow = importlib.util.module_from_spec(spec)
spec.loader.exec_module(workflow)


class LaunchTests(unittest.TestCase):
    def test_sync_is_rejected_for_simulation_before_building(self):
        with patch.object(sys, "argv", ["vibration.py", "simulate", "--params-sync"]), \
                patch.object(workflow, "compile_app") as build, patch("sys.stderr", new_callable=io.StringIO) as stderr:
            self.assertEqual(workflow.main(), 1)
            self.assertIn("run and distributed-local only", stderr.getvalue())
            build.assert_not_called()

    def test_missing_watcher_is_reported_before_launch(self):
        args = argparse.Namespace(framework_root=str(ROOT / "deps/sdl-ose"))
        with patch.object(workflow, "environment", return_value={}), \
                patch.object(workflow.subprocess, "run", return_value=subprocess.CompletedProcess([], 1)):
            with self.assertRaisesRegex(ValueError, "optional watcher"):
                workflow.require_params_sync(args)

    def test_synchronizer_exit_stops_both_processes(self):
        args = argparse.Namespace(port=17001, duration=60)
        application, synchronizer = Mock(), Mock()
        application.poll.return_value = None
        synchronizer.poll.return_value = 1
        with patch.object(workflow, "environment", return_value={}), \
                patch.object(workflow.subprocess, "Popen", return_value=application), \
                patch.object(workflow, "wait_for_listener"), \
                patch.object(workflow, "start_params_sync", return_value=synchronizer), \
                patch.object(workflow, "terminate") as terminate:
            with self.assertRaisesRegex(RuntimeError, "params-sync exited"):
                workflow.run_with_params_sync(args)
            self.assertEqual(terminate.call_args_list, [call(synchronizer), call(application)])
