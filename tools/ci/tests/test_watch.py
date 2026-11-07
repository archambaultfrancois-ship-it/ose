"""Watchdog policy tests; no network, Codex invocation or Git publication."""
import importlib.util
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import Mock, patch

spec = importlib.util.spec_from_file_location('watch', Path(__file__).parents[1] / 'watch.py')
watch = importlib.util.module_from_spec(spec)
spec.loader.exec_module(watch)


def run(identifier=1, sha='head', workflow=1, conclusion='failure', **kwargs):
    return dict(id=identifier, head_sha=sha, workflow_id=workflow, head_branch='main',
                status='completed', conclusion=conclusion, run_attempt=1,
                created_at=f'2026-01-{identifier:02d}', **kwargs)


class PolicyTests(unittest.TestCase):
    def setUp(self):
        self.workflows = [dict(id=1, name='Tests', state='active'),
                          dict(id=2, name='Release', state='disabled_manually')]
        self.state = {'attempts': 0, 'handled': []}

    def test_only_main_active_and_latest_run(self):
        wrong_branch = run(4)
        wrong_branch['head_branch'] = 'topic'
        old_attempt = run(3)
        retry = dict(old_attempt, run_attempt=2, conclusion='success')
        active, latest = watch.latest_runs(self.workflows, [run(1, sha='old'),
            run(2, workflow=2), wrong_branch, run(5, event='pull_request'),
            old_attempt, retry])
        self.assertEqual(active, {1: 'Tests'})
        self.assertEqual(latest, {1: retry})

    def test_path_filtered_failure_from_old_commit_is_actionable(self):
        active, latest = watch.latest_runs(self.workflows, [run(1, sha='older')])
        self.assertIsNotNone(watch.decision(self.state, active, latest, 'new-main', 3)[1])

    def test_new_failure_deduplicated_by_attempt(self):
        runs = {1: run()}
        _, signature = watch.decision(self.state, {1: 'Tests'}, runs, 'head', 3)
        self.assertIsNotNone(signature)
        self.state['handled'].append(signature)
        self.assertIsNone(watch.decision(self.state, {1: 'Tests'}, runs, 'head', 3)[1])
        self.assertIsNone(watch.decision(self.state, {1: 'Tests'}, runs, 'docs-push', 3)[1])
        runs[1]['run_attempt'] = 2
        self.assertIsNotNone(watch.decision(self.state, {1: 'Tests'}, runs, 'head', 3)[1])

    def test_incomplete_workflows_prevent_repair(self):
        running = run(2, workflow=2)
        running['status'] = 'in_progress'
        self.assertIsNone(watch.decision(self.state, {1: 'Tests', 2: 'Release'},
                                        {1: run(), 2: running}, 'head', 3)[1])

    def test_bound_applies_across_new_commits(self):
        self.state['attempts'] = 3
        message, signature = watch.decision(self.state, {1: 'Tests'},
                                            {1: run(2, sha='new')}, 'new', 3)
        self.assertIn('limit', message)
        self.assertIsNone(signature)

    def test_only_complete_green_resets_incident(self):
        self.state['attempts'] = 2
        success = {1: run(conclusion='success')}
        watch.decision(self.state, {1: 'Tests', 3: 'Other'}, success, 'head', 3)
        self.assertEqual(self.state['attempts'], 2)
        watch.decision(self.state, {1: 'Tests'}, success, 'head', 3)
        self.assertEqual(self.state, {'attempts': 0, 'handled': []})

    def test_no_runs_is_not_green(self):
        self.state['attempts'] = 2
        message, signature = watch.decision(self.state, {1: 'Tests'}, {}, 'head', 3)
        self.assertIn('No main workflow run', message)
        self.assertIsNone(signature)
        self.assertEqual(self.state['attempts'], 2)

    def test_cancelled_and_skipped_never_trigger_repair(self):
        for conclusion in ['cancelled', 'skipped', 'neutral', 'success']:
            with self.subTest(conclusion=conclusion):
                self.assertIsNone(watch.decision(self.state, {1: 'Tests'},
                    {1: run(conclusion=conclusion)}, 'head', 3)[1])

    def test_errors_do_not_log_command_output_or_credentials(self):
        result = subprocess.CompletedProcess([], 1, 'credential-value', 'credential-value')
        with patch.object(watch.subprocess, 'run', return_value=result):
            with self.assertRaises(RuntimeError) as caught:
                watch.command(['gh', 'api', 'anything'])
        self.assertNotIn('credential-value', str(caught.exception))

    def test_paginated_response(self):
        with patch.object(watch, 'command', return_value='[{"workflows":[1]},{"workflows":[2]}]'):
            self.assertEqual(watch.api('owner/repo', 'actions/workflows', 'workflows'), [1, 2])

    def test_state_saved_atomically(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'state.json'
            watch.save(path, self.state)
            self.assertEqual(watch.json.loads(path.read_text()), self.state)
            self.assertFalse(path.with_suffix('.tmp').exists())

    def test_subset_of_previously_handled_failures_is_not_new(self):
        self.state['handled'] = ['1/1,2/1']
        self.assertIsNone(watch.decision(self.state, {1: 'Tests'}, {1: run()}, 'head', 3)[1])

    def test_deleted_active_workflow_is_excluded_and_queries_are_bounded(self):
        calls = []
        def api(repo, endpoint, field=None, paginate=True):
            calls.append((endpoint, paginate))
            if endpoint == 'commits/main':
                return {'sha': 'current'}
            if endpoint.startswith('contents/'):
                return [{'path': '.github/workflows/tests.yml'}]
            if endpoint.startswith('actions/workflows?'):
                return [dict(id=1, name='Tests', state='active', path='.github/workflows/tests.yml'),
                        dict(id=2, name='Deleted', state='active', path='.github/workflows/deleted.yml')]
            return [run(1, sha='older')]
        with patch.object(watch, 'api', side_effect=api):
            sha, active, latest = watch.observe('owner/repo')
        self.assertEqual(sha, 'current')
        self.assertEqual(active, {1: 'Tests'})
        self.assertEqual(latest[1]['head_sha'], 'older')
        queries = [c for c in calls if '/runs?' in c[0]]
        self.assertEqual(len(queries), 1)
        self.assertIn('per_page=1', queries[0][0])
        self.assertFalse(queries[0][1])

    def test_codex_requires_report_of_successful_validation(self):
        for commands in [[], ['python -m unittest relevant_suite']]:
            with self.subTest(commands=commands), tempfile.TemporaryDirectory() as directory:
                output = Path(directory) / 'agent.log'
                report = {'status': 'fixed', 'summary': 'Fix verified.', 'tests_passed': commands}
                output.with_suffix('.report.json').write_text(watch.json.dumps(report))
                child = Mock(returncode=0)
                child.poll.return_value = 0
                with patch.object(watch.subprocess, 'Popen', return_value=child) as popen:
                    if commands:
                        self.assertEqual(watch.codex_repair(Path(directory), 'prompt', output), report)
                    else:
                        with self.assertRaisesRegex(RuntimeError, 'tested fix'):
                            watch.codex_repair(Path(directory), 'prompt', output)
                child.communicate.assert_called_once_with('prompt', timeout=1800)
                self.assertTrue(popen.call_args.kwargs['start_new_session'])

    def test_stop_refuses_recycled_pid(self):
        with tempfile.TemporaryDirectory() as directory:
            (Path(directory) / 'watch.lock').write_text('{"pid": 123, "started": "old"}')
            with patch.object(watch.sys, 'argv', ['watch.py', '--stop', '--state-dir', directory]), \
                    patch.object(watch, 'command', side_effect=['/tmp/repo/.git', '']), \
                    patch.object(watch.fcntl, 'flock', side_effect=BlockingIOError), \
                    patch.object(watch, 'process_start', return_value='new'), \
                    patch.object(watch.os, 'kill') as kill, patch.object(watch.sys, 'stderr'):
                with self.assertRaises(SystemExit):
                    watch.main()
                kill.assert_not_called()

    def test_once_observes_but_never_repairs_or_saves_state(self):
        with tempfile.TemporaryDirectory() as directory:
            with patch.object(watch.sys, 'argv', ['watch.py', '--once', '--state-dir', directory]), \
                    patch.object(watch, 'command', side_effect=['/tmp/repo/.git', '', '{"nameWithOwner":"owner/repo"}']), \
                    patch.object(watch, 'observe', return_value=('head', {1: 'Tests'}, {1: run()})), \
                    patch.object(watch, 'repair') as repair, \
                    patch.object(watch.signal, 'signal'), patch('builtins.print'):
                self.assertEqual(watch.main(), 0)
                repair.assert_not_called()
                self.assertFalse((Path(directory) / 'state.json').exists())

    def test_codex_timeout_terminates_process_group(self):
        with tempfile.TemporaryDirectory() as directory:
            child = Mock()
            child.communicate.side_effect = subprocess.TimeoutExpired('codex', 1800)
            child.poll.return_value = None
            with patch.object(watch.subprocess, 'Popen', return_value=child), \
                    patch.object(watch.os, 'killpg') as killpg:
                with self.assertRaises(subprocess.TimeoutExpired):
                    watch.codex_repair(Path(directory), 'prompt', Path(directory) / 'agent.log')
                killpg.assert_called_once_with(child.pid, watch.signal.SIGTERM)
                child.wait.assert_called_once_with(timeout=10)

    def test_publication_is_normal_push_and_reports_commit(self):
        with tempfile.TemporaryDirectory() as directory:
            args = watch.argparse.Namespace(repo=Path(directory))
            calls = []
            committed = False
            def command(arguments, cwd=None):
                nonlocal committed
                calls.append(arguments)
                if arguments[:2] == ['git', 'commit']:
                    committed = True
                if arguments[:3] == ['git', 'rev-parse', 'origin/main']:
                    return 'head'
                if arguments[:3] == ['git', 'rev-parse', 'HEAD']:
                    return 'published' if committed else 'head'
                if arguments[:3] == ['git', 'status', '--porcelain']:
                    return ' M fixed.py'
                return ''
            with patch.object(watch, 'command', side_effect=command), \
                    patch.object(watch, 'codex_repair', return_value={'tests_passed': ['make target']}), \
                    patch.object(watch, 'log'):
                result = watch.repair(args, Path(directory), 'owner/repo', 'head', {}, 1)
            self.assertIn(['git', 'push', 'origin', 'HEAD:main'], calls)
            self.assertEqual(result['sha'], 'published')
            self.assertEqual(result['tests_reported'], ['make target'])

    def test_main_race_preserves_worktree_and_never_pushes(self):
        with tempfile.TemporaryDirectory() as directory:
            args = watch.argparse.Namespace(repo=Path(directory))
            calls = []
            def command(arguments, cwd=None):
                calls.append(arguments)
                if arguments[:3] == ['git', 'rev-parse', 'origin/main']:
                    return 'head' if len(calls) < 8 else 'other'
                if arguments[:3] == ['git', 'rev-parse', 'HEAD']:
                    return 'head'
                if arguments[:3] == ['git', 'status', '--porcelain']:
                    return ' M fixed.py'
                return ''
            with patch.object(watch, 'command', side_effect=command), patch.object(watch, 'codex_repair'):
                with self.assertRaisesRegex(RuntimeError, 'main changed during repair'):
                    watch.repair(args, Path(directory), 'owner/repo', 'head', {}, 1)
            self.assertFalse(any(c[:2] == ['git', 'push'] for c in calls))


if __name__ == '__main__':
    unittest.main()
