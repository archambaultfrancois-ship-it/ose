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
    def setUp(self):
        from unittest.mock import patch
        environment = patch.dict(os.environ, PATH=str(Path(sys.executable).parent) + os.pathsep + os.environ['PATH'])
        environment.start()
        self.addCleanup(environment.stop)

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
                (checkout / 'tools').mkdir()
                shutil.copyfile(FRAMEWORK / 'tools/find-python.sh', checkout / 'tools/find-python.sh')
            custom = [str(root / 'custom path'), str(root / 'literal [*] path')]
            env = dict(os.environ, OSE_APP_ROOT=str(root),
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
                      'source "$FIRST_ENV"\n"$PYTHON" -c "$PROBE"\n'
                      'source "$SECOND_ENV"\n"$PYTHON" -c "$PROBE"\n'
                      'source "$FIRST_ENV"\n"$PYTHON" -c "$PROBE"\n'
                      'source "$FIRST_ENV"\n"$PYTHON" -c "$PROBE"\n')
            result = subprocess.run(['bash', '--noprofile', '--norc', '-c', script],
                                    env=env, capture_output=True, text=True, check=True)
            snapshots = [json.loads(line) for line in result.stdout.splitlines()]
            self.assertEqual(len(snapshots), 4)
            selected = [frameworks[0], frameworks[1], frameworks[0], frameworks[0]]
            for snapshot, checkout in zip(snapshots, selected):
                self.assertEqual(snapshot['ose'], str(checkout / 'ose/src/ose/__init__.py'))
                self.assertEqual(snapshot['sdl'], str(checkout / 'sdl/runtime/python/sdl_runtime.py'))
                expected = [str(checkout / path) for path in ('ose/src', 'sdl/runtime/python',
                            'sdl/runtime/filter/python', 'sdl/build/filter/release', 'sdl/build/recording-deps', 'build/python-deps')]
                self.assertEqual(snapshot['pythonpath'][:6], expected)
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
                       OSE_APP_ROOT=directory)
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
            probe = root / 'python3'
            probe.write_text('#!' + sys.executable + '\nimport json, sys\nif len(sys.argv) > 2 and sys.argv[1] == "-c" and "sys.version_info" in sys.argv[2]:\n    exec(sys.argv[2])\nelse:\n    print(json.dumps(sys.argv[1:]))\n')
            probe.chmod(0o755)
            env = dict(os.environ, FRAMEWORK_ENV=str(FRAMEWORK / 'setenv.sh'),
                       OSE_APP_ROOT=directory, PATH=str(root) + os.pathsep + os.environ['PATH'])
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
                           OSE_APP_ROOT=str(FRAMEWORK),
                           OSE_PARAMETER_TOKEN=token)
                script = '_ose_env_parameter_token_auto=test-old-auto\nsource "$FRAMEWORK_ENV"\nprintf "%s\\n" "${OSE_PARAMETER_TOKEN-unset}"\n'
                result = subprocess.run(['bash', '--noprofile', '--norc', '-c', script], env=env,
                                        text=True, capture_output=True, check=True)
                self.assertEqual(result.stdout.strip(), expected)

    def test_editor_passes_setup_and_options_to_the_checkout_launcher(self):
        with tempfile.TemporaryDirectory(prefix='editor application ') as directory:
            root = Path(directory)
            probe = root / 'python3'
            probe.write_text('#!' + sys.executable + '\nimport json, sys\nif len(sys.argv) > 2 and sys.argv[1] == "-c" and "sys.version_info" in sys.argv[2]:\n    exec(sys.argv[2])\nelse:\n    print(json.dumps(sys.argv[1:]))\n')
            probe.chmod(0o755)
            env = dict(os.environ, FRAMEWORK_ENV=str(FRAMEWORK / 'setenv.sh'),
                       OSE_APP_ROOT=directory, PATH=str(root) + os.pathsep + os.environ['PATH'])
            script = 'set -eu\nsource "$FRAMEWORK_ENV"\nose-editor "$@"\n'
            for arguments in (['setup'], ['--root', 'project with spaces', '--port', '8083']):
                result = subprocess.run(['bash', '--noprofile', '--norc', '-c', script, 'test', *arguments],
                                        env=env, text=True, capture_output=True, check=True)
                self.assertEqual(json.loads(result.stdout),
                                 [str(FRAMEWORK / 'ose-editor/tools/editor.py'), *arguments])

    def test_old_path_python_is_rejected_by_shell_and_make(self):
        with tempfile.TemporaryDirectory() as temporary:
            python = Path(temporary) / 'python3'
            python.write_text('#!' + sys.executable + '\nimport sys\nsys.version_info = (3, 9)\nexec(sys.argv[2])\n')
            python.chmod(0o755)
            for command in ('bash', 'make', 'sh', 'dirname'):
                (Path(temporary) / command).symlink_to(shutil.which(command))
            env = dict(os.environ, PATH=temporary,
                       FRAMEWORK_ENV=str(FRAMEWORK / 'setenv.sh'))
            commands = (['bash', '-c', 'source "$FRAMEWORK_ENV"'],
                        ['make', '-n', '-C', str(FRAMEWORK), 'check'])
            for command in commands:
                with self.subTest(command=command):
                    result = subprocess.run(command, env=env, text=True, capture_output=True)
                    self.assertNotEqual(result.returncode, 0)
                    self.assertIn('Python 3.10+ is required', result.stderr)

    def test_versioned_python_in_path_is_shared_by_shell_and_make(self):
        with tempfile.TemporaryDirectory(prefix='versioned python ') as temporary:
            directory = Path(temporary)
            old = directory / 'python3'
            old.write_text('#!' + sys.executable + '\nraise SystemExit(1)\n')
            old.chmod(0o755)
            selected = directory / 'python3.12'
            selected.symlink_to(sys.executable)
            for command in ('bash', 'make', 'sh', 'dirname'):
                (directory / command).symlink_to(shutil.which(command))
            env = dict(os.environ, PATH=temporary,
                       FRAMEWORK_ENV=str(FRAMEWORK / 'setenv.sh'))
            shell = subprocess.run(['bash', '-c', 'source "$FRAMEWORK_ENV" && printf "%s\\n" "$PYTHON"'],
                                   env=env, text=True, capture_output=True, check=True)
            make = subprocess.run(['make', '-s', '--no-print-directory', '-C', str(FRAMEWORK), 'python-info'],
                                  env=env, text=True, capture_output=True, check=True)
            self.assertEqual(shell.stdout.strip(), str(selected))
            self.assertEqual(make.stdout.splitlines()[0], str(selected))

    def test_compatible_default_python_is_preferred_over_versioned_names(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            selected = directory / 'python3'
            selected.symlink_to(sys.executable)
            (directory / 'python3.12').symlink_to(sys.executable)
            result = subprocess.run([shutil.which('sh'), str(FRAMEWORK / 'tools/find-python.sh')],
                                    env=dict(os.environ, PATH=temporary),
                                    text=True, capture_output=True, check=True)
            self.assertEqual(result.stdout.strip(), str(selected))

    def test_python_config_commands_are_not_treated_as_interpreters(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            config = directory / 'python3.12-config'
            config.write_text('#!' + sys.executable + '\nraise SystemExit(0)\n')
            config.chmod(0o755)
            result = subprocess.run([shutil.which('sh'), str(FRAMEWORK / 'tools/find-python.sh')],
                                    env=dict(os.environ, PATH=temporary), text=True, capture_output=True)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn('Python 3.10+ is required', result.stderr)
