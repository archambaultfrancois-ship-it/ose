"""A release must not advertise stale or incomplete editor assets."""
import hashlib
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest


HERE = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('editor_asset_builder', HERE / 'build.py')
builder = importlib.util.module_from_spec(spec)
spec.loader.exec_module(builder)


class EditorAssetTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        web = self.root / 'ose-ws/src/ose_ws/static'
        web.mkdir(parents=True)
        (web / 'index.html').write_text('web')
        self.static = self.root / 'ose-editor/src/ose_editor/static'
        (self.static / 'assets').mkdir(parents=True)
        (self.static / 'index.html').write_text('editor')
        (self.static / 'assets/app.js').write_text('compiled')
        (self.static / 'licenses.txt').write_text('runtime notices')
        self.frontend = self.root / 'ose-editor/frontend'
        (self.frontend / 'src').mkdir(parents=True)
        (self.frontend / 'scripts').mkdir()
        for name in ('index.html', 'package.json', 'package-lock.json', 'tsconfig.json', 'vite.config.ts', 'src/main.ts', 'scripts/build.mjs'):
            (self.frontend / name).write_text(name)
        self.seal()

    def seal(self):
        sources = sorted(path for path in self.frontend.rglob('*') if path.is_file())
        fingerprint = hashlib.sha256()
        for path in sources:
            fingerprint.update(path.relative_to(self.frontend).as_posix().encode() + b'\0' + path.read_bytes() + b'\0')
        files = {path.relative_to(self.static).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
                 for path in self.static.rglob('*') if path.is_file() and path.name != 'asset-manifest.json'}
        (self.static / 'asset-manifest.json').write_text(json.dumps({'version': 1, 'source_hash': fingerprint.hexdigest(), 'files': files}))

    def test_missing_layout_worker_or_other_advertised_asset_is_rejected(self):
        manifest = json.loads((self.static / 'asset-manifest.json').read_text())
        manifest['files']['assets/layout.worker.js'] = 'missing'
        (self.static / 'asset-manifest.json').write_text(json.dumps(manifest))
        with self.assertRaisesRegex(ValueError, 'Missing or corrupt editor asset'):
            builder.copy_resources(self.root, self.root / 'output')

    def test_stale_compiled_frontend_is_rejected(self):
        (self.frontend / 'src/main.ts').write_text('changed after compilation')
        with self.assertRaisesRegex(ValueError, 'frontend is out of date'):
            builder.copy_resources(self.root, self.root / 'output')

    def test_missing_editor_backend_is_rejected(self):
        with self.assertRaisesRegex(ValueError, 'Missing editor resource'):
            builder.copy_resources(self.root, self.root / 'output')


if __name__ == '__main__':
    unittest.main()
