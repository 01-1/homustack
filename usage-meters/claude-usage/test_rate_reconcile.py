"""Rate reconciliation and human soft credits using disposable proxy auth files."""
import copy
import io
import json
from contextlib import redirect_stdout
from datetime import datetime
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import claude_usage_boxes as b
import claude_usage_messages as human
import test_ladder as fixture


class Reconcile(unittest.TestCase):
    ledger = fixture.LadderTests.ledger
    record = fixture.LadderTests.record
    confirmed = fixture.LadderTests.confirmed

    def setUp(self):
        fixture.LadderTests.setUp(self)
        self.host.desktop_notify = lambda line: True
        patch.object(b, 'append_alert', return_value=True).start()
        patch.object(b, 'thread_notice', return_value='steered').start()

    def latch(self, category='fable', minutes=5, multiple=3.2):
        self.confirmed(category, minutes, multiple)
        lines, trips = b.ladder_evaluate(self.policy, self.data, {}, self.state, self.now)
        b.enforce(self.host, self.policy, self.data, trips, self.now)
        self.assertTrue(self.host.guard_state(self.host.GUARD_STATE)['latches'])

    def persist(self):
        (self.root/'category-ledger.json').write_text(json.dumps(self.data))
        (self.root/'rate-ladder.json').write_text(json.dumps(self.state))

    def soft(self, minutes, category='fable', confirm=True):
        with redirect_stdout(io.StringIO()) as output:
            result = b.soft_uncap(self.host, self.policy, category, minutes,
                                  confirmer=lambda: confirm, now=self.now + 1)
        return result, output.getvalue()

    def test_timed_uncap_credits_only_capped_categories(self):
        # Credit goes only to categories that are capped right now.
        self.latch('fable')
        self.persist()
        credits = lambda: [c['category'] for c in json.loads((self.root/'category-ledger.json').read_text()).get('soft_credits', [])]
        before = credits()
        for category in ('non-fable', 'codex'):
            with redirect_stdout(io.StringIO()) as output:
                self.assertEqual(b.soft_uncap(self.host, self.policy, category, 10, confirmer=lambda: True,
                                              now=self.now + 1, only_if_capped=True), 0)
            self.assertIn('not capped; skipped, no credit added', output.getvalue())
        self.assertEqual(credits(), before)
        with redirect_stdout(io.StringIO()):
            b.soft_uncap(self.host, self.policy, 'fable', 10, confirmer=lambda: True, now=self.now + 1, only_if_capped=True)
        self.assertEqual(credits(), before + ['fable'])

    def test_changed_hours_releases_rate_stop_and_restores_proxy_auth(self):
        self.latch()
        before = copy.deepcopy(self.data)
        self.policy['preparation']['active_hours'] = 24
        lines = b.reconcile_rate_latches(self.host, self.policy, self.data, self.state, self.now + 1)
        self.assertEqual(len(lines), 1)  # one category notice, not one/account
        self.assertTrue(lines[0].startswith('RELEASED'))
        self.assertFalse(self.host.guard_state(self.host.GUARD_STATE)['latches'])
        for path in self.host.AUTH_DIRS['claude'].glob('*.json'):
            self.assertNotIn('*fable*', json.loads(path.read_text()).get('excluded_models', []))
        self.assertEqual(self.data, before)
        self.assertNotIn('fable', self.state['trip_epochs'])

    def test_unchanged_math_does_not_age_out_or_release_on_new_run(self):
        self.latch()
        context = b.rate_context(self.policy, self.data, self.now)
        context['start'] = self.now + 3600
        with patch.object(b, 'rate_context', return_value=context):
            self.assertEqual(b.reconcile_rate_latches(self.host, self.policy, self.data, self.state, self.now+7200), [])
        self.assertTrue(self.host.guard_state(self.host.GUARD_STATE)['latches'])

    def test_pruned_evidence_does_not_release_unchanged_rate_stop(self):
        self.latch()
        self.data['meter_intervals'] = []
        self.assertEqual(b.reconcile_rate_latches(self.host, self.policy, self.data, self.state, self.now + 1), [])
        self.assertTrue(self.host.guard_state(self.host.GUARD_STATE)['latches'])

    def test_partial_history_does_not_release_unchanged_stop(self):
        self.latch(multiple=4)
        original = self.data['meter_intervals'][0]
        self.data['meter_intervals'] = [[original[0], self.now-150, 'fable', 2.5, original[4]],
                                       [self.now-150, original[1], 'fable', 2.5, original[4]]]
        self.data['meter_intervals'].pop(0)
        self.assertEqual(b.reconcile_rate_latches(self.host,self.policy,self.data,self.state,self.now+1), [])
        self.assertTrue(self.host.guard_state(self.host.GUARD_STATE)['latches'])

    def test_allowance_latch_never_released_by_changed_rate_math(self):
        reading = next(r for r in b.readings(self.policy, self.data, self.now) if r['category']=='codex')
        reading['used'] = reading['allowed'] + 1
        b.enforce(self.host, self.policy, self.data, [reading], self.now)
        old = self.host.guard_state(self.host.GUARD_STATE)['latches']
        self.policy['preparation']['active_hours'] = 1
        self.assertEqual(b.reconcile_rate_latches(self.host, self.policy, self.data, self.state, self.now + 1), [])
        self.assertEqual(self.host.guard_state(self.host.GUARD_STATE)['latches'], old)

    def test_failed_auth_restore_is_retried_without_adding_credit(self):
        self.latch()
        self.run['scale'] = 2
        apply = self.host.apply_hard_stops
        with patch.object(self.host, 'apply_hard_stops', side_effect=OSError('temporary auth failure')):
            with self.assertRaises(OSError):
                b.reconcile_rate_latches(self.host, self.policy, self.data, self.state, self.now + 1)
        self.assertTrue(self.host.guard_state(self.host.GUARD_STATE)['rate_reconcile_pending'])
        with patch.object(self.host, 'apply_hard_stops', wraps=apply) as retry:
            b.reconcile_rate_latches(self.host, self.policy, self.data, self.state, self.now + 2)
            retry.assert_called_once()
        self.assertFalse(self.host.guard_state(self.host.GUARD_STATE).get('rate_reconcile_pending'))

    def test_soft_credit_human_confirmation_required_and_no_meter_fetch(self):
        self.latch(); self.persist()
        old = (self.root/'category-ledger.json').read_bytes()
        result, output = self.soft(5, confirm=False)
        self.assertEqual(result, 2)
        self.assertEqual((self.root/'category-ledger.json').read_bytes(), old)
        self.assertIn('human terminal confirmation', output)

    def test_insufficient_soft_credit_stacks_without_forgiving_spend(self):
        self.latch(multiple=4); self.data['meter_intervals'][0][0] = self.now - 290; self.persist()
        result, output = self.soft(1)
        self.assertEqual(result, 1)
        self.assertIn('0.8000 points short', output)
        self.assertIn('4.01 more minutes', output)
        self.assertTrue(self.host.guard_state(self.host.GUARD_STATE)['latches'])
        saved = json.loads((self.root/'category-ledger.json').read_text())
        self.assertEqual(saved['forgiven'], self.data['forgiven'])
        self.assertEqual(saved['events'], self.data['events'])
        self.assertEqual(saved['meter_intervals'], self.data['meter_intervals'])
        self.assertAlmostEqual(b.soft_credit(saved, 'fable'), .2)
        result, output = self.soft(5)
        self.assertEqual(result, 0)
        saved = json.loads((self.root/'category-ledger.json').read_text())
        self.assertAlmostEqual(b.soft_credit(saved, 'fable'), 1.2)
        self.assertFalse(self.host.guard_state(self.host.GUARD_STATE)['latches'])
        self.assertEqual(saved.get('pending_rebase'), self.data.get('pending_rebase'))
        self.assertEqual(saved['forgiven'], self.data['forgiven'])
        # Same old spending remains below raised threshold; further spend trips.
        _, trips = b.ladder_evaluate(self.policy, saved, {}, {}, self.now + 2)
        self.assertEqual(trips, [])
        saved['meter_intervals'][0][3] += .3
        _, trips = b.ladder_evaluate(self.policy, saved, {}, {}, self.now + 3)
        self.assertTrue(any(t['category']=='fable' for t in trips))

    def test_soft_credit_affects_run_prep_and_rate_but_not_other_categories(self):
        self.persist(); before = b.readings(self.policy, self.data, self.now)
        result, _ = self.soft(10)
        self.assertEqual(result, 0)
        saved = json.loads((self.root/'category-ledger.json').read_text())
        for old,new in zip(before,b.readings(self.policy,saved,self.now + 1)):
            self.assertEqual(new['used'], old['used'])
            self.assertEqual(new['allowed']-old['allowed'], 2 if old['category']=='fable' else 0)
        self.run['scale'] = 4
        self.assertEqual(b.soft_credit(saved,'fable'),2)  # fixed points, not minutes recalculated

    def test_soft_credit_uses_final_opening_rate(self):
        final = self.policy['runs'][1]; self.now = final['start']+60
        self.data['runs'][b.run_id(final)] = dict(allow=dict.fromkeys(b.CATEGORIES, 195.), baseline=final['start'])
        self.persist(); result,_=self.soft(2)
        self.assertEqual(result,0)
        saved=json.loads((self.root/'category-ledger.json').read_text())
        self.assertAlmostEqual(b.soft_credit(saved,'fable'),2)  #195 /3.25h /60 *2min

    def test_linear_soft_credit_cannot_erase_pruned_history(self):
        self.latch(multiple=4); self.policy['linear_meter_steps'] = True
        self.data['meter_intervals'] = []; self.persist()
        result, output = self.soft(1)
        self.assertEqual(result, 1)
        self.assertIn('0.8000 points short', output)
        self.assertTrue(self.host.guard_state(self.host.GUARD_STATE)['latches'])

    def test_allowance_trip_supersedes_rate_stop_for_same_category(self):
        self.latch()
        reading = next(r for r in b.readings(self.policy, self.data, self.now) if r['category']=='fable')
        reading['used'] = reading['allowed'] + 1
        b.enforce(self.host, self.policy, self.data, [reading], self.now)
        self.run['scale'] = 4
        self.assertEqual(b.reconcile_rate_latches(self.host,self.policy,self.data,self.state,self.now+1), [])
        self.assertTrue(all(v['box']==reading['name'] for v in self.host.guard_state(self.host.GUARD_STATE)['latches'].values()))

    def test_other_current_rate_window_prevents_soft_release(self):
        self.latch(multiple=3.2)
        self.data['meter_intervals'].append([self.now-800,self.now-700,'fable',8,'claude:account-b@example.com'])
        self.persist(); result,output = self.soft(2)
        self.assertEqual(result,1)
        self.assertIn('Still blocked',output)
        self.assertTrue(self.host.guard_state(self.host.GUARD_STATE)['latches'])
        self.assertTrue(any('15-minute rate' in v['box'] for v in self.host.guard_state(self.host.GUARD_STATE)['latches'].values()))

    def test_shortfall_covers_every_rate_window_not_only_first_trip(self):
        self.latch(multiple=3.2)
        self.data['meter_intervals'].extend([
            [self.now-800,self.now-700,'fable',8,'claude:account-b@example.com'],
            [self.now-3200,self.now-3100,'fable',30,'claude:account-b@example.com']])
        self.persist(); result,output=self.soft(1)
        self.assertEqual(result,1)
        self.assertIn('25.4000 points short',output)
        self.assertIn('127.01 more minutes',output)

    def test_plain_non_preparation_cli_still_prints_boxes(self):
        policy=copy.deepcopy(self.policy);policy.pop('preparation');self.persist()
        args=SimpleNamespace(boxes=self.root/'boxes.toml',uncap=None,accept_defaults=False,
                             warn=False,log=False,passive=False,meter_check=False,test_box=None)
        with patch.object(b,'load',return_value=policy), patch.object(self.host,'fetch',return_value={}), \
             patch.object(self.host,'parse',return_value=self.rows),patch.object(self.host,'parse_codex',return_value=[]), \
             patch.object(self.host,'read_log',return_value=[]),patch.object(self.host,'fetch_modal',side_effect=OSError()), \
             redirect_stdout(io.StringIO()) as output:
            self.assertEqual(b.cli(self.host,args,lambda: datetime.fromtimestamp(self.now,self.host.ET)),0)
        self.assertIn('Measured mock',output.getvalue())

    def test_invalid_minutes_cannot_add_credit(self):
        self.persist()
        for value in (0,-1,float('nan'),float('inf')):
            with self.subTest(value=value), self.assertRaises(ValueError):
                self.soft(value)
        self.assertNotIn('soft_credits',json.loads((self.root/'category-ledger.json').read_text()))


