"""The delivered .sh forwards arguments, verifies its payload and cleans up."""
import importlib.util
import os
from pathlib import Path
import shutil
import subprocess
import tarfile
import tempfile
import unittest


HERE = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('ose_bundle_builder', HERE / 'build.py')
builder = importlib.util.module_from_spec(spec)
spec.loader.exec_module(builder)


class SelfExtractTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="ose bundle user's files ")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.runtime = self.root / 'calling directory'
        self.runtime.mkdir()
        self.temp = self.root / 'extraction directory'
        self.temp.mkdir()
        self.payload = self.root / 'ose-release'
        self.payload.mkdir()
        (self.payload / 'ose-install').write_text(
            '#!/bin/sh\nset -eu\numask > "$SFX_RESULT.umask"\nprintf "%s\\000" "$PWD" "$@" > "$SFX_RESULT"\nexit "${SFX_STATUS:-0}"\n')
        self.archive = self.root / 'embedded.tar.gz'
        self.bundle = self.root / "release owner's file.sh"
        self.result = self.root / 'arguments'
        self.build()

    def build(self):
        with tarfile.open(self.archive, 'w:gz') as stream:
            stream.add(self.payload, arcname='ose-release')
        builder.write_bundle(self.archive, self.bundle)

    def invoke(self, *args, overrides=None):
        env = dict(os.environ, TMPDIR=str(self.temp), SFX_RESULT=str(self.result))
        env.pop('OSE_INSTALL_PYTHON', None)
        env.pop('SFX_STATUS', None)
        env.update(overrides or {})
        result = subprocess.run([shutil.which('sh'), str(self.bundle), *args], env=env,
                                cwd=self.runtime, capture_output=True, text=True, timeout=30, umask=0o027)
        self.assertEqual(list(self.temp.iterdir()), [], 'temporary extraction files leaked')
        return result

    def test_forwards_arguments_and_original_working_directory(self):
        arguments = ['--prefix', "destination owner's files", '--features', 'none', '--dry-run']
        result = self.invoke(*arguments)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.result.read_bytes().decode().split('\0')[:-1], [str(self.runtime), *arguments])
        self.assertEqual(int(self.result.with_name(self.result.name + '.umask').read_text().strip(), 8), 0o027)
        self.assertTrue(os.access(self.bundle, os.X_OK))
        checksum = self.bundle.with_name(self.bundle.name + '.sha256').read_text()
        self.assertTrue(checksum.endswith('  ' + self.bundle.name + '\n'))

    def test_preserves_installer_failure_status_and_cleans_up(self):
        result = self.invoke(overrides={'SFX_STATUS': '37'})
        self.assertEqual(result.returncode, 37)
        self.assertTrue(self.result.is_file())

    def test_corruption_does_not_launch_installer(self):
        data = self.bundle.read_bytes()
        self.bundle.write_bytes(data[:-1] + bytes([data[-1] ^ 1]))
        result = self.invoke()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('checksum mismatch', result.stderr)
        self.assertFalse(self.result.exists())

    def test_truncated_bundle_does_not_launch_installer(self):
        self.bundle.write_bytes(self.bundle.read_bytes()[:-64])
        result = self.invoke()
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(self.result.exists())

    def test_missing_embedded_installer_is_reported(self):
        (self.payload / 'ose-install').unlink()
        self.build()
        result = self.invoke()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('installer is missing', result.stderr)

    def test_no_python_or_checksum_program_still_extracts_with_warning(self):
        path = self.root / 'shell utilities'
        path.mkdir()
        for name in ('sh', 'mktemp', 'tail', 'tar', 'rm', 'gzip'):
            command = shutil.which(name)
            if command:
                (path / name).symlink_to(command)
        result = self.invoke('--check-only', overrides={'PATH': str(path)})
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('WARNING', result.stderr)
        self.assertTrue(self.result.is_file())


if __name__ == '__main__':
    unittest.main()
