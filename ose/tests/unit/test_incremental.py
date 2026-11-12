"""Real compiler dependencies, cache invalidation and stable Cargo inputs."""
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

from ose.modeling.incremental import build_c, stable_sources


@unittest.skipUnless(shutil.which('cc'), 'requires a C compiler')
class IncrementalTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='ose build ')
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        (self.root / 'shared headers').mkdir()
        self.header = self.root / 'shared headers/value.h'
        self.header.write_text('#define VALUE 1\n')
        (self.root / 'outer.h').write_text('#include "shared headers/value.h"\n')
        self.source = self.root / 'main.c'
        self.source.write_text('#include "outer.h"\nint main(void) { return VALUE; }\n')
        self.binary = self.root / 'program'
        self.command = ['cc', '-std=c99', str(self.source), '-lm', '-o', str(self.binary)]
        self.env = dict(os.environ)

    def build(self):
        return build_c(self.command, cwd=str(self.root), env=self.env)

    def test_identical_content_skips_compilation_even_after_touch(self):
        self.assertTrue(self.build())
        before = self.binary.stat().st_mtime_ns
        os.utime(self.header, ns=(1, 1))
        self.assertFalse(self.build())
        self.assertEqual(self.binary.stat().st_mtime_ns, before)

    def test_changed_transitive_header_rebuilds_and_changes_the_executable(self):
        self.build()
        self.header.write_text('#define VALUE 2\n')
        self.assertTrue(self.build())
        self.assertEqual(subprocess.run([self.binary]).returncode, 2)

    def test_command_environment_and_missing_or_damaged_output_invalidate(self):
        self.build()
        self.command.insert(2, '-O2')
        self.assertTrue(self.build())
        self.env['OSE_BUILD_TEST'] = 'changed'
        self.assertTrue(self.build())
        self.binary.unlink()
        self.assertTrue(self.build())
        self.binary.write_bytes(b'damaged')
        self.assertTrue(self.build())
        self.binary.chmod(0o644)
        self.assertTrue(self.build())
        self.assertFalse(self.build())

    def test_compiler_wrapper_changes_invalidate(self):
        wrapper = self.root / 'compiler'
        wrapper.write_text('#!/bin/sh\nexec cc "$@"\n')
        wrapper.chmod(0o755)
        self.command[0] = './compiler'
        self.build()
        self.assertFalse(self.build())
        wrapper.write_text(wrapper.read_text() + '# changed compiler\n')
        self.assertTrue(self.build())

    def test_failed_build_does_not_record_success(self):
        self.build()
        state = self.root / '.ose-c-build.json'
        before = state.read_bytes()
        self.source.write_text('invalid C;\n')
        with self.assertRaises(subprocess.CalledProcessError):
            self.build()
        self.assertEqual(state.read_bytes(), before)

    def test_regenerated_identical_rust_files_keep_dates_and_removed_files_stay_removed(self):
        same, changed, removed = [self.root / name for name in ('same.rs', 'changed.rs', 'removed.rs')]
        for path in (same, changed, removed):
            path.write_text('old')
            os.utime(path, ns=(1, 1))
        with stable_sources(self.root):
            same.unlink()
            same.write_text('old')
            changed.write_text('new')
            removed.unlink()
        self.assertEqual(same.stat().st_mtime_ns, 1)
        self.assertNotEqual(changed.stat().st_mtime_ns, 1)
        self.assertFalse(removed.exists())
