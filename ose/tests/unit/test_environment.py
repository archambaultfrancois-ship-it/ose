"""Shell setup only selects paths and provides three application shortcuts."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

FRAMEWORK = Path(__file__).resolve().parents[3]


@unittest.skipUnless(shutil.which('bash') and (FRAMEWORK / 'setenv.sh').is_file(),
                     'requires Bash and framework checkout')
class EnvironmentTests(unittest.TestCase):
    def test_switching_frameworks_selects_current_modules_and_paths(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            frameworks = [root / 'first checkout', root / 'second checkout']
            for checkout in frameworks:
                package = checkout / 'ose/src/ose'
                package.mkdir(parents=True)
                (package / '__init__.py').write_text('')
                (package / 'cli.py').write_text('')
                runtime = checkout / 'sdl/runtime/python'
                runtime.mkdir(parents=True)
                (runtime / 'sdl_runtime.py').write_text('')
                shutil.copyfile(FRAMEWORK / 'setenv.sh', checkout / 'setenv.sh')
            custom = [str(root / 'custom path'), str(root / 'literal [*] path')]
            env = dict(os.environ, OSE_APP_ROOT=str(root), OSE_PYTHON=sys.executable,
                       FIRST_ENV=str(frameworks[0] / 'setenv.sh'),
                       SECOND_ENV=str(frameworks[1] / 'setenv.sh'),
                       PYTHONPATH=':'.join([*custom, str(frameworks[0] / 'ose/src'),
                                            str(frameworks[0] / 'ose/src')]),
                       PROBE='import json, os, ose, sdl_runtime; print(json.dumps({'
                             '"ose": ose.__file__, "sdl": sdl_runtime.__file__, '
                             '"pythonpath": os.environ["PYTHONPATH"].split(":"), '
                             '"path": os.environ["PATH"].split(":")}))')
            env.pop('OSE_PARAMETER_TOKEN', None)
            script = ('set -eu\n'
                      'source "$FIRST_ENV"\n"$OSE_PYTHON" -c "$PROBE"\n'
                      'source "$SECOND_ENV"\n"$OSE_PYTHON" -c "$PROBE"\n'
                      'source "$FIRST_ENV"\n"$OSE_PYTHON" -c "$PROBE"\n'
                      'source "$FIRST_ENV"\n"$OSE_PYTHON" -c "$PROBE"\n')
            result = subprocess.run(['bash', '--noprofile', '--norc', '-c', script],
                                    env=env, capture_output=True, text=True, check=True)
            snapshots = [json.loads(line) for line in result.stdout.splitlines()]
            self.assertEqual(len(snapshots), 4)
            selected = [frameworks[0], frameworks[1], frameworks[0], frameworks[0]]
            for snapshot, checkout in zip(snapshots, selected):
                self.assertEqual(snapshot['ose'], str(checkout / 'ose/src/ose/__init__.py'))
                self.assertEqual(snapshot['sdl'], str(checkout / 'sdl/runtime/python/sdl_runtime.py'))
                expected = [str(checkout / path) for path in ('ose/src', 'sdl/runtime/python',
                            'sdl/runtime/filter/python', 'sdl/build/filter/release', 'sdl/build/recording-deps')]
                self.assertEqual(snapshot['pythonpath'][:5], expected)
                for path in expected:
                    self.assertEqual(snapshot['pythonpath'].count(path), 1)
                recording = str(checkout / 'sdl/tools/recording')
                self.assertEqual(snapshot['path'][0], recording)
                self.assertEqual(snapshot['path'].count(recording), 1)
                self.assertLess(snapshot['pythonpath'].index(custom[0]), snapshot['pythonpath'].index(custom[1]))
            self.assertEqual(snapshots[-1], snapshots[-2])

    def test_setup_defines_only_useful_shortcuts(self):
        with tempfile.TemporaryDirectory() as directory:
            env = dict(os.environ, FRAMEWORK_ENV=str(FRAMEWORK / 'setenv.sh'),
                       OSE_APP_ROOT=directory, OSE_PYTHON=sys.executable)
            script = """set -eu
shopt -s expand_aliases
source "$FRAMEWORK_ENV"
alias -p
app-cd
printf '%s\n' "$PWD"
"""
            result = subprocess.run(['bash', '--noprofile', '--norc', '-c', script],
                                    env=env, text=True, capture_output=True, check=True)
            lines = result.stdout.splitlines()
            self.assertEqual(lines[:-1], ["alias app-build='_ose_env_cli app build'",
                "alias app-cd='_ose_env_app_cd'", "alias app-run='_ose_env_cli app run'",
                "alias ose='_ose_env_cli'"])
            self.assertEqual(lines[-1], directory)

    def test_build_and_run_are_plain_cli_shortcuts(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            probe = root / 'selected python'
            probe.write_text('#!' + sys.executable + '\nimport json, sys\nprint(json.dumps(sys.argv[1:]))\n')
            probe.chmod(0o755)
            env = dict(os.environ, FRAMEWORK_ENV=str(FRAMEWORK / 'setenv.sh'),
                       OSE_APP_ROOT=directory, OSE_PYTHON=str(probe))
            for alias, options in [('app-build', ['SCENARIO=normal']),
                                   ('app-run', ['--duration', '3', 'SCENARIO=normal'])]:
                script = 'set -eu\nshopt -s expand_aliases\nsource "$FRAMEWORK_ENV"\n' + alias + ' "$@"\n'
                result = subprocess.run(['bash', '--noprofile', '--norc', '-c', script, 'test', *options],
                                        env=env, text=True, capture_output=True, check=True)
                self.assertEqual(json.loads(result.stdout), ['-m', 'ose.cli', 'app', alias[4:], *options])

    def test_reloading_clears_old_auto_credentials_but_preserves_manual_overrides(self):
        for token, expected in [('test-old-auto', 'unset'), ('test-manual', 'test-manual')]:
            with self.subTest(token=token):
                env = dict(os.environ, FRAMEWORK_ENV=str(FRAMEWORK / 'setenv.sh'),
                           OSE_APP_ROOT=str(FRAMEWORK), OSE_PYTHON=sys.executable,
                           OSE_PARAMETER_TOKEN=token)
                script = '_ose_env_parameter_token_auto=test-old-auto\nsource "$FRAMEWORK_ENV"\nprintf "%s\\n" "${OSE_PARAMETER_TOKEN-unset}"\n'
                result = subprocess.run(['bash', '--noprofile', '--norc', '-c', script], env=env,
                                        text=True, capture_output=True, check=True)
                self.assertEqual(result.stdout.strip(), expected)

    def test_editor_passes_setup_and_options_to_the_checkout_launcher(self):
        with tempfile.TemporaryDirectory(prefix='editor application ') as directory:
            root = Path(directory)
            probe = root / 'selected python'
            probe.write_text('#!' + sys.executable + '\nimport json, sys\nprint(json.dumps(sys.argv[1:]))\n')
            probe.chmod(0o755)
            env = dict(os.environ, FRAMEWORK_ENV=str(FRAMEWORK / 'setenv.sh'),
                       OSE_APP_ROOT=directory, OSE_PYTHON=str(probe))
            script = 'set -eu\nsource "$FRAMEWORK_ENV"\nose-editor "$@"\n'
            for arguments in (['setup'], ['--root', 'project with spaces', '--port', '8083']):
                result = subprocess.run(['bash', '--noprofile', '--norc', '-c', script, 'test', *arguments],
                                        env=env, text=True, capture_output=True, check=True)
                self.assertEqual(json.loads(result.stdout),
                                 [str(FRAMEWORK / 'ose-editor/tools/editor.py'), *arguments])
