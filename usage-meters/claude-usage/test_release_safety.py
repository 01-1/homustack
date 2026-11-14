"""Missing-meter and human override safety; disposable auth files only."""
import copy
import io
import json
from contextlib import redirect_stdout
from unittest.mock import patch
import test_categories as fixtures
import claude_usage_boxes as b


class ReleaseSafety(fixtures.Categories):
    def test_codex_missing_meter_carries_high_water_and_recovers_once(self):
        ledger = self.ledger()
        self.rows[2]['u7'] = .25
        self.record(ledger, at=self.now - 120)
        self.rows[2]['u7'] = None
        self.record(ledger, at=self.now - 60)
        key = 'codex:' + self.rows[2]['email']
        self.assertEqual(ledger['sources'][key]['high']['codex'], 25)
        self.assertTrue(ledger['sources'][key]['carried'])
        self.assertFalse(ledger['gaps'])
        self.rows[2]['u7'] = .26
        self.rows[3]['u7'] = .27
        self.record(ledger)
        self.assertAlmostEqual(b.spent(ledger, 'codex', self.now - 86400, self.now), 7)
        self.assertFalse(ledger['sources'][key]['carried'])

    def test_claude_missing_both_gauges_keeps_both_categories(self):
        ledger = self.ledger()
        self.rows[1].update(u7=None, uf=None, u5=1)
        self.record(ledger)
        source = ledger['sources']['claude:' + self.rows[1]['email']]
        self.assertEqual(set(source['carried']), {'fable', 'non-fable'})
        self.assertEqual(source['high'], {'fable': 10, 'non-fable': 30})
        self.assertFalse(ledger['gaps'])

    def test_incomplete_alone_never_latches_any_category(self):
        ledger = self.ledger()
        items = [dict(category=c, approved=True, used=1, allowed=100, incomplete=True)
                 for c in b.CATEGORIES]
        b.enforce(self.host, self.policy, ledger, items, self.now)
        self.assertFalse(self.host.guard_state(self.host.GUARD_STATE)['latches'])

    def test_missing_release_and_recovery_do_not_charge_blocked_spend(self):
        ledger = self.ledger()
        path = self.host.LOG.parent / 'category-ledger.json'
        path.write_text(json.dumps(ledger))
        with redirect_stdout(io.StringIO()):
            self.assertTrue(b.uncap(self.host, 'codex', path, self.policy,
                                   confirmer=lambda _: True, reader=lambda: []))
        ledger = json.loads(path.read_text())
        now = ledger['forgiven']['codex']
        self.rows[2]['u7'] = .8
        b.observe(ledger, self.rows, now + 1, self.policy)
        self.assertEqual(b.spent(ledger, 'codex', now, now + 1), 0)
        self.assertFalse(ledger['gaps'])
        self.rows[2]['u7'] = .81
        b.observe(ledger, self.rows, now + 2, self.policy)
        self.assertAlmostEqual(b.spent(ledger, 'codex', now, now + 2), 1)

    def test_override_needs_confirmation_and_no_meter_or_policy(self):
        with patch.object(b, 'append_alert'), patch.object(self.host, 'desktop_notify'):
            self.assertEqual(b.override_command(self.host, confirmer=lambda: False), 2)
            self.assertFalse((self.host.LOG.parent / 'hard-continue.json').exists())
            self.assertEqual(b.override_command(self.host, confirmer=lambda: True, now=self.now), 0)
            self.assertEqual(set(b.override_state(self.host)['disabled']), set(b.CATEGORIES))
            item = dict(category='codex', approved=True, used=100, allowed=1, incomplete=False)
            b.enforce(self.host, self.policy, b.empty_ledger(), [item], self.now)
            self.assertFalse(self.host.guard_state(self.host.GUARD_STATE)['latches'])
            self.assertEqual(b.override_command(self.host, resume='codex', now=self.now+1), 0)
            self.assertEqual(set(b.override_state(self.host)['disabled']), {'fable', 'non-fable'})
            ledger = json.loads((self.host.LOG.parent / 'category-ledger.json').read_text())
            ledger['events'] = [[self.now+.5, 'codex', 50]]
            self.assertEqual(b.spent(ledger, 'codex', self.now-1, self.now+2), 0)
            self.assertEqual(b.override_command(self.host, resume='all', now=self.now+2), 0)
            self.assertFalse(b.override_state(self.host)['disabled'])

    def test_missing_meter_keeps_original_evidence_time(self):
        ledger = self.ledger()
        self.rows[2]['observed_at'] = self.now - 3600
        b.observe(ledger, self.rows, self.now - 3500, self.policy)
        self.rows[2].update(u7=None, observed_at=self.now - 60)
        b.observe(ledger, self.rows, self.now - 30, self.policy)
        self.rows[2].update(u7=.30, observed_at=self.now)
        b.observe(ledger, self.rows, self.now, self.policy)
        self.assertEqual(b.confirmed_window(ledger, 'codex', self.now - 300, self.now), 0)

    def test_one_point_cached_reversal_is_not_full_meter_reset(self):
        ledger = self.ledger()
        self.rows[2]['u7'] = .19
        self.record(ledger, at=self.now - 60)
        self.rows[2]['u7'] = .20
        self.record(ledger)
        self.assertEqual(b.spent(ledger, 'codex', self.now - 86400, self.now), 0)
        self.assertFalse(ledger['gaps'])


    def test_redeemed_fable_reset_preserves_spending_and_latches(self):
        self.rows[0].update(u7='0.73',uf=1.0,nf_points=46.)
        ledger=self.ledger()
        ledger['events']=[[self.now-100,'fable',7.], [self.now-100,'non-fable',2.]]
        old_events=copy.deepcopy(ledger['events'])
        self.rows[0].update(u7='0.23',uf=0.,nf_points=46.)
        b.observe(ledger,self.rows,self.now,self.policy)
        self.assertEqual(ledger['events'],old_events)
        self.assertEqual(b.spent(ledger,'fable',self.now-1000,self.now),7.)
        self.assertEqual(b.spent(ledger,'non-fable',self.now-1000,self.now),2.)
        b.enforce(self.host,self.policy,ledger,[dict(category=c,name='test',used=b.spent(ledger,c,self.now-1000,self.now),
                  allowed=100,approved=True,incomplete=True,end=self.now+1000,run=True) for c in b.CATEGORIES],self.now)
        self.assertFalse(self.host.guard_state(self.host.GUARD_STATE)['latches'])
