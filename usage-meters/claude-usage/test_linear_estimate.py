"""Linear meter windows and optional fitted-token minimum, entirely offline."""
import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import claude_usage_boxes as b
import token_cost_model as fitted


class LinearEstimate(unittest.TestCase):
    def setUp(self):
        self.ledger=b.empty_ledger()
        self.key='codex:account'
        self.ledger['events']=[[600.,'codex',6.]]
        self.ledger['spread_events']=[[0.,600.,'codex',6.,self.key,1.]]
        self.policy={'linear_meter_steps':True}

    def test_window_takes_only_overlapping_fraction(self):
        self.assertEqual(b.meter_estimate(self.ledger,'codex',300,600),3)
        self.assertEqual(b.meter_estimate(self.ledger,'codex',0,600),6)
        self.assertEqual(b.meter_estimate(self.ledger,'codex',700,900),0)

    def test_last_rate_continues_but_never_over_one_meter_step(self):
        self.assertEqual(b.meter_estimate(self.ledger,'codex',600,650),.5)
        self.assertEqual(b.meter_estimate(self.ledger,'codex',600,700),1)
        self.assertEqual(b.meter_estimate(self.ledger,'codex',600,3600),1)
        self.assertEqual(b.meter_estimate(self.ledger,'codex',650,900),.5)

    def test_release_stops_old_interval_extrapolation(self):
        self.ledger['forgiven']['codex']=600
        self.assertEqual(b.meter_estimate(self.ledger,'codex',600,900),0)

    def test_pooled_accounts_are_weighted_before_summing(self):
        self.ledger['events'].append([600.,'codex',.6])
        self.ledger['spread_events'].append([0.,600.,'codex',.6,'codex:small',.1])
        self.assertAlmostEqual(b.meter_estimate(self.ledger,'codex',300,600),3.3)

    def test_lower_estimate_and_unknown_fallback(self):
        with patch.object(b,'token_estimate',return_value=2):
            self.assertEqual(b.box_estimate(self.policy,self.ledger,'codex',300,600),2)
        with patch.object(b,'token_estimate',return_value=4):
            self.assertEqual(b.box_estimate(self.policy,self.ledger,'codex',300,600),3)
        with patch.object(b,'token_estimate',return_value=None):
            self.assertEqual(b.box_estimate(self.policy,self.ledger,'codex',300,600),3)

    def test_disabled_flag_preserves_installed_accounting(self):
        self.policy['linear_meter_steps']=False
        self.assertEqual(b.box_estimate(self.policy,self.ledger,'codex',300,600),6)

    def test_missing_model_or_uncovered_window_falls_back(self):
        with patch.object(b.Path,'read_text',side_effect=OSError):
            self.assertIsNone(b.token_estimate(self.policy,self.ledger,'codex',0,600))
        self.assertIsNone(b.token_estimate(self.policy,self.ledger,'fable',0,600))

    def test_only_supported_complete_fresh_category_model_is_used(self):
        self.ledger['token_sample']=dict(since=0,at=600,complete=True,records=[])
        with patch.object(fitted,'estimate_points',return_value={'fable':.5,'codex':0,'non-fable':0}):
            self.assertEqual(b.token_estimate(self.policy,self.ledger,'fable',300,600),.5)
            self.assertIsNone(b.token_estimate(self.policy,self.ledger,'codex',300,600))
            self.assertIsNone(b.token_estimate(self.policy,self.ledger,'non-fable',300,600))
            self.assertIsNone(b.token_estimate(self.policy,self.ledger,'fable',-1,600))
            self.assertIsNone(b.token_estimate(self.policy,self.ledger,'fable',300,700))

    def test_step_clock_does_not_restart_on_flat_poll(self):
        policy={'sources':[dict(provider='codex',email='account',weight=1)]}
        ledger=b.empty_ledger()
        row=dict(provider='codex',email='account',u7=.20,r7=99999)
        b.observe(ledger,[row],100,policy)
        b.observe(ledger,[row],200,policy)
        row['u7']=.21
        b.observe(ledger,[row],300,policy)
        self.assertEqual(ledger['spread_events'][0][:2],[100,300])
        step=ledger['steps'][0]
        self.assertEqual((step['meter'],step['old'],step['new'],step['previous_step']),('weekly',20,21,100))

    def test_step_log_is_append_only_without_duplicate_checks(self):
        with tempfile.TemporaryDirectory() as root:
            from types import SimpleNamespace
            host=SimpleNamespace(LOG=Path(root)/'log.tsv')
            ledger={'steps':[dict(id='step1',first_seen=123,account='account',meter='weekly',old=20,new=21,previous_step=100,routing=None)]}
            b.log_steps(host,ledger);b.log_steps(host,ledger)
            lines=(Path(root)/'meter-steps.jsonl').read_text().splitlines()
            self.assertEqual(len(lines),1)
            self.assertEqual(json.loads(lines[0])['old'],20)

    def test_token_dedup_cache_types_and_unknown_account(self):
        record={'timestamp':'2025-11-11T20:00:00Z','type':'assistant','message':{'id':'msg','model':'claude-fable-5-2',
            'usage':{'input_tokens':100,'output_tokens':20,'cache_creation_input_tokens':30,'cache_read_input_tokens':40}}}
        now=b.stamp(record['timestamp'])
        state={'routes':[[now-1,'claude','claude-fable-5-2','account-b@example.com']]}
        b.sample_token_record(record,'claude',{},state,now)
        b.sample_token_record(record,'claude',{},state,now)
        self.assertEqual(len(state['token_records']),1)
        self.assertEqual(state['token_records'][0]['usage']['input'],100)
        self.assertEqual(state['token_records'][0]['usage']['account'],'account-b@example.com')
        record['message']['id']='msg2'
        state['routes'].append([now-1,'claude','claude-fable-5-2','account-a@example.com'])
        b.sample_token_record(record,'claude',{},state,now)
        self.assertIsNone(state['token_records'][-1]['usage']['account'])

    def test_equal_reset_and_spread_events_are_counted_separately(self):
        self.ledger['events'].append([600.,'codex',6.])
        self.assertEqual(b.meter_estimate(self.ledger,'codex',0,600),12)

    def test_missing_roots_cannot_certify_zero_tokens(self):
        with tempfile.TemporaryDirectory() as root:
            state={'token_since':0}
            b.sample_sessions(state,{'claude':Path(root)/'missing'},600)
            self.assertFalse(state['token_complete'])
            self.assertEqual(state['token_since'],600)


