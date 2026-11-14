from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from decimal import Decimal as D
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

import outside_api_budget as b
from claude_usage_boxes import RATE_LADDER


def policy(hours='26'):
    return D(hours), D(1), [(m, tuple(D(str(x)) for x in levels), D(str(stop)))
                            for m, levels, stop in RATE_LADDER]


class Budget(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name)
        self.db = b.db_open(self.directory)
        self.addCleanup(self.db.close)
        b.initialize(self.db)
        self.now = 1773370400

    def reserve(self, identifier, amount, provider='cerebras', model=None):
        return b.reserve(self.db, {'id': identifier, 'provider': provider, 'upper_usd': amount},
                         self.now, model or policy())

    def test_reserve_settle_replaces_pending_not_additive_and_duplicate_denied(self):
        self.assertTrue(self.reserve('call-1', '.10')['allowed'])
        self.assertFalse(self.reserve('call-1', '.10')['allowed'])
        with closing(b.db_open(self.directory)) as reopened:
            row = b.snapshot(reopened, self.now, policy())
            self.assertEqual(D(row['committed_usd']), D('.10'))
            self.assertEqual(D(row['pending_usd']), D('.10'))
        value = b.settle(self.db, {'id': 'call-1', 'actual_usd': '.03'}, self.now + 1, policy())
        self.assertEqual(D(value['committed_usd']), D('.03'))
        self.assertEqual(D(value['pending_usd']), D(0))
        b.settle(self.db, {'id': 'call-1', 'actual_usd': '.03'}, self.now + 2, policy())
        with self.assertRaises(ValueError):
            b.settle(self.db, {'id': 'call-1', 'actual_usd': '.04'}, self.now + 3, policy())

    def test_atomic_shared_cap_concurrent_calls_and_spare_disabled(self):
        self.assertFalse(self.reserve('j1', '.01', 'spare')['allowed'])
        def call(identifier):
            with closing(b.db_open(self.directory)) as db:
                return b.reserve(db, {'id': identifier, 'provider': 'cerebras', 'upper_usd': '30'},
                                 self.now, policy('.001'))['allowed']
        with ThreadPoolExecutor(max_workers=2) as pool:
            values = list(pool.map(call, ['parallel1', 'parallel2']))
        self.assertEqual(sum(values), 1)
        self.assertEqual(D(b.snapshot(self.db, self.now, policy())['committed_usd']), D(30))

    def test_unset_corrupt_inputs_no_reservation_and_no_provider_fetch(self):
        with self.db:
            self.db.execute('DELETE FROM settings')
        self.assertEqual(self.reserve('no-cap', '.01')['reason'], 'cap-unset')
        for amount in ['NaN', '-1', '0', 'Infinity', True]:
            with self.assertRaises((ValueError, ArithmeticError)):
                self.reserve('bad', amount)
        self.assertEqual(self.db.execute('SELECT count(*) FROM calls').fetchone()[0], 0)

    def test_overshoot_settlement_latches_and_human_credit_never_erases_spend(self):
        self.reserve('overrun', '.01')
        value = b.settle(self.db, {'id': 'overrun', 'actual_usd': '41'}, self.now + 1, policy())
        self.assertTrue(value['blocked'])
        value = b.human_release(self.db, '1', self.now + 2, policy())
        self.assertTrue(value['blocked'])
        self.assertEqual(D(value['committed_usd']), D(41))
        self.assertFalse(self.reserve('blocked', '.01')['allowed'])
        value = b.human_release(self.db, None, self.now + 3, policy())
        self.assertFalse(value['blocked'])
        self.assertEqual(D(value['committed_usd']), D(41))
        self.assertTrue(b.reserve(self.db, {'id': 'after-release', 'provider': 'cerebras', 'upper_usd': '.01'},
                                  self.now + 4, policy())['allowed'])

    def test_existing_ladder_exact_threshold_and_1_52x_format(self):
        # 15min rate has1.5,1.75,1.875 warnings and2x stop, unchanged.
        self.assertEqual(policy()[2][1], (15, (D('1.5'), D('1.75'), D('1.875')), D('2.0')))
        #60min63% emits no warning; choose cap/rate40/1h and no shorter window
        #usage by putting the cost40minutes in the past.
        self.reserve('old', '1', model=policy('.001'))
        with self.db:
            b.put(self.db, 'cap', '200')
            self.db.execute('UPDATE calls SET actual_usd=?,settled_at=?,at=?', ('25.2', self.now - 2400, self.now - 2401))
            self.db.execute('DELETE FROM notices'); self.db.execute('DELETE FROM warnings')
            b.evaluate(self.db, self.now, policy('5'))
        self.assertEqual(self.db.execute('SELECT count(*) FROM notices').fetchone()[0], 0)
        #15-minute window1.52x; place settled cost7min ago so5min rule is empty.
        with self.db:
            self.db.execute('UPDATE calls SET actual_usd=?,settled_at=?,at=?', ('15.2', self.now - 420, self.now - 421))
            b.evaluate(self.db, self.now, policy('5'))
        bodies = '\n'.join(x[0] for x in self.db.execute('SELECT body FROM notices'))
        self.assertIn('152 target %', bodies)
        self.assertNotIn('% over', bodies)

    def test_notice_dedup_and_unknown_call_reservation_survives_restart(self):
        self.reserve('unknown', '.30')
        #Unknown never settles. Reopening retains full worst-case charge.
        with closing(b.db_open(self.directory)) as db:
            self.assertEqual(D(b.snapshot(db, self.now + 3601, policy())['pending_usd']), D('.30'))
            with db:
                b.evaluate(db, self.now, policy())
            before = db.execute('SELECT count(*) FROM notices').fetchone()[0]
            with db:
                b.evaluate(db, self.now, policy())
            self.assertEqual(db.execute('SELECT count(*) FROM notices').fetchone()[0], before)
            runner = Mock(return_value=Mock(returncode=0, stdout='queued\n'))
            b.flush(db, runner); b.flush(db, runner)
            self.assertEqual(runner.call_count, before)

    def test_recovery_only_when_lowest_warning_clears(self):
        self.reserve('recover', '.01', model=policy('.001'))
        denominator = D(40) / 26 / 12
        for multiple, expected in [('2.7', 0), ('2.2', 0), ('1.9', 1)]:
            with self.db:
                self.db.execute('UPDATE calls SET actual_usd=?,settled_at=?',
                                (str(denominator * D(multiple)), self.now))
                self.db.execute('DELETE FROM notices')
                b.evaluate(self.db, self.now, policy())
            notes = [r[0] for r in self.db.execute('SELECT body FROM notices')
                     if '5-minute rate back below warning' in r[0]]
            self.assertEqual(len(notes), expected)
        # Total 90% -> 80% must stay open; 80% -> 60% closes WARNING,
        # even though the legitimate 50% budget NOTE remains active.
        for used, expected in [('37', 0), ('32', 0), ('24', 1)]:
            with self.db:
                self.db.execute('UPDATE calls SET actual_usd=?,settled_at=?',
                                (used, self.now - 7200))
                self.db.execute('DELETE FROM notices')
                b.evaluate(self.db, self.now, policy())
            notes = [r[0] for r in self.db.execute('SELECT body FROM notices')
                     if 'Total cap back below warning' in r[0]]
            self.assertEqual(len(notes), expected)

    def test_noninteractive_release_and_set_cap_refuse_before_open(self):
        for action in ['uncap', 'set-cap']:
            with patch('sys.argv', ['outside-api-budget', action, '40']), patch('sys.stdin', io.StringIO()), \
                    patch('sys.stdout', io.StringIO()), patch.object(b, 'db_open') as opened:
                self.assertEqual(b.main(), 2)
                opened.assert_not_called()

    def test_exact_cap_reservation_then_lower_settle_does_not_latch_unused_cost(self):
        self.assertTrue(self.reserve('exact-cap', '40', model=policy('.001'))['allowed'])
        self.assertFalse(b.snapshot(self.db, self.now, policy('.001'))['blocked'])
        self.assertFalse(self.reserve('while-pending', '.01', model=policy('.001'))['allowed'])
        value = b.settle(self.db, {'id': 'exact-cap', 'actual_usd': '1'}, self.now + 1, policy('.001'))
        self.assertFalse(value['blocked'])
        self.assertTrue(self.reserve('after-settle', '.01', model=policy('.001'))['allowed'])

    def test_cached_line_reports_each_provider(self):
        self.reserve('line', '.01')
        with patch.object(b, 'STATE', self.directory), patch.object(b, 'rate_policy', return_value=policy()):
            line = b.cached_line()
        self.assertIn('Cerebras $', line)
        self.assertIn('Spare $0.0000', line)

    def test_timed_credit_skipped_when_not_blocked(self):
        output = io.StringIO(); output.isatty = lambda: True
        input_stream = io.StringIO(); input_stream.isatty = lambda: True
        with patch.object(b, 'STATE', self.directory), patch.object(b, 'rate_policy', return_value=policy()), \
                patch.object(b.time, 'time', return_value=self.now + 2), patch.object(b, 'flush'):
            self.assertEqual(b.release_all('1', input_stream, output, only_if_blocked=True), 0)
        self.assertIn('not capped; skipped, no credit added', output.getvalue())
        value = b.human_release(self.db, '1', self.now + 3, policy(), only_if_blocked=True)
        self.assertTrue(value.get('skipped'))
        self.assertEqual(D(value['credit_added_usd']), D(0))

    def test_soft_credit_terminal_reports_shortfall(self):
        self.reserve('overrun', '.01')
        b.settle(self.db, {'id': 'overrun', 'actual_usd': '41'}, self.now + 1, policy())
        output = io.StringIO(); output.isatty = lambda: True
        input_stream = io.StringIO(); input_stream.isatty = lambda: True
        with patch.object(b, 'STATE', self.directory), patch.object(b, 'rate_policy', return_value=policy()), \
                patch.object(b.time, 'time', return_value=self.now + 2), patch.object(b, 'flush'):
            self.assertEqual(b.release_all('1', input_stream, output), 1)
        self.assertIn('short', output.getvalue())
        self.assertIn('$40.', output.getvalue())


if __name__ == '__main__':
    unittest.main()
