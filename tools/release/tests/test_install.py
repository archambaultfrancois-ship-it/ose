"""Generic releases install outside the checkout and missing tools only warn."""
from contextlib import redirect_stderr, redirect_stdout
import importlib.util
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch


HERE = Path(__file__).resolve().parents[1]
ROOT = HERE.parents[1]


def module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


installer = module('ose_release_install', HERE / 'install.py')
builder = module('ose_release_build', HERE / 'build.py')


class InstallTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="ose release user's files ")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.release = self.root / 'release'
        self.release.mkdir()
        builder.copy_resources(ROOT, self.release)
        (self.release / 'packages').mkdir()
        for name in ('install.py', 'launch.py', 'ose-install', 'install-resources.sh'):
            shutil.copy2(HERE / name, self.release / name)
        self.seal()
        self.prefix = self.root / 'installed framework'
        self.bin = self.root / 'command files'
        self.environment = patch.dict(os.environ, {key: value for key, value in os.environ.items()
                                                  if key not in ('PYTHONPATH', 'SDL_ROOT', 'FRAMEWORK_ROOT', 'OSE_APP_ROOT')}, clear=True)
        self.environment.start()
        self.addCleanup(self.environment.stop)

    def seal(self):
        manifest = builder.manifest(self.release, ROOT)
        # Tests intentionally omit third-party wheels and assert the warning.
        manifest['requirements'] = {key: [] for key in builder.REQUIREMENTS}
        (self.release / 'manifest.json').write_text(json.dumps(manifest))
        (self.release / 'SHA256SUMS').write_text(''.join(value + '  ' + name + '\n' for name, value in manifest['files'].items()))

    def install(self, *arguments):
        stdout, stderr = io.StringIO(), io.StringIO()
        with redirect_stdout(stdout), redirect_stderr(stderr):
            code = installer.main(['--prefix', str(self.prefix), '--bin-dir', str(self.bin), *arguments], source=self.release)
        self.output, self.errors = stdout.getvalue(), stderr.getvalue()
        return code

    def invoke(self, command, *args, cwd=None):
        return subprocess.run([str(self.bin / command), *args], cwd=cwd or self.root,
                              capture_output=True, text=True, timeout=30)

    def test_maximal_defaults_and_missing_tools_install_with_warnings(self):
        with patch.object(installer.shutil, 'which', return_value=None):
            self.assertEqual(self.install(), 0, self.errors)
        report = json.loads((self.prefix / 'install-report.json').read_text())
        self.assertEqual(report['profile'], 'sdk')
        self.assertEqual(report['languages'], sorted(installer.LANGUAGES))
        self.assertEqual(report['features'], sorted(installer.FEATURES))
        self.assertIn('GNATMAKE', self.errors)
        self.assertIn('No pip wheel', self.errors)
        self.assertFalse(report['python_dependencies'])
        self.assertTrue((self.prefix / 'framework/ose/sdk/ada/ose_component.ads').is_file())
        self.assertTrue((self.prefix / 'framework/sdl/runtime/java/SdlCodec.java').is_file())
        self.assertTrue((self.prefix / 'framework/ose-ws/src/ose_ws/static/index.html').is_file())

    def test_runtime_selection_has_no_compiler_sdks_or_web(self):
        self.assertEqual(self.install('--profile', 'runtime', '--languages', 'python', '--features', 'none'), 0, self.errors)
        self.assertFalse((self.prefix / 'framework/ose/sdk').exists())
        self.assertFalse((self.prefix / 'framework/sdl/sdlc').exists())
        self.assertFalse((self.prefix / 'framework/ose-ws').exists())
        self.assertFalse((self.bin / 'ose-ws').exists())
        self.assertFalse((self.bin / 'sdlc').exists())
        result = self.invoke('ose', '--help')
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_java_selection_retains_shared_native_client_dependencies(self):
        self.assertEqual(self.install('--languages', 'java', '--features', 'none'), 0, self.errors)
        for path in ('client/java/org/sdlose/client/Client.java', 'client/c/ose_client.h',
                     'client/rust/Cargo.toml', 'java/org/sdlose/Component.java'):
            self.assertTrue((self.prefix / 'framework/ose/sdk' / path).is_file(), path)
        self.assertFalse((self.prefix / 'framework/ose/sdk/ada').exists())

    def test_malformed_compiler_environment_is_only_a_warning(self):
        with patch.dict(os.environ, {'CC': "'", 'CARGO': "'", 'JAVAC': "'"}):
            self.assertEqual(self.install(), 0, self.errors)
        self.assertIn('WARNING', self.errors)

    def test_commands_preserve_caller_and_find_installed_resources(self):
        self.assertEqual(self.install('--languages', 'python,c,rust', '--features', 'none'), 0, self.errors)
        caller = self.root / 'independent application'
        caller.mkdir()
        result = self.invoke('ose', 'app', 'env', cwd=caller)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('Application: ' + str(caller), result.stdout)
        self.assertIn('Framework: ' + str(self.prefix / 'framework'), result.stdout)
        self.assertFalse((caller / 'build').exists())
        result = self.invoke('sdlc', '--help', cwd=caller)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_reinstall_removes_deselected_resources_and_commands(self):
        self.assertEqual(self.install('--languages', 'python', '--features', 'web'), 0, self.errors)
        self.assertTrue((self.bin / 'ose-ws').is_symlink())
        self.assertEqual(self.install('--profile', 'runtime', '--languages', 'python', '--features', 'none'), 0, self.errors)
        self.assertFalse((self.prefix / 'framework/ose-ws').exists())
        self.assertFalse((self.bin / 'ose-ws').is_symlink())
        self.assertFalse((self.bin / 'sdlc').is_symlink())

    def test_preview_warns_but_makes_no_destination_changes(self):
        for option in ('--dry-run', '--check-only'):
            with self.subTest(option=option), patch.object(installer.shutil, 'which', return_value=None):
                self.assertEqual(self.install(option), 0, self.errors)
                self.assertIn('WARNING', self.errors)
                self.assertFalse(self.prefix.exists())
                self.assertFalse(self.bin.exists())

    def test_corrupt_payload_is_rejected_before_any_destination_write(self):
        (self.release / 'framework/sdl/runtime/python/sdl_runtime.py').write_text('corrupted')
        self.assertEqual(self.install(), 1)
        self.assertIn('corrupt', self.errors)
        self.assertFalse(self.prefix.exists())

    def test_manifest_cannot_escape_release_directory(self):
        path = self.release / 'manifest.json'
        manifest = json.loads(path.read_text())
        manifest['files']['../outside'] = 'bad'
        path.write_text(json.dumps(manifest))
        self.assertEqual(self.install(), 1)
        self.assertIn('Unsafe release path', self.errors)
        self.assertFalse(self.prefix.exists())

    def test_unrelated_command_is_kept(self):
        self.bin.mkdir()
        existing = self.bin / 'ose'
        existing.write_text('existing command')
        self.assertEqual(self.install('--languages', 'python', '--features', 'none'), 0, self.errors)
        self.assertEqual(existing.read_text(), 'existing command')
        self.assertIn('Existing command kept', self.errors)

    def test_unrelated_destination_is_kept(self):
        self.prefix.mkdir()
        marker = self.prefix / 'application data'
        marker.write_text('keep')
        self.assertEqual(self.install(), 1)
        self.assertEqual(marker.read_text(), 'keep')

    def test_incompatible_target_warns_without_blocking_resource_install(self):
        path = self.release / 'manifest.json'
        manifest = json.loads(path.read_text())
        manifest['target']['platform'] = 'other-platform'
        path.write_text(json.dumps(manifest))
        self.assertEqual(self.install('--languages', 'python', '--features', 'none'), 0, self.errors)
        self.assertIn('other-platform', self.errors)
        self.assertTrue((self.prefix / 'framework/ose/src/ose/cli.py').is_file())

    def test_release_has_no_application_tests_caches_or_frontend_build_tools(self):
        names = list(json.loads((self.release / 'manifest.json').read_text())['files'])
        self.assertFalse(any('vibration-watch' in name for name in names))
        self.assertFalse(any('/tests/' in name or '__pycache__' in name or '/frontend/' in name for name in names))

    def test_python_absence_still_installs_resources_and_can_be_repaired(self):
        # A restricted PATH exposes only shell utilities, deliberately no Python.
        path = self.root / 'shell utilities'
        path.mkdir()
        for name in ('sh', 'dirname', 'pwd', 'cp', 'mkdir', 'ls', 'chmod', 'ln', 'sha256sum', 'readlink'):
            command = shutil.which(name)
            if command:
                (path / name).symlink_to(command)
        result = subprocess.run([shutil.which('sh'), str(self.release / 'ose-install'),
                                 '--prefix', str(self.prefix), '--bin-dir', str(self.bin)],
                                env=dict(os.environ, PATH=str(path)), capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('Python is unavailable', result.stderr)
        self.assertTrue((self.prefix / 'framework/ose/src/ose/cli.py').is_file())
        self.assertEqual(self.install('--languages', 'python', '--features', 'none'), 0, self.errors)
        self.assertEqual(self.invoke('ose', '--help').returncode, 0)


if __name__ == '__main__':
    unittest.main()
