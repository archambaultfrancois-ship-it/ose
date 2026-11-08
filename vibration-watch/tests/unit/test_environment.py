"""The application shell selects its parent framework and keeps minimal aliases."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]


@unittest.skipUnless(shutil.which('bash'), 'requires Bash')
class EnvironmentTests(unittest.TestCase):
    def setUp(self):
        from unittest.mock import patch
        environment = patch.dict(os.environ, PATH=str(Path(sys.executable).parent) + os.pathsep + os.environ['PATH'])
        environment.start()
        self.addCleanup(environment.stop)

    def test_setup_selects_parent_framework_and_does_not_load_credentials(self):
        with tempfile.TemporaryDirectory() as temporary:
            env = dict(os.environ, APP_ENV=str(ROOT / 'setenv.sh'),
                       FRAMEWORK_ROOT='/previous/framework', OSE_APP_ROOT='/previous/application')
            env.pop('OSE_PARAMETER_TOKEN', None)
            script = '''set -eu
shopt -s expand_aliases
source "$APP_ENV"
type ose ose-ws app-build app-run app-cd >/dev/null
for name in params-list params-get params-get-all params-set params-watch params-sync record-inspect; do
    if alias "$name" >/dev/null 2>&1; then exit 1; fi
done
"$PYTHON" -c 'import json, os; print(json.dumps({"application": os.environ["OSE_APP_ROOT"], "framework": os.environ["FRAMEWORK_ROOT"], "token": os.environ.get("OSE_PARAMETER_TOKEN")}))'
'''
            result = subprocess.run(['bash', '--noprofile', '--norc', '-c', script],
                                    cwd=temporary, env=env, capture_output=True, text=True, check=True)
            self.assertEqual(json.loads(result.stdout), {'application': str(ROOT),
                'framework': str(ROOT.parent), 'token': None})

    def test_retained_run_shortcut_forwards_to_the_application_cli(self):
        with tempfile.TemporaryDirectory() as temporary:
            python = Path(temporary) / 'python3'
            python.write_text('#!' + sys.executable + '\nimport json, sys\nif len(sys.argv) > 2 and sys.argv[1] == "-c" and "sys.version_info" in sys.argv[2]:\n    exec(sys.argv[2])\nelse:\n    print(json.dumps(sys.argv[1:]))\n')
            python.chmod(0o755)
            env = dict(os.environ, APP_ENV=str(ROOT / 'setenv.sh'), PATH=temporary + os.pathsep + os.environ['PATH'])
            script = 'set -eu\nshopt -s expand_aliases\nsource "$APP_ENV"\napp-run --duration 3 SCENARIO=normal\n'
            result = subprocess.run(['bash', '--noprofile', '--norc', '-c', script],
                                    cwd=temporary, env=env, capture_output=True, text=True, check=True)
            self.assertEqual(json.loads(result.stdout),
                             ['-m', 'ose.cli', 'app', 'run', '--duration', '3', 'SCENARIO=normal'])

    def test_web_console_uses_the_same_path_python_from_another_directory(self):
        with tempfile.TemporaryDirectory() as temporary:
            python = Path(temporary) / 'python3'
            python.write_text('#!' + sys.executable + '\nimport json, sys\nif len(sys.argv) > 2 and sys.argv[1] == "-c" and "sys.version_info" in sys.argv[2]:\n    exec(sys.argv[2])\nelse:\n    print(json.dumps(sys.argv[1:]))\n')
            python.chmod(0o755)
            env = dict(os.environ, APP_ENV=str(ROOT / 'setenv.sh'), PATH=temporary + os.pathsep + os.environ['PATH'])
            script = 'set -eu\nsource "$APP_ENV"\nose-ws --port 8081 --config "my console.json"\n'
            result = subprocess.run(['bash', '--noprofile', '--norc', '-c', script],
                                    cwd=temporary, env=env, capture_output=True, text=True, check=True)
            self.assertEqual(json.loads(result.stdout), [str(ROOT / 'tools/web_console.py'),
                '--port', '8081', '--config', 'my console.json'])
