"""Exercise generated completion in Bash, without executing application commands."""
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest


OSE_ROOT = Path(__file__).resolve().parents[2]
FRAMEWORK = OSE_ROOT.parent


@unittest.skipUnless(shutil.which('bash'), 'requires Bash')
class CompletionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.environment = dict(os.environ, PYTHONPATH=str(OSE_ROOT / 'src'))
        result = subprocess.run([sys.executable, '-m', 'ose.cli', '--completion', 'bash'],
                                env=cls.environment, capture_output=True, text=True, check=True)
        cls.script = result.stdout
        subprocess.run(['bash', '-n'], input=cls.script, text=True, check=True)

    def complete(self, *words):
        script = self.script + '''
set -eu
COMP_WORDS=(ose "$@")
COMP_CWORD=${#COMP_WORDS[@]}
COMP_CWORD=$((COMP_CWORD - 1))
_ose_complete
printf '%s\\n' "${COMPREPLY[@]}"
'''
        result = subprocess.run(['bash', '--noprofile', '--norc', '-c', script, 'test', *words],
                                env=self.environment, capture_output=True, text=True, check=True)
        return result.stdout.split()

    def test_commands_options_and_nested_parameter_actions(self):
        for words, expected in [
            (('',), '--completion'),
            (('w',), 'ws'),
            (('ed',), 'editor'),
            (('ws', ''), 'setup'),
            (('ws', ''), 'init'),
            (('ws', 'check', '--co'), '--config'),
            (('ws', '--config', 'check', '--wo'), '--working-directory'),
            (('editor', ''), 'setup'),
            (('editor', '--de'), '--deployment'),
            (('editor', '--source-root', 'setup', '--ch'), '--check'),
            (('pa',), 'parameter'),
            (('parameter', ''), 'sync'),
            (('parameter', 'g'), 'get'),
            (('parameter', 'get', '--ex'), '--export'),
            (('parameter', 'sync', '--de'), '--debounce'),
            (('run', '--du'), '--duration'),
            (('subsystem', ''), 'test'),
            (('subsystem', 'test', '--integration-'), '--integration-profile'),
            (('app', ''), 'run'),
            (('app', '--root', 'run', 'parameter', ''), 'get'),
            (('app', 'parameter', 'get', '--ex'), '--export'),
            (('app', 'record', ''), 'inspect'),
            (('app', 'record', 'inspect', '--fo'), '--format'),
            (('record', '--label', 'run', '--ca'), '--capacity'),
            (('parameter', 'get', '--client', 'sync', '--ex'), '--export'),
            (('parameter', 'get', '--client=sync', '--ex'), '--export'),
        ]:
            with self.subTest(words=words):
                self.assertIn(expected, self.complete(*words))

    def test_option_choices_and_equals_word_breaks(self):
        for words, expected in [
            (('run', '--mode', ''), ['realtime', 'simulated']),
            (('run', '--mode', 'si'), ['simulated']),
            (('run', '--mode=si'), ['--mode=simulated']),
            (('run', '--mode', '=', 'si'), ['simulated']),
            (('run', '--mode', '='), ['realtime', 'simulated']),
            (('run', '--mode', '=', 'simulated', '--du'), ['--duration']),
            (('parameter', 'sync', '--initial', 'a'), ['app']),
            (('top', '--format', 'j'), ['jsonl']),
            (('--completion', 'b'), ['bash']),
            (('--completion=b',), ['--completion=bash']),
            (('--completion', '=', 'b'), ['bash']),
            (('app', 'parameter', 'sync', '--initial', 'a'), ['app']),
            (('app', 'record', 'inspect', '--format', 'j'), ['json']),
        ]:
            with self.subTest(words=words):
                self.assertEqual(self.complete(*words), expected)

    def test_file_values_and_end_of_options_leave_bash_fallback(self):
        self.assertEqual(self.complete('build', '--model', ''), [])
        self.assertEqual(self.complete('ws', '--config', ''), [])
        self.assertEqual(self.complete('editor', '--source-root', ''), [])
        self.assertEqual(self.complete('replay', 'capture'), [])
        self.assertEqual(self.complete('parameter', 'get', '--', '--ex'), [])
        self.assertIn('complete -o bashdefault -o default', self.script)

    def test_global_option_exits_before_command_validation_and_replaces_old_command(self):
        for arguments in (['--completion', 'bash'], ['--completion=bash'], ['--completion', 'bash', 'validate']):
            with self.subTest(arguments=arguments):
                result = subprocess.run([sys.executable, '-m', 'ose.cli', *arguments],
                                        env=self.environment, capture_output=True, text=True)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(result.stdout, self.script)
        for arguments in ([], ['completion', 'bash'], ['--completion'], ['--completion', 'zsh']):
            with self.subTest(arguments=arguments):
                result = subprocess.run([sys.executable, '-m', 'ose.cli', *arguments],
                                        env=self.environment, capture_output=True, text=True)
                self.assertEqual(result.returncode, 2)
                self.assertEqual(result.stdout, '')
        self.assertNotIn('completion', self.complete(''))

    @unittest.skipUnless((FRAMEWORK / 'setenv.sh').is_file(), 'requires framework checkout')
    def test_setenv_activates_completion_for_alias_and_can_be_sourced_again(self):
        with tempfile.TemporaryDirectory() as temporary:
            env = dict(self.environment, FRAMEWORK_ENV=str(FRAMEWORK / 'setenv.sh'),
                       PATH=str(Path(sys.executable).parent) + os.pathsep + os.environ['PATH'], OSE_APP_ROOT=temporary,
                       OSE_PARAMETER_TOKEN='test-only-explicit-token', PS1='', PS2='')
            script = '''
set -eu
source "$FRAMEWORK_ENV" >/dev/null
source "$FRAMEWORK_ENV" >/dev/null
complete -p ose
alias ose
# Completion uses the generated script even with Python unavailable afterwards.
PATH=/missing/commands
PYTHON=/missing/interpreter
COMP_WORDS=(ose parameter get --ex)
COMP_CWORD=3
_ose_complete
printf '%s\\n' "${COMPREPLY[@]}"
'''
            result = subprocess.run(['bash', '--noprofile', '--norc', '-i', '-c', script],
                                    env=env, capture_output=True, text=True, check=True)
            self.assertIn('-F _ose_complete ose', result.stdout)
            self.assertIn("alias ose='_ose_env_cli'", result.stdout)
            self.assertEqual(result.stdout.splitlines()[-2:], ['--exact', '--export'])
