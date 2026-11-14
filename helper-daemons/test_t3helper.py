"""Offline tests with a fake t3 script: no real T3 thread is ever messaged.
Run: python3 test_t3helper.py
"""
import importlib
import io
import json
import os
from contextlib import redirect_stdout
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest

HERE = Path(__file__).resolve().parent
sys.dont_write_bytecode = True


class HelperTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(lambda: subprocess.run(['rm', '-rf', str(self.tmp)]))
        self.log = self.tmp / 'calls.jsonl'
        self.t3 = self.tmp / 't3'
        # Fake t3: records every call; FAKE_AUTO_FAIL makes --mode auto fail; FAKE_ALL_FAIL fails sends.
        self.t3.write_text(
            '#!/usr/bin/python3\nimport json,os,sys\n'
            f'open({str(self.log)!r},"a").write(json.dumps([sys.argv[1:], sys.stdin.read() if "-" in sys.argv else ""])+"\\n")\n'
            'if sys.argv[1:3] == ["thread","list"]:\n'
            '    print(json.dumps([{"threadId":"r-old","title":"Reviewer: x","createdAt":"1"},'
            '{"threadId":"new","title":"You are Builder.","createdAt":"2"},{"threadId":"z","title":"Builderx","createdAt":"3"},'
            '{"threadId":"r-new","title":"Reviewer 2","createdAt":"4"}])); sys.exit(0)\n'
            'if os.environ.get("FAKE_ALL_FAIL") or (os.environ.get("FAKE_AUTO_FAIL") and "auto" in sys.argv):\n'
            '    sys.exit("busy")\n')
        self.t3.chmod(0o700)
        self.project = self.tmp / 'project'
        self.project.mkdir()
        os.environ.update(T3_BIN=str(self.t3), T3_PROJECT=str(self.project),
                          HELPER_STATE=str(self.tmp / 'state'), HELPER_TZ='UTC')
        for k in ('FAKE_AUTO_FAIL', 'FAKE_ALL_FAIL', 'T3_THREADS'):
            os.environ.pop(k, None)
        sys.path.insert(0, str(HERE))
        import t3helper
        self.h = importlib.reload(t3helper)

    def calls(self):
        return [json.loads(l) for l in self.log.read_text().splitlines()] if self.log.exists() else []

    def quiet(self, fn, *a, **k):
        out = io.StringIO()
        with redirect_stdout(out):
            result = fn(*a, **k)
        return result, out.getvalue()

    def test_lookup_one_takes_last_tsv_line_then_the_only_matching_title(self):
        (self.project / 'threads.tsv').write_text('Builder\tt1\n\nBuilder \t t2 \nOther\tt3\n')
        self.assertEqual(self.h.lookup_one('Builder'), 't2')
        self.assertEqual(self.calls(), [])
        self.assertEqual(self.h.lookup_one('Builder2'), None)
        (self.project / 'threads.tsv').unlink()
        self.assertEqual(self.h.lookup_one('Builder'), 'new')  # 'Builderx' is not a match

    def test_lookup_one_refuses_several_title_matches_unless_tsv_picks_one(self):
        with self.assertRaises(self.h.AmbiguousName) as cm:
            self.h.lookup_one('Reviewer')
        self.assertIn('2 threads match', str(cm.exception))
        self.assertIn('r-new, r-old', str(cm.exception))
        (self.project / 'threads.tsv').write_text('Reviewer\tr-old\n')
        self.assertEqual(self.h.lookup_one('Reviewer'), 'r-old')

    def test_search_all_lists_tsv_entry_then_titles_newest_first(self):
        self.assertEqual(self.h.search_all('Reviewer'), ['r-new', 'r-old'])
        self.assertEqual(self.h.search_all('Builder'), ['new'])
        self.assertEqual(self.h.search_all('Nobody'), [])
        (self.project / 'threads.tsv').write_text('Reviewer\tr-old\nNobody\tn1\n')
        self.assertEqual(self.h.search_all('Reviewer'), ['r-old', 'r-new'])
        self.assertEqual(self.h.search_all('Nobody'), ['n1'])
        with self.assertRaises(ValueError):
            self.h.search_all(' ')

    def test_send_to_ambiguous_name_fails_at_once_without_sending(self):
        ok, out = self.quiet(self.h.send, 'Reviewer', 'x', pause=0)
        self.assertFalse(ok)
        self.assertIn('FAILED Reviewer NOT FOUND x AmbiguousName: 2 threads match', out)
        self.assertEqual([c[0][:2] for c in self.calls()], [['thread', 'list']])

    def test_empty_name_never_sends(self):
        (self.project / 'threads.tsv').write_text('\tblank-thread\n')
        for name in ('', '  '):
            ok, out = self.quiet(self.h.send, name, 'x', pause=0)
            self.assertFalse(ok)
            self.assertIn('agent name is empty', out)
        self.assertEqual(self.calls(), [])

    def test_send_steers_and_falls_back_to_queue(self):
        ok, out = self.quiet(self.h.send, 'Builder', 'hello\nthere', pause=0)
        self.assertTrue(ok)
        self.assertIn('SENT Builder new hello there', out)
        self.assertEqual(self.calls()[-1], [['thread', 'send', '--mode', 'auto', 'new', '-'], 'hello\nthere'])
        os.environ['FAKE_AUTO_FAIL'] = '1'
        ok, _ = self.quiet(self.h.send, 'Builder', 'again', pause=0)
        self.assertTrue(ok)
        self.assertEqual([c[0][3] for c in self.calls()[-2:]], ['auto', 'queue'])

    def test_three_failed_tries_then_failed(self):
        os.environ['FAKE_ALL_FAIL'] = '1'
        ok, out = self.quiet(self.h.send, 'Builder', 'x', pause=0)
        self.assertFalse(ok)
        self.assertIn('FAILED Builder new x busy', out)
        self.assertEqual(sum(c[0][:2] == ['thread', 'send'] for c in self.calls()), 6)

    def test_dry_run_sends_and_writes_nothing(self):
        ok, out = self.quiet(self.h.send, 'Builder', 'msg', dry_run=True)
        self.assertTrue(ok)
        self.assertIn('WOULD SEND Builder new msg\nmsg\n', out)
        self.assertFalse(any(c[0][:2] == ['thread', 'send'] for c in self.calls()))
        state = self.h.State('x', dry_run=True)
        state.data['a'] = 1
        state.save()
        self.assertFalse((self.tmp / 'state').exists())

    def test_state_is_atomic_and_unreadable_counts_as_missing(self):
        s = self.h.State('x')
        s.data['n'] = 1
        s.save()
        self.assertEqual(json.loads((self.tmp / 'state/x.json').read_text()), {'n': 1})
        (self.tmp / 'state/x.json').write_text('{broken')
        s2, out = self.quiet(self.h.State, 'x')
        self.assertEqual(s2.data, {})
        self.assertIn('unreadable', out)

    def test_example_daemon_dry_run_and_single_copy(self):
        inbox = self.tmp / 'inbox'
        inbox.mkdir()
        (inbox / 'a.txt').write_text('first')
        script = HERE / 'examples/inbox_relay.py'
        env = dict(os.environ)
        r = subprocess.run([sys.executable, '-B', str(script), 'Builder', str(inbox), '--dry-run'],
                           env=env, capture_output=True, text=True, timeout=30)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn('WOULD SEND Builder new New file a.txt', r.stdout)
        self.assertFalse((self.tmp / 'state').exists())
        # A real copy relays once and holds the lock; a second copy exits 3.
        p = subprocess.Popen([sys.executable, '-B', str(script), 'Builder', str(inbox)], env=env,
                             stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        try:
            for _ in range(50):
                if (self.tmp / 'state/inbox_relay.json').exists():
                    break
                time.sleep(0.1)
            self.assertEqual(json.loads((self.tmp / 'state/inbox_relay.json').read_text()), {'relayed': ['a.txt']})
            r2 = subprocess.run([sys.executable, '-B', str(script), 'Builder', str(inbox)], env=env,
                                capture_output=True, text=True, timeout=30)
            self.assertEqual((r2.returncode, r2.stdout.strip()), (3, 'already running'))
        finally:
            p.terminate()
            p.communicate(timeout=10)
        self.assertEqual(p.returncode, 0)

    def test_run_forever_stops_on_exit_3(self):
        log = self.tmp / 'loop.log'
        r = subprocess.run([str(HERE / 'run-forever.sh'), str(log), '--', 'sh', '-c', 'echo hi; exit 3'],
                           capture_output=True, text=True, timeout=30)
        self.assertEqual(r.returncode, 0)
        self.assertIn('hi', log.read_text())
        self.assertIn('loop stops', log.read_text())


if __name__ == '__main__':
    unittest.main()
