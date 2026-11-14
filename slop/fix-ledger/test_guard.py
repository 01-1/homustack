import json
import tempfile
import unittest
import runpy
from pathlib import Path
from unittest.mock import patch

from guard import reconcile, completion


class GuardTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / 'repo'
        self.state = Path(self.temp.name) / 'state'
        (self.root / 'docs/archive').mkdir(parents=True)
        self.lp = self.root / 'docs/fix-ledger.md'
        self.row = '- OPEN [fix:a] owner: Ops | kind: process | next: install'
        self.lp.write_text(self.row + '\n')
        for priority in ('hi', 'med', 'lo'):
            (self.root / f'TODO-{priority}.md').write_text('# TODO\n')

    def run_guard(self):
        return reconcile(self.root, self.state, True)

    def test_double_deletion_recovers_ledger_and_todo(self):
        self.assertFalse(self.run_guard()[0])
        self.lp.unlink()
        (self.root / 'TODO-hi.md').unlink()
        errors, fixed = self.run_guard()
        self.assertFalse(errors)
        self.assertIn(self.row, self.lp.read_text())
        self.assertIn('[fix:a]', (self.root / 'TODO-hi.md').read_text())
        self.assertEqual(len(fixed), 2)
        self.assertEqual(self.run_guard(), ([], []))

    def test_text_proof_does_not_close_process_fix(self):
        self.lp.write_text(self.row.replace('OPEN', 'CLOSED') + ' | proof: triaged\n')
        errors, fixed = self.run_guard()
        self.assertIn('owning agent must establish completion', errors[0])
        self.assertTrue(fixed)
        self.assertIn('- OPEN ', self.lp.read_text())

    def test_checked_todo_does_not_hide_open_ledger(self):
        (self.root / 'TODO-hi.md').write_text('- [x] **[fix:a]** triaged\n')
        self.run_guard()
        self.assertIn('- [ ] **[fix:a]**', (self.root / 'TODO-hi.md').read_text())

    def test_unregistered_todo_survives_deletion(self):
        p = self.root / 'TODO-med.md'
        line = '- [ ] **Future process (Owner).** The user asked for it.'
        p.write_text(line + '\n')
        self.run_guard()
        p.write_text('# cleanup\n')
        self.run_guard()
        self.assertIn(line, p.read_text())

    def test_archived_completion_and_priority_move_are_preserved(self):
        p = self.root / 'TODO-med.md'
        line = '- [ ] **Future process (Owner).** The user asked for it.'
        p.write_text(line + '\n')
        self.run_guard()
        p.write_text('')
        (self.root / 'TODO-lo.md').write_text(line + '\n')
        self.assertEqual(self.run_guard(), ([], []))
        (self.root / 'TODO-lo.md').write_text('')
        (self.root / 'docs/archive/todo-done-log-2026-11-10.md').write_text(line.replace('[ ]', '[x]') + ' Evidence: actual result\n')
        self.assertEqual(self.run_guard(), ([], []))

    def test_no_owner_is_not_invented(self):
        self.lp.write_text('- OPEN [fix:a] owner: | next: assign\n')
        errors, fixed = self.run_guard()
        self.assertIn('no owner', errors[0])
        self.assertTrue(fixed)

    def test_distinct_same_title_requests_both_survive(self):
        hi, med = self.root / 'TODO-hi.md', self.root / 'TODO-med.md'
        hi.write_text('- [ ] **Fi trial.** Read expiry and card status.\n')
        med.write_text('- [ ] **Fi trial.** Cancel after finals.\n')
        self.run_guard()
        hi.write_text('')
        self.run_guard()
        self.assertIn('Read expiry', hi.read_text())
        self.assertIn('Cancel after', med.read_text())
        self.assertNotIn('Read expiry', med.read_text())
        self.assertEqual(self.run_guard(), ([], []))

    def test_retitle_keeps_latest_wording_and_recovers_real_drop(self):
        p = self.root / 'TODO-med.md'
        old = '- [ ] **Old task title (Owner).** Identical owner and details.'
        new = old.replace('Old task title', 'New task title')
        p.write_text(old + '\n')
        self.run_guard()
        p.write_text(new + '\n')
        self.assertEqual(self.run_guard(), ([], []))
        self.assertNotIn(old, p.read_text())
        p.write_text('')
        self.run_guard()
        self.assertIn(new, p.read_text())
        self.assertNotIn(old, p.read_text())

    def test_explicit_supersession_changes_details_and_survives_successor_drop(self):
        p = self.root / 'TODO-med.md'
        old = '- [ ] **Old title (Owner).** Old details.'
        interim = '- [ ] **Interim title (Owner).** Revised details. <!-- supersedes: todo:old title -->'
        new = '- [ ] **Final title (Owner).** Final details. <!-- supersedes: todo:interim title -->'
        p.write_text(old + '\n')
        self.run_guard()
        p.write_text(interim + '\n')
        self.assertEqual(self.run_guard(), ([], []))
        p.write_text(new + '\n')
        self.assertEqual(self.run_guard(), ([], []))
        p.write_text('')
        self.run_guard()
        self.assertEqual(p.read_text().strip(), new)
        self.assertEqual(self.run_guard(), ([], []))

    def test_existing_other_request_cannot_hide_drop(self):
        p = self.root / 'TODO-med.md'
        old = '- [ ] **Old task (Owner).** Same details.'
        other = '- [ ] **Other task (Owner).** Same details.'
        p.write_text(old + '\n' + other + '\n')
        self.run_guard()
        p.write_text(other + '\n')
        self.run_guard()
        self.assertIn(old, p.read_text())

    def test_pure_retitle_back_to_former_title_keeps_latest_identity(self):
        p = self.root / 'TODO-med.md'
        old = '- [ ] **Original title (Owner).** Old details.'
        revised = '- [ ] **Revised title (Owner).** New details. <!-- supersedes: todo:original title -->'
        back = revised.replace('**Revised title', '**Original title')
        p.write_text(old + '\n')
        self.run_guard()
        p.write_text(revised + '\n')
        self.assertEqual(self.run_guard(), ([], []))
        p.write_text(back + '\n')
        self.assertEqual(self.run_guard(), ([], []))
        self.assertEqual(p.read_text().strip(), back)
        p.write_text('')
        self.run_guard()
        self.assertEqual(p.read_text().strip(), back)

    def test_body_match_without_same_owner_does_not_supersede(self):
        p = self.root / 'TODO-med.md'
        old = '- [ ] **Old task (Owner).** Same details.'
        other = '- [ ] **Other task (Owner2).** Same details.'
        p.write_text(old + '\n')
        self.run_guard()
        p.write_text(other + '\n')
        self.run_guard()
        self.assertIn(old, p.read_text())

    def test_invalid_supersedes_does_not_disable_restore(self):
        p = self.root / 'TODO-med.md'
        old = '- [ ] **Old task (Owner).** Old details.'
        p.write_text(old + '\n')
        self.run_guard()
        p.write_text('- [ ] **New task (Owner).** Different. <!-- supersedes: todo:typo -->\n')
        errors, fixed = self.run_guard()
        self.assertIn('unknown/ambiguous', errors[0])
        self.assertIn(old, p.read_text())

    def test_receipt_checks_live_trigger_and_changed_artifact(self):
        import hashlib
        import subprocess
        artifact = self.root / 'trigger.py'
        artifact.write_text('deployed')
        receipt = dict(key='fix:a', automatic=True, passed=True,
                       observed_at='2026-11-11T10:00:00-05:00', trigger='example.path',
                       event='file changed', expected='repaired', actual='repaired',
                       repair='automatic', agent_fallback='owner received case',
                       artifacts={'trigger.py': hashlib.sha256(artifact.read_bytes()).hexdigest()})
        (self.root / 'receipt.json').write_text(json.dumps(receipt))
        rest = ' owner: Ops | proof: result | receipt: receipt.json'
        def systemctl(args, **kwargs):
            return subprocess.CompletedProcess(args, 0, 'enabled\n' if args[2] == 'is-enabled' else 'active\n')
        with patch('guard.subprocess.run', side_effect=systemctl):
            self.assertIsNone(completion(self.root, 'fix:a', rest))
            artifact.write_text('different')
            self.assertIn('changed artifact', completion(self.root, 'fix:a', rest))

    def test_transient_repair_handoff_retries_after_delivery_failure(self):
        import subprocess
        run = runpy.run_path(str(Path(__file__).with_name('fix-ledger-run')))['run']
        results = iter([
            subprocess.CompletedProcess([], 1, 'FIXED reopened\nUNFIXED completion still unproved\n', ''),
            subprocess.CompletedProcess([], 0, 'fix-ledger OK\n', ''),
            subprocess.CompletedProcess([], 0, 'fix-ledger OK\n', '')])
        attempts = []
        def sender(message):
            attempts.append(message)
            if len(attempts) == 1:
                raise OSError('offline')
            return True
        checker = lambda *args, **kwargs: next(results)
        self.assertEqual(run(self.root, self.state, sender, checker), 1)
        self.assertEqual(run(self.root, self.state, sender, checker), 0)
        self.assertEqual(len(attempts), 2)
        self.assertEqual(attempts[0], attempts[1])
        self.assertEqual(run(self.root, self.state, sender, checker), 0)
        self.assertEqual(len(attempts), 2)

    def test_dry_run_writes_nothing(self):
        import contextlib, io
        run = runpy.run_path(str(Path(__file__).with_name('fix-ledger-run')))['run']
        self.lp.write_text('- OPEN [fix:b] owner: | next: assign\n')
        before = {p: p.read_text() for p in self.root.rglob('*.md')}
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            self.assertEqual(run(self.root, self.state, dry_run=True), 0)
        self.assertIn('WOULD HAND OFF', out.getvalue())
        self.assertIn('fix:b: no owner', out.getvalue())
        self.assertFalse(self.state.exists())
        self.assertEqual(before, {p: p.read_text() for p in self.root.rglob('*.md')})

    def test_notifier_gets_message_on_stdin(self):
        import sys
        notifier = runpy.run_path(str(Path(__file__).with_name('fix-ledger-run')))['notifier']
        out = self.root / 'got.txt'
        stub = self.root / 'stub.py'
        stub.write_text(f"import sys; open({str(out)!r}, 'w').write(sys.stdin.read())\n")
        (self.root / '.fix-ledger.json').write_text(json.dumps({'notify': {'argv': [sys.executable, str(stub)]}}))
        self.assertTrue(notifier(self.root)('hello'))
        self.assertEqual(out.read_text(), 'hello')

    def test_layout_from_repo_config(self):
        (self.root / '.fix-ledger.json').write_text(json.dumps(
            {'ledger': 'LEDGER.md', 'todos': {'now': 'TODO.md'}, 'key_prefix': 'req'}))
        (self.root / 'LEDGER.md').write_text('- OPEN [req:x] owner: Ops | next: do it\n')
        errors, fixed = self.run_guard()
        self.assertEqual(errors, [])
        self.assertIn('[req:x]', (self.root / 'TODO.md').read_text())
        self.assertIn('see LEDGER.md', (self.root / 'TODO.md').read_text())

    def test_install_quotes_paths_and_keeps_other_repos_units(self):
        import os, subprocess
        home, units = Path(self.temp.name) / 'home', Path(self.temp.name) / 'units'
        home.mkdir()
        other = Path(self.temp.name) / 'elsewhere' / 'repo'
        other.mkdir(parents=True)
        spaced = Path(self.temp.name) / 'my repo'
        spaced.mkdir()
        script = str(Path(__file__).with_name('install.sh'))
        env = {**os.environ, 'HOME': str(home)}  # --dest outside the default also means no systemctl
        def install(repo):
            return subprocess.run(['bash', script, str(repo), '--dest', str(units)], env=env,
                                  capture_output=True, text=True, timeout=60)
        self.assertEqual(install(self.root).returncode, 0)
        self.assertEqual(install(self.root).returncode, 0)  # reinstalling the same repo is fine
        r = install(other)  # same folder name 'repo', different repository
        self.assertEqual(r.returncode, 2)
        self.assertIn('another repository', r.stderr)
        self.assertIn(f'# fix-ledger repo: {self.root.resolve()}', (units / 'fix-ledger-repo.service').read_text())
        self.assertEqual(install(spaced).returncode, 0)
        self.assertIn(f'"{spaced.resolve()}"', (units / 'fix-ledger-my_repo.service').read_text())


if __name__ == '__main__':
    unittest.main()
