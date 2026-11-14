import json
import unittest
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import usage_dashboard as ud


class Fake(SimpleNamespace):
    pass


def fake_cu(claude=None, codex=None):
    def fetch(url='claude', key=None):
        if url == 'codex':
            if isinstance(codex, Exception): raise codex
            return codex
        if isinstance(claude, Exception): raise claude
        return claude
    return Fake(ET=ZoneInfo('America/New_York'), CODEX_URL='codex', CODEX_KEY=None, fetch=fetch,
                parse=lambda data, excluded_accounts=None: data, parse_codex=lambda data: data,
                when=lambda e: 'R' if e else '-')


class Tests(unittest.TestCase):
    def test_rows_and_percentages(self):
        cu = fake_cu([dict(provider='claude', email='a@x', status='active', u5=0.19, r5=4e9, u7=0.96, r7=4e9, uf=None, rf=None)],
                     [dict(provider='codex', email='<b>@x', status='error', u5=None, r5=None, u7=1.0, r7=4e9)])
        rows, errors = ud.collect(cu)
        self.assertEqual(errors, [])
        out = ud.page(rows, errors, cu)
        self.assertIn('19%', out); self.assertIn('96%', out); self.assertIn('100%', out)
        self.assertIn('&lt;b&gt;@x', out); self.assertNotIn('<b>@x', out)
        self.assertIn('class=hi', out)

    def test_bad_signal_strings_do_not_crash(self):
        cu = fake_cu([dict(provider='claude', email='a@x', status='active', u5='', r5='4e9', u7='0.5', r7='4e9', uf='nan', rf=None)], [])
        rows, _ = ud.collect(cu)
        out = ud.page(rows, [], cu)
        self.assertIn('50%', out)

    def test_rates_table(self):
        snap = dict(context='Preparation', windows_minutes=[5, 15], categories=[
            dict(category='fable', label='Fable', target_per_hour=2.0, windows={
                '5': dict(target_percent=250.0, status='ok'), '15': dict(target_percent=None, status='incomplete', reason='window_coverage_incomplete')}),
            dict(category='outside-api', label='Outside APIs', target_per_hour=1.0, windows={
                '5': dict(target_percent=0.0, status='ok'), '15': dict(target_percent=120.0, status='ok')})])
        out = ud.rates_table(snap, {5: (2.0, 3.0), 15: (1.5, 2.0)})
        self.assertIn('<th>5m</th><th>15m</th>', out)
        self.assertIn('250%', out); self.assertIn('class=mid', out)  # 250 >= warn 200, < stop 300
        self.assertIn('>inc<', out); self.assertIn('window_coverage_incomplete', out)
        self.assertIn('APIs', out); self.assertIn('120%', out)

    def test_totals_use_policy_units(self):
        import importlib.util, os
        spec = importlib.util.spec_from_file_location('bx', os.path.expanduser('~/.local/bin/claude_usage_boxes.py'))
        bx = importlib.util.module_from_spec(spec); spec.loader.exec_module(bx)
        policy = dict(sources=[dict(provider='claude', email='acct1@x', weight=1, fable_banked=2, weekly_banked=1),
                               dict(provider='claude', email='pro@x', weight=0.1),
                               dict(provider='codex', email='acct2@x', weight=1, banked=2),
                               dict(provider='codex', email='old@x', weight=0.5)])
        rows = [dict(provider='claude', email='acct1@x', u7='0.5', uf='0.4', nf_points=None, r7=2e9),
                dict(provider='claude', email='pro@x', u7=1.0, uf=None, nf_points=None, r7=2e9),
                dict(provider='codex', email='acct2@x', u7=0.75, r7=2e9),
                dict(provider='codex', email='old@x', u7=1.0, r7=10)]  # weekly window already reset
        t = {x['category']: x for x in ud.totals(policy, rows, bx, 1e9)}
        self.assertEqual((t['fable']['used'], t['fable']['total']), (40., 410.))  # 100*1*(1+2+1) + 100*0.1
        self.assertEqual(t['fable']['missing'], ['pro'])
        self.assertEqual((t['non-fable']['used'], t['non-fable']['total']), (60., 210.))  # 200*.5-100*.4
        self.assertEqual((t['codex']['used'], t['codex']['total'], t['codex']['missing']), (75., 350., ['old']))
        out = ud.totals_table(list(t.values()))
        self.assertIn('10%', out); self.assertIn('40/410', out); self.assertIn('no figure: old', out)

    def bx(self):
        import importlib.util, os
        spec = importlib.util.spec_from_file_location('bx', os.path.expanduser('~/.local/bin/claude_usage_boxes.py'))
        bx = importlib.util.module_from_spec(spec); spec.loader.exec_module(bx)
        return bx

    def test_rebase_from_ledger(self):
        bx = self.bx()
        policy = dict(sources=[dict(provider='claude', email='acct1@x', weight=1, fable_banked=2, weekly_banked=1),
                               dict(provider='claude', email='pro@x', weight=0.1),
                               dict(provider='codex', email='acct2@x', weight=1, banked=2)])
        ledger = dict(sources={'claude:acct1@x': dict(time=110., row=dict(provider='claude', u7=0.45, uf=0.38, nf_points=None, r7=2e9)),
                               'claude:pro@x': dict(time=110., row=dict(provider='claude', u7=0.5, uf=0.2, nf_points=None, r7=2e9),
                                                    carried=['fable', 'non-fable']),
                               'codex:acct2@x': dict(time=90., row=dict(provider='codex', u7=0.83, r7=2e9))},
                      events=[[50., 'fable', 9.], [110., 'fable', 3.]])
        b = ud.make_baseline(policy, ledger, bx, 120.)
        self.assertEqual((b['at'], b['marker']), (120., [110., 'fable', 3.]))
        # pro's readings are carried from before a reset: nothing left, listed missing
        self.assertEqual(b['remaining'], {'fable': 362., 'non-fable': 148., 'codex': 217.})
        self.assertEqual(b['missing'], {'fable': ['pro'], 'non-fable': ['pro'], 'codex': []})
        # Sol r6: a run that captured 100 and stalled commits Codex after the rebase.
        ledger['events'].append([100., 'codex', 10.])
        ledger['events'].append([130., 'fable', 2.])
        saved = json.loads(json.dumps(b))  # as read back from baseline.json
        t = {x['category']: x for x in ud.since_baseline(saved, ledger, bx, 300.)}
        self.assertEqual((t['codex']['used'], t['fable']['used'], t['fable']['total']), (10., 2., 362.))
        out = ud.totals_table(list(t.values()), 'Thu 07:28')
        self.assertIn('Since Thu 07:28', out); self.assertIn('no figure at rebase: pro', out)
        # Age pruning that keeps the marker is fine; losing it is a visible error.
        ledger['events'] = ledger['events'][1:]
        self.assertEqual(ud.since_baseline(b, ledger, bx, 300.)[0]['used'], 2.)
        ledger['events'] = ledger['events'][1:]
        with self.assertRaises(ValueError):
            ud.since_baseline(b, ledger, bx, 300.)

    def test_empty_ledger_marker(self):
        bx = self.bx()
        b = ud.make_baseline(dict(sources=[]), dict(sources={}, events=[]), bx, 1.)
        self.assertIsNone(b['marker'])
        self.assertEqual(ud.since_baseline(b, dict(events=[[0., 'codex', 4.]]), bx, 2.)[2]['used'], 4.)

    def test_rebase_counts_raw_events_not_override_exempt(self):
        ledger = dict(events=[[150., 'fable', 7.5]], override_periods=[['fable', 100., 200.]])
        self.assertEqual(ud.spent_since(ledger, 'fable', None), 7.5)

    def test_bad_baseline_is_an_error_not_a_crash(self):
        bx = self.bx()
        for bad in (dict(at=1., remaining={'fable': 1., 'non-fable': 1., 'codex': 1.}, missing={}),  # no marker
                    dict(at=1., remaining={'fable': '5'}, missing={}, marker=None), dict(at='x', remaining={}, missing={}),
                    dict(at=1., remaining={'fable': 1., 'non-fable': 1., 'codex': float('nan')}, missing={})):
            with self.assertRaises(ValueError):
                ud.since_baseline(bad, dict(events=[]), bx, 2.)

    def test_estimate_shown_when_producer_has_no_figure(self):
        cell = dict(target_percent=None, status='incomplete', reason='window_coverage_incomplete')
        out = ud.rate_cell(cell, 150., 200., (33.4, 0.))
        self.assertIn('~33%', out); self.assertIn('class=est', out); self.assertIn('window_coverage_incomplete', out)
        out = ud.rate_cell(dict(target_percent=10., status='ok'), 150., 200., (99., 0.98))
        self.assertIn('>10%<', out); self.assertNotIn('~', out); self.assertIn('soft credit 0.98', out)
        self.assertIn('>inc<', ud.rate_cell(cell, 150., 200.))

    def test_rates_unreadable(self):
        bad = Fake(snapshot=lambda: (_ for _ in ()).throw(ValueError('x')))
        self.assertEqual(ud.collect_rates(bad), (None, ['Target rates: unreadable (ValueError)']))
        self.assertIn('none', ud.rates_table(None, {5: (2.0, 3.0)}))

    def test_uncap_guard_and_run(self):
        import threading
        calls = []
        def uncap_main(argv, stdin, stdout):
            self.assertTrue(stdin.isatty() and stdout.isatty())
            calls.append(argv); print('Codex: released', file=stdout); return 0
        lock, o = threading.Lock(), 'http://127.0.0.1:8770'
        ok = dict(minutes=['5'], token=['t'])
        self.assertEqual(ud.run_uncap(ok, None, o, 't', uncap_main, lock)[0], 403)
        self.assertEqual(ud.run_uncap(ok, 'http://evil', o, 't', uncap_main, lock)[0], 403)
        self.assertEqual(ud.run_uncap(dict(minutes=['5'], token=['x']), o, o, 't', uncap_main, lock)[0], 403)
        self.assertEqual(ud.run_uncap(dict(minutes=['60'], token=['t']), o, o, 't', uncap_main, lock)[0], 400)
        self.assertEqual(calls, [])
        status, msg = ud.run_uncap(ok, o, o, 't', uncap_main, lock)
        self.assertEqual((status, calls), (200, [['5']])); self.assertIn('Codex: released', msg)
        with lock:
            self.assertEqual(ud.run_uncap(ok, o, o, 't', uncap_main, lock)[0], 409)
        self.assertEqual(len(calls), 1)

    def test_page_has_buttons_and_token(self):
        out = ud.page([], [], fake_cu([], []), token='tok', last=('07:00:00', 'uncap 1 (exit 0):\nFable: skipped'))
        for m in ('1', '5', '15'):
            self.assertIn(f'value={m}', out)
        self.assertIn('value="tok"', out); self.assertIn('Fable: skipped', out)

    def test_past_reset_is_not_shown_as_current(self):
        cu = fake_cu([], [dict(provider='codex', email='c@x', status='error', u5=0.78, r5=1, u7=1.0, r7=4e9)])
        rows, errors = ud.collect(cu)
        out = ud.page(rows, errors, cu)
        self.assertNotIn('78%', out); self.assertIn('window reset', out); self.assertIn('100%', out)

    def test_one_proxy_down_still_shows_other(self):
        cu = fake_cu(OSError('down'), [dict(provider='codex', email='c@x', status='active', u5=0.5, r5=4e9, u7=0.2, r7=4e9)])
        rows, errors = ud.collect(cu)
        self.assertEqual(len(rows), 1)
        self.assertEqual(errors, ['Claude proxy: unreadable (OSError)'])
        self.assertIn('Claude proxy: unreadable', ud.page(rows, errors, cu))


if __name__ == '__main__':
    unittest.main()
