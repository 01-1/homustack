"""One-file offline admission checks. No real state, transport or credentials."""
import sys
sys.dont_write_bytecode = True
import copy
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import claude_usage_boxes as boxes
import usage_helper_admission as gate


class AdmissionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.home = Path(self.tmp.name)
        self.root = self.home / '.local/state/claude-usage'
        self.root.mkdir(parents=True)
        self.policy_path = self.home / '.config/claude-usage/boxes.toml'
        self.policy_path.parent.mkdir(parents=True)
        text = Path(__file__).with_name('fixtures').joinpath('boxes-preparation-v2.toml').read_text()
        self.policy_path.write_text(text.replace('approved = false', 'approved = true'))
        self.policy = boxes.load(self.policy_path)
        self.at = boxes.stamp('2025-11-12T01:00:00-05:00')
        self.ledger = boxes.empty_ledger()
        rows = [dict(provider=s['provider'], email=s['email'], u7=.2, uf=.1, u5=.1,
                     observed_at=self.at, r7=self.at+100000, rf=self.at+100000)
                for s in self.policy['sources']]
        boxes.observe(self.ledger, rows, self.at - 86400, self.policy)
        boxes.observe(self.ledger, rows, self.at, self.policy)
        start = self.policy['preparation']['start']
        self.ledger['preparation'] = {str(start): dict(at=start, P=dict.fromkeys(boxes.CATEGORIES, 468.),
                                                      C=dict.fromkeys(boxes.CATEGORIES, 702.))}
        self.samples = dict(events=[], token_since=self.at-7200, token_complete=True,
                            token_last_sampled=self.at, token_records=[])
        self.stops = dict(latches={}, released=[], files={})
        for target in ('socket.create_connection', 'urllib.request.urlopen', 'subprocess.run'):
            self.addCleanup(patch.stopall)
            patch(target, side_effect=AssertionError('I/O forbidden')).start()
        self.save()

    def save(self):
        for name, value in (('category-ledger.json', self.ledger), ('passive-rate.json', self.samples),
                            ('hard-stops.json', self.stops)):
            self.write(name, value)
        os.utime(self.policy_path, (self.at-1, self.at-1))

    def write(self, name, value):
        path = self.root / name
        path.write_text(json.dumps(value))
        os.utime(path, (self.at, self.at))

    def check(self):
        return gate.check(self.home, self.at)

    def test_clear_and_strictly_read_only(self):
        before = {str(p): (p.read_bytes(), p.stat().st_mtime_ns) for p in self.home.rglob('*') if p.is_file()}
        result = self.check()
        self.assertEqual((result['allowed'], result['status'], result['reasons']), (True, 'clear', []))
        self.assertEqual(result['coverage']['fresh_sources'], 2)
        self.assertEqual(before, {str(p): (p.read_bytes(), p.stat().st_mtime_ns) for p in self.home.rglob('*') if p.is_file()})

    def test_current_rate_and_recovery_not_history(self):
        factor = self.policy['rate_points_per_million']['codex'] / 1e6
        self.samples['events'] = [[self.at-1, 'codex', 2.15 / factor]]
        self.save()
        self.assertIn('codex_rate_warning', self.check()['reasons'])
        self.samples['events'] = []
        self.save()
        self.write('rate-ladder.json', {'levels': {'codex:5:notify:2': {'fired': True}},
                                       'pending': [{'line': 'old STOP Codex'}], 'current': ['old warning']})
        self.assertTrue(self.check()['allowed'])

    def test_fixed_budget_warning_and_stop(self):
        for amount, expected in ((360, 'codex_fixed_budget_warning'), (468, 'codex_fixed_budget_stop')):
            self.ledger['events'] = [[self.at-7200, 'codex', amount]]
            self.save()
            self.assertIn(expected, self.check()['reasons'])

    def test_category_and_legacy_account_latches_and_human_override(self):
        for latch in ({'pool': 'codex:fixture:week', 'category': 'codex'}, {'pool': 'codex:fixture:week'}):
            self.stops['latches'] = {'test': latch}
            self.save()
            self.assertIn('codex_stop_latch', self.check()['reasons'])
            self.write('hard-continue.json', {'disabled': {'codex': self.at-1}})
            self.assertTrue(self.check()['allowed'])
            (self.root/'hard-continue.json').unlink()

    def test_override_does_not_hide_warnings_or_missing_coverage(self):
        self.write('hard-continue.json', {'disabled': {'codex': self.at-1}})
        self.ledger['events'] = [[self.at-7200, 'codex', 400]]
        self.save()
        self.assertEqual(self.check()['status'], 'hold')
        self.samples['token_complete'] = False
        self.save()
        self.assertEqual(self.check()['status'], 'unknown')

    def test_missing_corrupt_stale_or_future_evidence_denies(self):
        p = self.root/'passive-rate.json'
        for mode in ('missing', 'corrupt', 'stale', 'future'):
            self.save()
            if mode == 'missing': p.unlink()
            elif mode == 'corrupt': p.write_text('{')
            else: os.utime(p, (self.at + (1 if mode == 'future' else -91),)*2)
            self.assertEqual((self.check()['allowed'], self.check()['status']), (False, 'unknown'))

    def test_full_source_coverage_required(self):
        original = copy.deepcopy(self.ledger)
        key = next(k for k in self.ledger['sources'] if k.startswith('codex:'))
        for mode in ('missing', 'stale', 'future', 'carried', 'nan'):
            self.ledger = copy.deepcopy(original)
            if mode == 'missing': del self.ledger['sources'][key]
            elif mode == 'carried': self.ledger['sources'][key]['carried'] = {'codex': 0}
            elif mode == 'nan': self.ledger['sources'][key]['row']['u7'] = float('nan')
            else: self.ledger['sources'][key]['row']['observed_at'] = self.at + (1 if mode == 'future' else -1201)
            self.save()
            self.assertEqual(self.check()['status'], 'unknown')

    def test_no_synthetic_opening_or_incomplete_rate_window(self):
        self.ledger['preparation'] = {}
        self.save()
        self.assertEqual(self.check()['status'], 'unknown')
        self.setUpSnapshot()
        self.samples['token_since'] = self.at-299
        self.save()
        self.assertEqual(self.check()['status'], 'unknown')

    def setUpSnapshot(self):
        start = self.policy['preparation']['start']
        self.ledger['preparation'] = {str(start): dict(at=start, P=dict.fromkeys(boxes.CATEGORIES,468), C=dict.fromkeys(boxes.CATEGORIES,702))}

    def test_63_percent_hour_is_clear(self):
        self.samples['events'] = [[self.at-1, 'codex', 7.56 / (self.policy['rate_points_per_million']['codex']/1e6)]]
        # Spread token events over the hour to keep shorter windows below warning.
        amount = self.samples['events'][0][2] / 60
        self.samples['events'] = [[self.at-60*i-1, 'codex', amount] for i in range(60)]
        self.save()
        self.assertTrue(self.check()['allowed'])

    def test_unknown_latch_and_invalid_override_fail_closed(self):
        self.stops['latches'] = {'bad': {'pool': 'unknown'}}
        self.save()
        self.assertEqual(self.check()['status'], 'unknown')
        self.stops['latches'] = {}
        self.save()
        self.write('hard-continue.json', {'disabled': {'codex': self.at+1}})
        self.assertEqual(self.check()['status'], 'unknown')

    def test_concurrent_change_denies(self):
        original = boxes.warnings
        def changed(*args):
            self.write('hard-stops.json', dict(self.stops, released=['changed']))
            return original(*args)
        with patch.object(boxes, 'warnings', side_effect=changed):
            self.assertEqual(self.check()['status'], 'unknown')

    def test_invalid_epoch_and_mismatched_latch_deny(self):
        self.ledger['forgiven']['codex'] = self.at+1
        self.save()
        self.assertEqual(self.check()['status'], 'unknown')
        self.ledger['forgiven'] = {}
        self.stops['latches'] = {'bad': {'pool': 'codex:fixture:week', 'category': 'fable'}}
        self.save()
        self.assertEqual(self.check()['status'], 'unknown')


    def test_category_flag_echo_and_strict_cli(self):
        for category in boxes.CATEGORIES:
            result = gate.check(self.home, self.at, category)
            self.assertEqual((result['category'], result['status'], result['reasons']), (category, 'clear', []))
        self.assertEqual(self.check()['category'], 'codex')  # schema-1 default unchanged
        self.assertEqual(gate.check(self.home, self.at, 'Fable')['reasons'], ['unsupported_category'])
        seen = []
        with patch.object(gate, 'check', side_effect=lambda category: seen.append(category) or dict(allowed=True, status='clear')), \
                patch('builtins.print'):
            self.assertEqual([gate.main(a) for a in ([], ['--category', 'fable'], ['--category', 'non-fable'])], [0, 0, 0])
            for bad in (['--category'], ['--category=fable'], ['--category', 'claude'], ['--category', 'fable', 'x'], ['fable']):
                self.assertEqual(gate.main(bad), 2)
        self.assertEqual(seen, ['codex', 'fable', 'non-fable'])

    def test_claude_categories_are_separate(self):
        self.stops['latches'] = {'f': {'pool': 'claude:fixture:fable', 'category': 'fable'}}
        self.save()
        check = lambda c: gate.check(self.home, self.at, c)
        self.assertEqual((check('fable')['reasons'], check('non-fable')['status'], check('codex')['status']),
                         (['fable_stop_latch'], 'clear', 'clear'))
        self.stops['latches'] = {'legacy': {'pool': 'claude:fixture:week'}}  # pool-only Claude latch: both categories
        self.save()
        self.assertEqual((check('fable')['reasons'], check('non-fable')['reasons'], check('codex')['status']),
                         (['fable_stop_latch'], ['non-fable_stop_latch'], 'clear'))
        self.write('hard-continue.json', {'disabled': {'fable': self.at-1}})
        self.assertEqual((check('fable')['status'], check('non-fable')['status']), ('clear', 'hold'))

    def test_claude_exhaustion_carried_and_rate(self):
        original = copy.deepcopy(self.ledger)
        for key, source in self.ledger['sources'].items():
            if key.startswith('claude:'):
                source['row']['uf'] = 1.
        self.save()
        self.assertIn('fable_all_accounts_exhausted', gate.check(self.home, self.at, 'fable')['reasons'])
        self.assertNotIn('non-fable_all_accounts_exhausted', gate.check(self.home, self.at, 'non-fable')['reasons'])
        self.ledger = copy.deepcopy(original)
        key = next(k for k in self.ledger['sources'] if k.startswith('claude:'))
        self.ledger['sources'][key]['carried'] = {'fable': 0}
        self.save()
        self.assertEqual((gate.check(self.home, self.at, 'fable')['status'],
                          gate.check(self.home, self.at, 'non-fable')['status']), ('unknown', 'clear'))
        self.ledger = copy.deepcopy(original)
        factor = self.policy['rate_points_per_million']['fable'] / 1e6
        self.samples['events'] = [[self.at-1, 'fable', 2.15 / factor]]
        self.save()
        self.assertIn('fable_rate_warning', gate.check(self.home, self.at, 'fable')['reasons'])
        self.assertEqual(gate.check(self.home, self.at, 'codex')['status'], 'clear')

    def test_claude_meter_text_gauges(self):
        original = copy.deepcopy(self.ledger)
        for key, source in self.ledger['sources'].items():
            source['row'].update(u7=str(source['row']['u7']), u5=str(source['row']['u5'])) if key.startswith('claude:') else None
        self.save()
        self.assertEqual([gate.check(self.home, self.at, c)['status'] for c in ('fable', 'non-fable')], ['clear', 'clear'])
        for bad in ('nan', 'abc'):
            self.ledger = copy.deepcopy(original)
            key = next(k for k in self.ledger['sources'] if k.startswith('claude:'))
            self.ledger['sources'][key]['row']['u7'] = bad
            self.save()
            self.assertEqual(gate.check(self.home, self.at, 'fable')['status'], 'unknown')
        self.ledger = copy.deepcopy(original)
        key = next(k for k in self.ledger['sources'] if k.startswith('codex:'))
        self.ledger['sources'][key]['row']['u7'] = '0.2'  # Codex stays strict, as before
        self.save()
        self.assertEqual(self.check()['status'], 'unknown')

    def test_other_category_epoch_does_not_change_checked_category(self):
        self.ledger['forgiven'] = {'fable': self.at-1}
        self.ledger['cap_restart'] = {'fable': self.at-1}
        self.stops['latches'] = {'n': {'pool': 'claude:fixture:non-fable', 'category': 'non-fable'}}
        self.save()
        self.assertEqual(gate.check(self.home, self.at, 'non-fable')['reasons'], ['non-fable_stop_latch'])
        self.assertEqual(self.check()['status'], 'clear')  # default Codex unaffected (Sol P1)

if __name__ == '__main__':
    unittest.main()
