"""Single-file offline runner checks; no native/provider/service calls."""
from copy import deepcopy
from datetime import datetime, timezone
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import pace

NOW = 1762925400.0
PROJECT, ENVIRONMENT = 'project-1', 'environment-1'
SCOPE = {'projectId': PROJECT, 'environmentId': ENVIRONMENT}


def inspect(data, now, exclusions):
    return pace.inspect_snapshot(data, now, exclusions, PROJECT, ENVIRONMENT)


def iso(seconds):
    return datetime.fromtimestamp(seconds, timezone.utc).isoformat().replace('+00:00', 'Z')


def row(**changes):
    data = dict(threadId='target', parentThreadId=None, relationshipToParent=None,
                activeRunId='run-one', status='running', providerInstanceId='codex',
                continuousWorkingSince=iso(NOW - 301), eligible=True, reason=None,
                automationHelper=False, settled=False, archived=False,
                blockedOnThreadWait=False, pendingRequestCount=0)
    return {**data, **changes}


def snapshot(rows=None, **changes):
    return {**dict(environmentId=ENVIRONMENT, projectId=PROJECT,
                   observedAt=iso(NOW), threads=rows if rows is not None else [row()]), **changes}


class Fake(pace.Runner):
    def __init__(self, home):
        super().__init__(home)
        self.state.mkdir(parents=True)
        self.calls = []
        self.snapshot = snapshot()
        self.admission = (0, dict(schema=1, allowed=True, status='clear', reasons=[], measured_at=iso(NOW)))
        self.result = (0, dict(taskId='task', childThreadId='child', childRunId='child-run', replayed=False))
        self.gates = 0

    def local_gates(self):
        self.gates += 1

    def json_command(self, argv):
        argv = [str(arg) for arg in argv]
        self.calls.append(argv)
        if 'activity' in argv:
            return 0, deepcopy(self.snapshot)
        if 'assist' in argv:
            return self.result
        return self.admission