class NonFableRounding(unittest.TestCase):
    def test_single_shared_meter_step_cannot_rate_stop_with_d_enabled(self):
        policy=b.load(Path(__file__).parent/'fixtures/boxes-preparation-v2.toml')
        policy.update(approved=True,linear_meter_steps=True)
        for run in policy['runs']: run['approved']=True
        now=b.stamp('2025-11-11T16:00:00-05:00')
        ledger=b.empty_ledger()
        rows=[dict(provider=s['provider'],email=s['email'],u7=.20,uf=.10,
                   nf_points=30 if s['provider']=='claude' else None,nf_observed_at=now-300,
                   observed_at=now-300,fable_observed_at=now-300,r7=now+10000,rf=now+10000)
              for s in policy['sources']]
        b.observe(ledger,rows,now-300,policy)
        prep=policy['preparation']
        ledger['preparation']={str(prep['start']):dict(at=now-3600,C=dict.fromkeys(b.CATEGORIES,58.5),
                P=dict.fromkeys(b.CATEGORIES,39.),resets=dict.fromkeys(b.CATEGORIES,0))}
        run=b.active_run(policy,now)
        ledger['runs'][b.run_id(run)]=dict(allow=dict(run['allow']),baseline=now-3600)
        rows[0].update(u7=.21,nf_points=32,nf_observed_at=now)
        b.observe(ledger,rows,now,policy)
        _,trips=b.ladder_evaluate(policy,ledger,{}, {},now)
        self.assertFalse(any(item['category']=='non-fable' for item in trips))

    def test_sampler_resume_cannot_certify_usage_during_downtime(self):
        import os
        with tempfile.TemporaryDirectory() as root:
            path=Path(root)/'session.jsonl'
            path.write_text('{"type":"assistant","timestamp":"2025-11-11T10:00:00Z","message":{"id":"old","model":"claude-fable-5-2","usage":{"output_tokens":1000}}}\n')
            os.utime(path,(1000,1000))
            state={'token_since':0,'token_last_sampled':100}
            b.sample_sessions(state,{'claude':Path(root)},10000)
            self.assertEqual(state['token_since'],10000)
