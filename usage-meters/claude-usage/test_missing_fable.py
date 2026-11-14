"""Offline missing-Fable accounting regressions; no proxy/provider operations."""
import copy
import unittest
import claude_usage_boxes as b
import claude_usage_messages as human
import test_categories as fixture


class MissingFable(fixture.Categories):
    def baseline(self):
        row = self.rows[0]
        row.update(u7=.13, uf=.26, nf_points=0., observed_at=self.now-120,
                   nf_observed_at=self.now-120, fable_observed_at=self.now-120)
        ledger = self.ledger(self.now-120)
        key = 'claude:'+row['email']
        ledger['sources'][key]['high']['non-fable'] = 1.
        ledger['events'].append([self.now-121, 'non-fable', 5.])
        return row,ledger,key

    def test_missing_excluded_fable_backfills_monotonically_and_deduplicates(self):
        row,ledger,key = self.baseline()
        weight = self.policy['sources'][0]['weight']
        row.update(u7=.14, uf=None, nf_points=None, fable_excluded=True,
                   observed_at=self.now, nf_observed_at=None, fable_observed_at=None)
        before = sum(e[2] for e in ledger['events'] if e[1]=='non-fable')
        self.record(ledger)
        after = sum(e[2] for e in ledger['events'] if e[1]=='non-fable')
        self.assertAlmostEqual(after-before, weight)  # high-water1 -> synthetic2.
        self.assertEqual(ledger['sources'][key]['high']['fable'],26.)
        self.assertFalse(ledger['sources'][key].get('carried'))
        self.assertTrue(ledger['estimated_meter_updates'])
        self.record(ledger,at=self.now+60)
        row.update(uf=.26,nf_points=2.,nf_observed_at=self.now+120,fable_observed_at=self.now+120)
        self.record(ledger,at=self.now+120)
        self.assertAlmostEqual(sum(e[2] for e in ledger['events'] if e[1]=='non-fable'),after)

    def test_missing_unproven_fable_stays_carried_but_no_traffic_can_fallback(self):
        row,ledger,key = self.baseline()
        row.update(u7=.14,uf=None,nf_points=None,observed_at=self.now,nf_observed_at=None)
        self.record(ledger)
        self.assertIn('non-fable',ledger['sources'][key]['carried'])
        row['fable_no_traffic']=True
        self.record(ledger,at=self.now+60)
        self.assertNotIn('non-fable',ledger['sources'][key]['carried'])

    def test_top_level_pair_without_model_entry_preserves_fable_and_non_fable(self):
        row,ledger,key = self.baseline()
        prefix = 'Anthropic-Ratelimit-Unified-'
        from datetime import datetime, timezone
        raw = {'files': [dict(provider='claude', email=row['email'], quota=dict(
            observed_at=datetime.fromtimestamp(self.now, timezone.utc).isoformat(),
            signals={prefix+'7d-Utilization': '.14', prefix+'7d_oi-Utilization': '.28',
                     prefix+'7d-Reset': row['r7'], prefix+'7d_oi-Reset': row['rf']}))]}
        parsed = self.host.parse(raw, excluded_accounts={row['email']})[0]
        before = list(ledger['events'])
        self.record(ledger,rows=[parsed])
        self.assertAlmostEqual(ledger['sources'][key]['high']['fable'], 28.)
        self.assertAlmostEqual(ledger['sources'][key]['high']['non-fable'], 1.)
        self.assertFalse(any(e[1]=='non-fable' for e in ledger['events'][len(before):]))
        self.assertFalse(parsed['fable_no_traffic'])
        self.assertFalse(ledger['sources'][key]['row'].get('nf_estimated'))

    def test_missing_fable_new_reset_does_not_recharge_historical_fable(self):
        row,ledger,key = self.baseline()
        old_reset = row['rf']
        before = sum(e[2] for e in ledger['events'] if e[1]=='fable')
        row.update(u7=.14, uf=None, nf_points=None, fable_excluded=True,
                   rf=old_reset+604800, observed_at=self.now,
                   nf_observed_at=None, fable_observed_at=None)
        self.record(ledger)
        self.assertEqual(sum(e[2] for e in ledger['events'] if e[1]=='fable'), before)
        self.assertEqual(ledger['sources'][key]['row']['rf'],old_reset)
        self.record(ledger,at=self.now+60)
        self.assertEqual(sum(e[2] for e in ledger['events'] if e[1]=='fable'), before)
        # A later real new-epoch gauge charges only its actual new usage.
        row.update(uf=.02, nf_points=26., nf_observed_at=self.now+120,
                   fable_observed_at=self.now+120, observed_at=self.now+120)
        self.record(ledger,at=self.now+120)
        self.assertAlmostEqual(sum(e[2] for e in ledger['events'] if e[1]=='fable')-before,
                               2*self.policy['sources'][0]['weight'])

    def test_newer_model_fable_retained_before_account_only_remainder(self):
        row,ledger,key = self.baseline()
        # Start with a lower paired snapshot, then receive an intermediate real
        # Fable pair and a newer account-only reading in the same poll.
        row.update(u7=.12,uf=.24)
        ledger = self.ledger(self.now-120)
        row.update(u7=.14,uf=.26,nf_points=0.,fable_excluded=True,
                   observed_at=self.now,nf_observed_at=self.now-60,
                   fable_observed_at=self.now-60)
        self.record(ledger)
        weight=self.policy['sources'][0]['weight']
        self.assertAlmostEqual(sum(e[2] for e in ledger['events'] if e[1]=='fable'),2*weight)
        self.assertAlmostEqual(sum(e[2] for e in ledger['events'] if e[1]=='non-fable'),2*weight)
        self.assertEqual(ledger['sources'][key]['row']['uf'],.26)
        self.record(ledger,at=self.now+60)
        self.assertAlmostEqual(sum(e[2] for e in ledger['events'] if e[1]=='non-fable'),2*weight)

    def test_weekly_reset_never_readopts_pre_epoch_model_on_repeated_poll(self):
        row,ledger,key=self.baseline()
        row.update(u7=.01,uf=.26,nf_points=0.,fable_excluded=True,
                   r7=row['r7']+604800,observed_at=self.now,
                   nf_observed_at=self.now-60,fable_observed_at=self.now-60)
        self.record(ledger)
        after=list(ledger['events'])
        self.assertEqual(ledger['sources'][key]['row']['uf'],0.)
        self.record(ledger,at=self.now+60)
        self.assertEqual(ledger['events'],after)
        self.assertEqual(ledger['sources'][key]['row']['uf'],0.)
        # A genuine post-reset model snapshot can be counted once.
        row.update(u7=.02,uf=.02,nf_points=2.,observed_at=self.now+120,
                   nf_observed_at=self.now+120,fable_observed_at=self.now+120)
        self.record(ledger,at=self.now+120)
        self.assertAlmostEqual(sum(e[2] for e in ledger['events'][len(after):] if e[1]=='fable'),
                               2*self.policy['sources'][0]['weight'])

    def test_parser_exclusion_and_reset_label_and_estimate_visibility(self):
        raw={'files':[dict(provider='claude',email='fixture@invalid',excluded_models=['*fable*'],
                          quota=dict(signals={'Anthropic-Ratelimit-Unified-7d-Utilization':'.14'}))]}
        parsed=self.host.parse(raw,excluded_accounts=set())[0]
        self.assertTrue(parsed['fable_excluded']);self.assertFalse(parsed['fable_no_traffic'])
        row,ledger,key=self.baseline()
        ledger['sources'][key]['row']['nf_estimated']=True
        reading=dict(category='non-fable',name='Preparation total',used=5.,allowed=100.,end=self.now+3600,prep_total=True)
        text=b.notice_message(reading,self.now,ledger=ledger)
        self.assertIn('estimated non-Fable split',text)
        timing=human.timing_line(dict(ends='5-hour limit resets: Wed 04:10 ET'))
        self.assertEqual(timing,'5-hour limit resets: Wed 04:10 ET')


if __name__=='__main__':unittest.main()
