"""One-file offline checks: frozen fixtures, no provider/network/process calls."""
import copy
from contextlib import closing
import json
import os
from pathlib import Path
import unittest

import test_helper_admission as fixtures
import usage_target_rates as view
import claude_usage_boxes as boxes
import outside_api_budget as api
import claude_usage_messages as human


class TargetRates(unittest.TestCase):
    setUp=fixtures.AdmissionTests.setUp
    save=fixtures.AdmissionTests.save
    write=fixtures.AdmissionTests.write

    def prepare(self):
        for saved in self.ledger['sources'].values():
            if saved['row']['provider']=='claude':
                saved['row'].update(fable_observed_at=self.at,nf_observed_at=self.at)
        with closing(api.db_open(self.home/'.local/state/outside-api-budget')) as db, db:
            api.put(db,'cap','40')
            api.put(db,'credit','0')
            api.put(db,'hard_extra','0')
            api.put(db,'rate_epoch','0')
            api.put(db,'providers','cerebras')
        factor=self.policy['rate_points_per_million']['codex']/1e6
        target=468/self.policy['preparation']['active_hours']
        self.samples['events']=[[self.at-1,'codex',target/12/factor]]
        self.save()

    def row(self,result,key):return next(r for r in result['categories'] if r['category']==key)

    def test_known_target_percentage_and_no_state_writes_or_identifiers(self):
        self.prepare()
        before={str(p):(p.read_bytes(),p.stat().st_mtime_ns) for p in self.home.rglob('*') if p.is_file()}
        result=view.snapshot(self.home,self.at)
        self.assertAlmostEqual(self.row(result,'codex')['windows']['5']['target_percent'],100)
        self.assertEqual(self.row(result,'outside-api')['windows']['60']['target_percent'],0)
        self.assertEqual(result['unit'],'target_percent')
        self.assertNotIn('@',json.dumps(result))
        self.assertNotIn(str(self.home),json.dumps(result))
        self.assertEqual(before,{str(p):(p.read_bytes(),p.stat().st_mtime_ns) for p in self.home.rglob('*') if p.is_file()})

    def test_stale_and_missing_do_not_become_zero(self):
        self.prepare()
        p=self.root/'passive-rate.json';os.utime(p,(self.at-91,)*2)
        result=view.snapshot(self.home,self.at)
        self.assertEqual(self.row(result,'codex')['windows']['5']['status'],'stale')
        self.assertIsNone(self.row(result,'codex')['windows']['5']['target_percent'])
        p.unlink()
        result=view.snapshot(self.home,self.at)
        self.assertIsNone(self.row(result,'codex')['windows']['5']['target_percent'])
        self.assertEqual(self.row(result,'outside-api')['windows']['5']['status'],'ok')

    def test_per_window_gap_and_incomplete_sample(self):
        self.prepare()
        self.ledger['gaps']=[[self.at-400,'codex']];self.save()
        result=view.snapshot(self.home,self.at)
        row=self.row(result,'codex')
        self.assertEqual(row['windows']['5']['status'],'ok')
        self.assertIsNone(row['windows']['15']['target_percent'])
        self.samples['token_complete']=False;self.save()
        self.assertEqual(self.row(view.snapshot(self.home,self.at),'codex')['windows']['5']['status'],'incomplete')

    def test_source_age_and_missing_baseline(self):
        self.prepare()
        key=next(k for k in self.ledger['sources'] if k.startswith('codex:'))
        self.ledger['sources'][key]['row']['observed_at']=self.at-1201;self.save()
        self.assertIsNone(self.row(view.snapshot(self.home,self.at),'codex')['windows']['5']['target_percent'])
        self.ledger['preparation']={};self.save()
        self.assertEqual(self.row(view.snapshot(self.home,self.at),'fable')['windows']['5']['status'],'unavailable')

    def test_fresh_account_cannot_mask_stale_category_gauge(self):
        self.prepare()
        key=next(k for k in self.ledger['sources'] if k.startswith('claude:'))
        row=self.ledger['sources'][key]['row']
        row['fable_observed_at']=self.at-1201;self.save()
        self.assertIsNone(self.row(view.snapshot(self.home,self.at),'fable')['windows']['5']['target_percent'])
        row['fable_observed_at']=self.at;row.update(nf_points=10,nf_observed_at=self.at-1201);self.save()
        self.assertIsNone(self.row(view.snapshot(self.home,self.at),'non-fable')['windows']['5']['target_percent'])
        # Existing NF fallback explicitly records account observation time.
        row.update(nf_estimated=True,nf_observed_at=self.at,fable_observed_at=self.at-1201);self.save()
        self.assertEqual(self.row(view.snapshot(self.home,self.at),'non-fable')['windows']['5']['status'],'ok')

    def test_excluded_fable_gauge_stays_fixed_without_waiving_other_freshness(self):
        self.prepare()
        key=next(k for k in self.ledger['sources'] if k.startswith('claude:'))
        row=self.ledger['sources'][key]['row']
        row.update(fable_excluded=True,fable_excluded_since=self.at-3600,fable_observed_at=self.at-1201)
        self.save()
        self.assertEqual(self.row(view.snapshot(self.home,self.at),'fable')['windows']['5']['status'],'ok')
        row['nf_observed_at']=self.at-1201;self.save()
        self.assertEqual(self.row(view.snapshot(self.home,self.at),'non-fable')['windows']['5']['status'],'stale')
        self.ledger['sources'][key]['time']=self.at-121;self.save()
        self.assertEqual(self.row(view.snapshot(self.home,self.at),'fable')['windows']['5']['status'],'stale')
        self.ledger['sources'][key]['time']=self.at
        row['fable_excluded']=False;self.save()
        self.assertEqual(self.row(view.snapshot(self.home,self.at),'fable')['windows']['5']['status'],'stale')

    def test_current_exclusion_without_window_history_does_not_make_freshness(self):
        self.prepare()
        key=next(k for k in self.ledger['sources'] if k.startswith('claude:'))
        row=self.ledger['sources'][key]['row']
        row.update(fable_excluded=True,fable_observed_at=self.at-1201)
        self.save()
        self.assertEqual(self.row(view.snapshot(self.home,self.at),'fable')['windows']['5']['status'],'stale')
        row['fable_excluded_since']=self.at-200;self.save()
        self.assertEqual(self.row(view.snapshot(self.home,self.at),'fable')['windows']['5']['status'],'incomplete')

    def test_ladder_refactor_preserves_legacy_threshold_math(self):
        # The expected values are what the pre-refactor module (a backup kept only in the installed
        # ~/.local/bin) returned for these inputs, so the test no longer reads the live home folder.
        self.prepare()
        golden=json.loads((Path(__file__).parent/'fixtures/ladder-before-target-rate.json').read_text())
        self.ledger['meter_intervals']=[[self.at-299,self.at,'codex',100,next(k for k in self.ledger['sources'] if k.startswith('codex:'))]]
        for linear in (False,True):
            policy=copy.deepcopy(self.policy);policy['linear_meter_steps']=linear
            state={}
            lines,trips=boxes.ladder_evaluate(policy,copy.deepcopy(self.ledger),self.samples,state,self.at,all_trips=True)
            # Each rate trip also carries its planned amount, so the voice can say
            # "300 percent" at a 3x stop; it is the planned amount the legacy line names.
            for trip in trips:
                planned=trip.pop('planned')
                [line]=[l for l in lines if l.startswith('STOP ') and f": {trip['name'].split(': ')[1].split('-')[0]}m confirmed" in l]
                self.assertIn(f'Target rate: {planned:g} points;',line)
            expected=golden['linear' if linear else 'stepped']
            self.assertEqual(json.loads(json.dumps([lines,trips])),expected['result'])
            self.assertEqual(json.loads(json.dumps(state)),expected['state'])
            if not linear:
                self.assertEqual(len(trips),4)  # the stepped case does trip; the comparison is not vacuous

    def test_wording_emits_target_and_accepts_legacy_annotation(self):
        self.assertIn('152 target %',human.rate_detail(3,1.98,15))
        line='Fable 5m 2× crossed (2.00×; 2.00 points / 1.00 planned). Run rate: 1 points; Spend: 2 points; Box: Preparation.'
        text=human.render([('x',2,line)],self.at)
        self.assertIn('target rate',text)
        self.assertNotIn('run rate',text.lower())

if __name__=='__main__':unittest.main()
