"""The application shell selects its pinned framework and keeps minimal aliases."""
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
    def test_setup_selects_pinned_framework_and_does_not_load_credentials(self):
        with tempfile.TemporaryDirectory() as temporary:
            env = dict(os.environ, APP_ENV=str(ROOT / 'setenv.sh'), OSE_PYTHON=sys.executable,
                       FRAMEWORK_ROOT='/previous/framework', OSE_APP_ROOT='/previous/application')
            env.pop('OSE_PARAMETER_TOKEN', None)
            script = '''set -eu
shopt -s expand_aliases
source "$APP_ENV"
type ose ose-ws app-build app-run app-cd >/dev/null
for name in params-list params-get params-get-all params-set params-watch params-sync record-inspect; do
    if alias "$name" >/dev/null 2>&1; then exit 1; fi
done
"$OSE_PYTHON" -c 'import json, os; print(json.dumps({"application": os.environ["OSE_APP_ROOT"], "framework": os.environ["FRAMEWORK_ROOT"], "token": os.environ.get("OSE_PARAMETER_TOKEN")}))'
'''
            result = subprocess.run(['bash', '--noprofile', '--norc', '-c', script],
                                    cwd=temporary, env=env, capture_output=True, text=True, check=True)
            self.assertEqual(json.loads(result.stdout), {'application': str(ROOT),
                'framework': str(ROOT / 'deps/sdl-ose'), 'token': None})

    def test_retained_run_shortcut_forwards_to_the_application_cli(self):
        with tempfile.TemporaryDirectory() as temporary:
            python = Path(temporary) / 'selected python'
            python.write_text('#!' + sys.executable + '\nimport json, sys\nprint(json.dumps(sys.argv[1:]))\n')
            python.chmod(0o755)
            env = dict(os.environ, APP_ENV=str(ROOT / 'setenv.sh'), OSE_PYTHON=str(python))
            script = 'set -eu\nshopt -s expand_aliases\nsource "$APP_ENV"\napp-run --duration 3 SCENARIO=normal\n'
            result = subprocess.run(['bash', '--noprofile', '--norc', '-c', script],
                                    cwd=temporary, env=env, capture_output=True, text=True, check=True)
            self.assertEqual(json.loads(result.stdout),
                             ['-m', 'ose.cli', 'app', 'run', '--duration', '3', 'SCENARIO=normal'])

    def test_web_console_uses_its_interpreter_and_application_from_another_directory(self):
        with tempfile.TemporaryDirectory() as temporary:
            python = Path(temporary) / 'web python'
            python.write_text('#!' + sys.executable + '\nimport json, sys\nprint(json.dumps(sys.argv[1:]))\n')
            python.chmod(0o755)
            env = dict(os.environ, APP_ENV=str(ROOT / 'setenv.sh'), OSE_WS_PYTHON=str(python))
            script = 'set -eu\nsource "$APP_ENV"\nose-ws --port 8081 --config "my console.json"\n'
            result = subprocess.run(['bash', '--noprofile', '--norc', '-c', script],
                                    cwd=temporary, env=env, capture_output=True, text=True, check=True)
            self.assertEqual(json.loads(result.stdout), [str(ROOT / 'tools/web_console.py'),
                '--port', '8081', '--config', 'my console.json'])
