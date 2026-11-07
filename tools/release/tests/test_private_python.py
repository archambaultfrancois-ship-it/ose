"""Exercise native interpreter relocation and installation without host Python."""
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest


HERE = Path(__file__).resolve().parents[1]
ROOT = HERE.parents[1]


def module(name):
    spec = importlib.util.spec_from_file_location('release_' + name, HERE / (name + '.py'))
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


@unittest.skipUnless(sys.platform in ('linux', 'android'), 'ELF release targets')
class PrivatePythonTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory(prefix="ose private Python owner's files ")
        cls.addClassCleanup(cls.temporary.cleanup)
        cls.root = Path(cls.temporary.name)
        cls.builder = module('build')
        cls.release = cls.root / 'release'
        cls.release.mkdir()
        module('private_python').prepare(cls.release)
        cls.builder.copy_resources(ROOT, cls.release)
        (cls.release / 'packages').mkdir()
        for name in ('install.py', 'launch.py', 'ose-install', 'install-resources.sh'):
            shutil.copy2(HERE / name, cls.release / name)
        cls.manifest = cls.builder.manifest(cls.release, ROOT)
        cls.manifest['requirements'] = {key: [] for key in cls.builder.REQUIREMENTS}
        (cls.release / 'manifest.json').write_text(json.dumps(cls.manifest))
        (cls.release / 'SHA256SUMS').write_text(''.join(value + '  ' + name + '\n'
            for name, value in cls.manifest['files'].items()))
        cls.utilities = cls.root / 'only shell utilities'
        cls.utilities.mkdir()
        for name in ('sh', 'dirname', 'pwd', 'cp', 'mkdir', 'ls', 'chmod', 'ln', 'sha256sum', 'readlink', 'rm'):
            command = shutil.which(name)
            if command:
                (cls.utilities / name).symlink_to(command)
        cls.sentinel = cls.root / 'host-python-was-used'
        fake = cls.utilities / 'python3'
        fake.write_text('#!' + shutil.which('sh') + '\n: > "$HOST_PYTHON_SENTINEL"\nexit 97\n')
        fake.chmod(0o755)
        cls.hostile = dict(os.environ, PATH=str(cls.utilities),
                           PYTHONHOME='/missing/system/python', PYTHONPATH='/missing/system/modules',
                           OSE_INSTALL_PYTHON=str(fake), HOST_PYTHON_SENTINEL=str(cls.sentinel))
        cls.hostile.pop('LD_LIBRARY_PATH', None)

    def test_relocated_interpreter_and_children_use_only_private_runtime(self):
        moved = self.root / 'a different runtime location'
        shutil.copytree(self.release / 'python', moved)
        self.addCleanup(shutil.rmtree, moved)
        binary = moved / 'bin/python'
        script = '''import sys, sysconfig, ssl, sqlite3, ctypes, bz2, lzma, zlib, subprocess, pathlib, json
root = pathlib.Path(sys.executable).parents[1]
assert pathlib.Path(sys.prefix) == root
assert sys.flags.no_user_site
assert all(pathlib.Path(p).is_relative_to(root) for p in sys.path)
assert pathlib.Path(sysconfig.get_config_var('LIBDIR')) == root / 'lib'
assert pathlib.Path(sysconfig.get_path('include')).is_relative_to(root)
environment = dict(__import__('os').environ)
environment.pop('LD_LIBRARY_PATH', None)
child = subprocess.check_output([sys.executable, '-I', '-c', 'import sys; print(sys.prefix)'], env=environment, text=True)
assert child.strip() == str(root), child
for line in pathlib.Path('/proc/self/maps').read_text().splitlines():
    path = line.split(maxsplit=5)[-1]
    if any(part in path for part in ('libpython', 'libssl.so', 'libcrypto.so', 'libsqlite3.so', '/lib-dynload/')):
        assert pathlib.Path(path).is_relative_to(root), path
print(json.dumps({'prefix': sys.prefix, 'version': sys.version.split()[0]}))
'''
        result = subprocess.run([str(binary), '-I', '-c', script], env=self.hostile,
                                capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)['prefix'], str(moved))
        self.assertFalse(self.sentinel.exists())

    def install(self, prefix, *options):
        result = subprocess.run([shutil.which('sh'), str(self.release / 'ose-install'),
                                 '--prefix', str(prefix), '--bin-dir', str(prefix / 'bin'),
                                 '--profile', 'runtime', '--languages', 'python', '--features', 'none', *options],
                                env=self.hostile, capture_output=True, text=True, timeout=60)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertFalse(self.sentinel.exists())
        return result

    def test_no_host_python_installs_private_runtime_and_preserves_working_directory(self):
        prefix = self.root / 'installed without system Python'
        self.install(prefix)
        report = json.loads((prefix / 'install-report.json').read_text())
        self.assertTrue(report['python_runtime']['bundled'])
        self.assertTrue(report['capabilities']['engine'])
        self.assertFalse((prefix / '.venv/bin/python').is_symlink())
        caller = self.root / 'separate execution directory'
        caller.mkdir()
        result = subprocess.run([str(prefix / 'bin/ose'), 'app', 'env'], cwd=caller,
                                env=self.hostile, capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('Application: ' + str(caller), result.stdout)
        self.assertIn(str(prefix / '.venv/bin/python'), result.stdout)
        # The bootstrap extraction can disappear; the installed runtime is complete.
        result = subprocess.run([str(prefix / '.venv/bin/python'), '-I', '-c',
                                 'import sys, ssl; print(sys.prefix)'], env=self.hostile,
                                capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), str(prefix / '.venv'))

    def test_upgrade_replaces_old_system_venv_and_reinstallation_is_supported(self):
        prefix = self.root / 'upgraded installation'
        (prefix / '.venv/bin').mkdir(parents=True)
        (prefix / 'install-report.json').write_text('{}')
        (prefix / '.venv/pyvenv.cfg').write_text('home = /old/system/python\n')
        (prefix / '.venv/bin/python').symlink_to(sys.executable)
        for _ in range(2):
            self.install(prefix)
            self.assertFalse((prefix / '.venv/pyvenv.cfg').exists())
            self.assertTrue((prefix / '.venv/runtime.json').is_file())
            self.assertFalse((prefix / '.venv/bin/python').is_symlink())

    def test_preview_uses_private_python_without_creating_destination(self):
        prefix = self.root / 'preview destination'
        self.install(prefix, '--check-only')
        self.assertFalse(prefix.exists())

    def test_shell_recovery_replaces_old_venv_without_following_its_symlinks(self):
        prefix = self.root / 'shell recovery installation'
        (prefix / '.venv/bin').mkdir(parents=True)
        (prefix / 'install-report.json').write_text('{}')
        marker = self.root / 'old interpreter must remain intact'
        marker.write_text('keep')
        (prefix / '.venv/bin/python').symlink_to(marker)
        result = subprocess.run([shutil.which('sh'), str(self.release / 'install-resources.sh'),
                                 '--prefix', str(prefix), '--bin-dir', str(prefix / 'bin')],
                                env=self.hostile, capture_output=True, text=True, timeout=60)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(marker.read_text(), 'keep')
        self.assertFalse((prefix / '.venv/bin/python').is_symlink())
        self.assertTrue((prefix / '.venv/runtime.json').is_file())


if __name__ == '__main__':
    unittest.main()