class MatrixTransitions(unittest.TestCase):
    def test_failed_release_matrix_retry_keeps_release_preview(self):
        from pathlib import Path
        import tempfile
        from test_usage import cu
        with tempfile.TemporaryDirectory() as tmp:
            host=SimpleNamespace(LOG=Path(tmp)/'log.tsv',guard_lock=cu.guard_lock,read_state=cu.read_state,
                                 atomic_write=cu.atomic_write,desktop_notify=lambda line: True)
            now=b.stamp('2025-11-11T17:00:00-05:00')
            line=human.pack('RELEASED','Fable','new math clears stop','1 of 2 points','released now','nothing needed')
            state={'current':[]}
            with patch.object(b,'matrix_notice',return_value=False),patch.object(b,'thread_notice',return_value='steered'),patch.object(b,'append_alert',return_value=True):
                b.ladder_delivery(host,state,[line],now)
            with patch.object(b,'matrix_notice',return_value=True) as matrix,patch.object(b,'thread_notice',return_value='steered'),patch.object(b,'append_alert',return_value=True):
                b.ladder_delivery(host,state,[],now+61)
            self.assertTrue(matrix.call_args.args[0].startswith('RELEASED  Fable:'))

    def test_partial_matrix_failure_does_not_repeat_successful_stop(self):
        from pathlib import Path
        import tempfile
        from test_usage import cu
        with tempfile.TemporaryDirectory() as tmp:
            host=SimpleNamespace(LOG=Path(tmp)/'log.tsv',guard_lock=cu.guard_lock,read_state=cu.read_state,
                                 atomic_write=cu.atomic_write,desktop_notify=lambda line: True)
            now=b.stamp('2025-11-11T17:00:00-05:00')
            stop='STOP Codex: 60m confirmed 1.35× ≥ 1.3× (4.10 points / 3.90 limit); blocked on every account until human UNCAP.'
            warning='Fable 5m 2.5× crossed (2.60×; 1.10 points / 0.42 planned).'
            state={'current':[warning]}
            with patch.object(b,'matrix_notice',side_effect=[True,False]),patch.object(b,'thread_notice',return_value='steered'),patch.object(b,'append_alert',return_value=True):
                b.ladder_delivery(host,state,[warning,stop],now)
            with patch.object(b,'matrix_notice',return_value=True) as matrix,patch.object(b,'thread_notice',return_value='steered'),patch.object(b,'append_alert',return_value=True):
                b.ladder_delivery(host,state,[],now+1)
            self.assertEqual(matrix.call_count,1)
            self.assertNotIn('STOPPED',matrix.call_args.args[0])
            self.assertIn('WARNING',matrix.call_args.args[0])

    def test_stop_release_each_separate_before_warnings_with_visible_preview(self):
        now=b.stamp('2025-11-11T17:00:00-05:00')
        raw=[('w',2,'Fable 5m 2.5× crossed (2.60×; 1.10 points / 0.42 planned).'),
             ('s',4,'STOP Codex: 60m confirmed 1.35× ≥ 1.3× (4.10 points / 3.90 limit); blocked on every account until human UNCAP.'),
             ('r',1,human.pack('RELEASED','Fable','new math clears stop','1 of 2 points','released now','nothing needed'))]
        text=human.render(raw,now)
        with patch.object(b.subprocess,'run',return_value=SimpleNamespace(returncode=0,stdout='queued',stderr='')) as send:
            self.assertTrue(b.matrix_notice(text))
        bodies=[c.kwargs['input'] for c in send.call_args_list]
        self.assertEqual(len(bodies),3)
        self.assertTrue(bodies[0].startswith('For you: STOPPED  Codex:'))
        self.assertTrue(bodies[1].startswith('For you: RELEASED  Fable:'))
        self.assertIn('WARNING',bodies[2])
        self.assertNotIn('WARNING',bodies[0]);self.assertNotIn('Usage,',bodies[0])
        self.assertNotIn('STOPPED',bodies[2]);self.assertNotIn('RELEASED',bodies[2])
