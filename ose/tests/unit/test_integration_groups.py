"""Ensure CI partitioning cannot silently drop or repeat integration modules."""
import importlib.util
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


spec = importlib.util.spec_from_file_location(
    "run_integration", Path(__file__).resolve().parents[1] / "run_integration.py")
runner = importlib.util.module_from_spec(spec)
spec.loader.exec_module(runner)


class IntegrationGroupTests(unittest.TestCase):
    def test_groups_collect_in_a_fresh_process(self):
        root = Path(__file__).resolve().parents[2]
        for group in runner.GROUPS:
            with self.subTest(group=group):
                result = subprocess.run([
                    sys.executable, "-c",
                    "import sys, unittest; from tests.run_integration import GROUPS; "
                    "loader = unittest.TestLoader(); "
                    "suite = loader.loadTestsFromNames(['tests.integration.' + name "
                    "for name in GROUPS[sys.argv[1]]]); "
                    "assert not loader.errors, '\\n'.join(loader.errors); "
                    "assert suite.countTestCases() > 0", group,
                ], cwd=root, capture_output=True, text=True)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_repository_partition_is_complete(self):
        runner.validate_groups(Path(__file__).resolve().parents[1] / "integration")

    def test_partition_rejects_unassigned_missing_and_duplicate_modules(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "test_one.py").touch()
            (root / "test_two.py").touch()
            groups = {"a": ("test_one", "test_missing"), "b": ("test_one",)}
            with self.assertRaisesRegex(ValueError, "unassigned: test_two; missing: test_missing; duplicated: test_one"):
                runner.validate_groups(root, groups)

    def test_partition_accepts_disjoint_groups(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "test_one.py").touch()
            (root / "test_two.py").touch()
            runner.validate_groups(root, {"a": ("test_one",), "b": ("test_two",)})
