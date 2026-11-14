"""Soft credits are negative usage at one moment, not a permanent offset."""
import json
import unittest

import claude_usage_boxes as b
from test_rate_reconcile import Reconcile


class PointCredit(Reconcile):
    def test_window_sum_counts_only_credits_inside_the_window(self):
        ledger = dict(soft_credits=[dict(category='fable', points=2., at=100.),
                                    dict(category='fable', points=3., at=200.),
                                    dict(category='codex', points=7., at=150.)])
        self.assertEqual(b.soft_credit(ledger, 'fable'), 5.)
        self.assertEqual(b.soft_credit(ledger, 'fable', 0, 150), 2.)
        self.assertEqual(b.soft_credit(ledger, 'fable', 150, 300), 3.)
        self.assertEqual(b.soft_credit(ledger, 'fable', 100, 200), 3.)  # (start, end], like usage events
        self.assertEqual(b.soft_credit(ledger, 'fable', 300), 0.)
        self.assertEqual(b.soft_credit(ledger, 'codex', 0, 150), 7.)

    def test_credit_releases_its_stop_then_leaves_each_rate_window_as_it_slides(self):
        self.latch(multiple=4); self.persist()
        result, _ = self.soft(30)
        self.assertEqual(result, 0)
        self.assertFalse(self.host.guard_state(self.host.GUARD_STATE)['latches'])
        saved = json.loads((self.root/'category-ledger.json').read_text())
        granted = saved['soft_credits'][-1]
        at, points = granted['at'], granted['points']
        context = b.rate_context(self.policy, saved, at)
        credit = lambda minutes, now: b.target_rate_window(
            self.policy, saved, {}, context, 'fable', minutes, (1.5,), now)['credit']
        self.assertAlmostEqual(credit(5, at), points)
        self.assertAlmostEqual(credit(60, at + 10 * 60), points)  # still inside the hour
        self.assertEqual(credit(5, at + 5 * 60 + 1), 0.)          # slid out of the 5-minute window
        self.assertEqual(credit(60, at + 60 * 60 + 1), 0.)
        # Not before it was granted either.
        self.assertEqual(credit(5, at - 1), 0.)


if __name__ == '__main__':
    unittest.main()