class PaceTests(unittest.TestCase):
    def test_strict_boundary_and_same_snapshot_time(self):
        data = snapshot([row(continuousWorkingSince=iso(NOW - 300))])
        self.assertEqual(inspect(data, NOW + 1, set()), [])
        data['threads'][0]['continuousWorkingSince'] = iso(NOW - 300.001)
        self.assertEqual(len(inspect(data, NOW, set())), 1)

    def test_scope_freshness_duplicates_and_unknown_gates_deny(self):
        cases = [snapshot(projectId='private'), snapshot(environmentId='cloud'),
                 snapshot(observedAt=iso(NOW - 16)), snapshot(observedAt=iso(NOW + 1)),
                 snapshot([row(), row()]), snapshot([row(automationHelper=None)]),
                 snapshot([row(pendingRequestCount=True)]), snapshot([row(reason='new_unknown_gate')])]
        for data in cases:
            with self.subTest(data=data), self.assertRaises(pace.Denied):
                inspect(data, NOW, set())

    def test_ordinary_subagent_eligible_but_helpers_passive_and_waits_are_not(self):
        child = row(parentThreadId='parent', relationshipToParent='subagent')
        self.assertEqual(len(inspect(snapshot([child]), NOW, set())), 1)
        for changes in [dict(automationHelper=True), dict(settled=True), dict(archived=True),
                        dict(blockedOnThreadWait=True), dict(pendingRequestCount=1),
                        dict(status='waiting'), dict(reason='under_threshold'), dict(eligible=False)]:
            self.assertEqual(inspect(snapshot([row(**changes)]), NOW, set()), [])
        self.assertEqual(inspect(snapshot(), NOW, {'target'}), [])
        with self.assertRaises(pace.Denied):
            inspect(snapshot([row(continuousWorkingSince=None)]), NOW, set())

    def test_working_codex_count_includes_helpers_and_refuses_above_four(self):
        rows = [row(threadId=str(index), automationHelper=index > 0) for index in range(4)]
        self.assertEqual(len(inspect(snapshot(rows), NOW, set())), 1)
        rows.append(row(threadId='fifth'))
        with self.assertRaisesRegex(pace.Denied, 'concurrency'):
            inspect(snapshot(rows), NOW, set())

    def test_admission_unknown_stale_inconsistent_or_nonzero_deny(self):
        clear = dict(schema=1, allowed=True, status='clear', reasons=[], measured_at=iso(NOW))
        pace.admission_ok(0, clear, NOW)
        for changes, code in [(dict(status='unknown'), 2), (dict(allowed=False), 0),
                              (dict(schema=True), 0), (dict(reasons=['hold']), 0),
                              (dict(measured_at=iso(NOW - 16)), 0), ({}, 1)]:
            with self.assertRaises(pace.Denied):
                pace.admission_ok(code, {**clear, **changes}, NOW)

    def test_disabled_and_read_only_never_assist(self):
        with tempfile.TemporaryDirectory() as root, patch('pace.time.time', return_value=NOW):
            runner = Fake(Path(root))
            self.assertEqual(runner.tick({'enabled': False, **SCOPE}, True, {})['status'], 'disabled')
            self.assertEqual(runner.calls, [])
            self.assertEqual(runner.tick({'enabled': True, **SCOPE}, False, {})['status'], 'ready_not_launched')
            self.assertFalse(any('assist' in call for call in runner.calls))
            self.assertFalse((runner.state / 'receipts.json').exists())

    def test_usage_denial_stops_before_assist(self):
        with tempfile.TemporaryDirectory() as root, patch('pace.time.time', return_value=NOW):
            runner = Fake(Path(root))
            runner.admission = (2, dict(schema=1, allowed=False, status='unknown', reasons=['coverage']))
            with self.assertRaises(pace.Denied):
                runner.tick({'enabled': True, **SCOPE}, True, {})
            self.assertFalse(any('assist' in call for call in runner.calls))

    def test_native_launch_exact_selection_receipt_and_local_dedup(self):
        with tempfile.TemporaryDirectory() as root, patch('pace.time.time', return_value=NOW):
            runner, seen = Fake(Path(root)), {}
            result = runner.tick({'enabled': True, **SCOPE}, True, seen)
            self.assertEqual(result['status'], 'launched')
            assist = next(call for call in runner.calls if 'assist' in call)
            self.assertEqual(assist[assist.index('--run') + 1], 'run-one')
            self.assertEqual(assist[-7:], ['--provider', 'codex', '--model', 'gpt-6.1-sol', '--option', 'reasoningEffort=low', '--json'])
            self.assertGreaterEqual(runner.gates, 2)
            self.assertEqual(json.loads((runner.state / 'receipts.json').read_text()), seen)
            self.assertEqual((runner.state / 'receipts.json').stat().st_mode & 0o777, 0o600)
            runner.calls.clear()
            self.assertEqual(runner.tick({'enabled': True, **SCOPE}, True, seen)['status'], 'no_eligible_target')
            self.assertFalse(any('assist' in call for call in runner.calls))

    def test_unconfirmed_native_launch_not_saved_and_retry_identity_unchanged(self):
        with tempfile.TemporaryDirectory() as root, patch('pace.time.time', return_value=NOW):
            runner, seen = Fake(Path(root)), {}
            runner.result = (0, {'taskId': 'task'})
            with self.assertRaises(pace.Denied):
                runner.tick({'enabled': True, **SCOPE}, True, seen)
            self.assertEqual(seen, {})
            first = next(call for call in runner.calls if 'assist' in call)
            runner.result = (0, dict(taskId='task', childThreadId='child', childRunId='child-run', replayed=True))
            self.assertEqual(runner.tick({'enabled': True, **SCOPE}, True, seen)['status'], 'replayed')
            self.assertEqual(first, [call for call in runner.calls if 'assist' in call][-1])

    def test_malformed_hold_denies_without_services_or_native_calls(self):
        with tempfile.TemporaryDirectory() as root:
            runner = pace.Runner(Path(root), ['.local/state/t3-load-build/no-restart-until'])
            hold = Path(root) / '.local/state/t3-load-build/no-restart-until'
            hold.parent.mkdir(parents=True)
            hold.write_text('unknown')
            mem = 'MemAvailable: 16777216 kB\n'
            original = Path.read_text
            def read(path, *args, **kwargs):
                return mem if str(path) == '/proc/meminfo' else original(path, *args, **kwargs)
            with patch.object(Path, 'read_text', read), self.assertRaises(pace.Denied):
                runner.local_gates()


    def test_scope_is_required_and_checked(self):
        with tempfile.TemporaryDirectory() as root:
            runner = Fake(Path(root))
            with self.assertRaises(pace.Denied):
                runner.tick({'enabled': True}, False, {})
            with self.assertRaises(pace.Denied):
                runner.tick({'enabled': True, 'projectId': 'other', 'environmentId': ENVIRONMENT}, False, {})
            self.assertTrue(any('--project' in c and 'other' in c for c in runner.calls))
        with self.assertRaises(pace.Denied):
            pace.inspect_snapshot(snapshot(), NOW, set(), '', '')


if __name__ == '__main__':
    unittest.main()
