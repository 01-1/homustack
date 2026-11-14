"""Approved weekly reset capacity, entirely synthetic local state."""
import copy
from pathlib import Path
import unittest
import claude_usage_boxes as b


class WeeklyResetBudget(unittest.TestCase):
    def setUp(self):
        self.policy=b.load(Path(__file__).parent/'fixtures/boxes-preparation-v2.toml')
        self.main=next(s for s in self.policy['sources'] if s['provider']=='claude' and s['weight']==1)
        self.main['weekly_banked']=2
        self.main['fable_banked']=2
        self.policy['preparation']['active_hours']=26
        self.now=self.policy['preparation']['start']+100
        self.ledger=b.empty_ledger()
        self.ledger['events']=[[self.now-1,'fable',19.5]]
        self.ledger['preparation']={str(self.policy['preparation']['start']):dict(
            at=self.now-10,C={'fable':308.9,'non-fable':65.,'codex':280.14285714285717},
            P={'fable':205.9333333333333,'non-fable':43.333333333333336,'codex':186.7619047619048})}

    def test_approved_numbers_preserve_spend_start_codex_and_idempotence(self):
        before=copy.deepcopy(self.ledger)
        saved=b.add_weekly_reset_capacity(self.policy,self.ledger,self.main['email'],2,'weekly-20251111',self.now)
        for category,c,p,r in (('fable',508.9,339.26666666666665,13.048717948717948),
                               ('non-fable',265.,176.66666666666666,6.794871794871795)):
            self.assertAlmostEqual(saved['C'][category],c)
            self.assertAlmostEqual(saved['P'][category],p)
            self.assertAlmostEqual(saved['P'][category]/26,r)
        self.assertEqual(self.ledger['events'],before['events'])
        self.assertEqual(saved['at'],self.now-10)
        self.assertEqual(saved['P']['codex'],186.7619047619048)
        b.add_weekly_reset_capacity(self.policy,self.ledger,self.main['email'],2,'weekly-20251111',self.now)
        self.assertAlmostEqual(saved['P']['fable'],339.26666666666665)
        without=copy.deepcopy(self.ledger);without['preparation']=before['preparation']
        self.assertEqual(without,before)

    def test_weekly_and_fable_restore_separate_halves_no_double_count(self):
        baseline=b.capacities(self.policy)
        self.main['weekly_banked']=0
        old=b.capacities(self.policy)
        self.assertEqual(baseline['fable']-old['fable'],200)
        self.assertEqual(baseline['non-fable']-old['non-fable'],200)
        self.assertEqual(baseline['codex'],old['codex'])
        self.main['weekly_banked']=2
        self.ledger['sources']={}
        for s in self.policy['sources']:
            self.ledger['sources'][s['provider']+':'+s['email']]=dict(time=self.now,row=dict(
                provider=s['provider'],email=s['email'],u7=.28,uf=.2,u5=None))
        remaining=b.remaining_capacity(self.policy,self.ledger,self.policy['preparation']['end'],self.now)
        self.main['weekly_banked']=0
        previous=b.remaining_capacity(self.policy,self.ledger,self.policy['preparation']['end'],self.now)
        self.assertAlmostEqual(remaining['fable']-previous['fable'],200)
        self.assertAlmostEqual(remaining['non-fable']-previous['non-fable'],200)

    def test_summary_shows_higher_rate_and_both_reset_inventories(self):
        b.add_weekly_reset_capacity(self.policy,self.ledger,self.main['email'],2,'weekly-20251111',self.now)
        text='\n'.join(b.preparation_summary(self.policy,self.ledger,self.now))
        self.assertIn('H=26',text)
        self.assertIn('C=508.90, P=339.27',text)
        self.assertIn('R=13.049',text)
        self.assertIn('weekly=2',text)

    def test_missing_frozen_snapshot_is_rejected_without_mutation(self):
        self.ledger.pop('preparation')
        before=copy.deepcopy(self.ledger)
        with self.assertRaisesRegex(ValueError,'existing frozen'):
            b.add_weekly_reset_capacity(self.policy,self.ledger,self.main['email'],2,'new',self.now)
        self.assertEqual(self.ledger,before)


if __name__=='__main__':unittest.main()
