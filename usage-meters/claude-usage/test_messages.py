"""Human formatting, state deduplication and stale-outbox checks."""
from contextlib import redirect_stdout
from datetime import datetime
import io
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import claude_usage_boxes as b
import claude_usage_messages as human
import test_usage as legacy


class Messages(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.now = b.stamp('2025-11-11T14:20:00-05:00')
        self.desktop, self.thread, self.log = [], [], []
        self.host = SimpleNamespace(LOG=self.root/'log.tsv', guard_lock=legacy.cu.guard_lock,
                                    atomic_write=legacy.cu.atomic_write, read_state=legacy.cu.read_state,
                                    desktop_notify=lambda line: self.desktop.append(line) or True)
        self.stop = 'STOP Codex: 60m confirmed 1.35× ≥ 1.3× (4.10 points / 3.90 limit); blocked on every account until human UNCAP.'
        self.rate = 'Fable 5m 2.5× crossed (2.60×; 1.10 points / 0.42 planned).'
        self.addCleanup(patch.stopall)
        legacy.no_category_dispatch(self)
        patch.object(b, 'matrix_notice', return_value=True).start()
        patch.object(b, 'thread_notice', side_effect=lambda host, line: self.thread.append(line) or 'steered').start()
        def append_alert(line, path):
            # alert_path() keeps a fake LOG's alerts beside it, never in the real alert log.
            self.assertEqual(path, self.root / 'usage-alerts.log')
            self.log.append(line)
            return True
        patch.object(b, 'append_alert', side_effect=append_alert).start()

    def test_single_rate_stop_names_rule_and_base_rate_without_false_end(self):
        output = human.render([('x', 4, self.stop)], self.now)
        self.assertEqual(output, 'STOPPED  Codex: 60-minute rate over 1.3x\n'
                         'Used: 4.10 points vs 3.00 at target rate (last 60 min), 137 target %, stop at 3.90 points\n'
                         'Do: at a terminal run  uncap  (release everything) or  uncap 30  (30 minutes of credit)')
        self.assertNotIn(';', output)
        self.assertNotIn('Usage:', output)

    def test_multiple_events_one_message_with_worst_first(self):
        output = human.render([('rate:fable', 2, self.rate), ('trip:codex', 4, self.stop)], self.now)
        self.assertTrue(output.startswith('Usage, 14:20 ET\n\nSTOPPED  Codex'))
        self.assertIn('\n\nWARNING  Fable:', output)
        self.assertEqual(output.count('Do:'), 2)
        self.assertNotIn(';', output)

    def test_rate_display_uses_target_percent_above_and_below(self):
        self.assertIn('152 target %',human.rate_detail(3.,1.98,15))
        self.assertIn('63 target %',human.rate_detail(5.,7.92,60))
        self.assertNotIn('% over',human.rate_detail(3.,1.98,15))

    def test_warning_has_amount_window_multiple_and_action(self):
        output = human.render([('rate:fable', 2, self.rate)], self.now)
        self.assertIn('WARNING  Fable: 5-minute rate over 2.5x', output)
        self.assertIn('Used: 1.10 points vs 0.42 at target rate (last 5 min), 262 target %', output)
        self.assertNotIn('Ends:', output)
        self.assertNotIn('14:25', output)
        self.assertIn('Do: slow down this category', output)

    def test_account_is_only_detail_inside_a_category_event(self):
        line = 'Claude account@test Fable: 99 of 100 meter points used; 3d 16h 10m until reset. Only 1 points remain; slow down or use another pool.'
        output = human.render([('cap:fable', 3, line)], self.now)
        self.assertTrue(output.startswith('WARNING  Fable:'))
        self.assertNotIn('account@test', output.splitlines()[0])
        self.assertIn('account@test Fable account meter', output)
        self.assertNotIn('see the current box', output)

    def test_same_state_never_repeats_even_after_old_reminder_interval(self):
        events = [('fable:box', 2, self.rate, 30, False)]
        b.deliver_current(self.host, events, self.now)
        b.deliver_current(self.host, events, self.now + 3600)
        self.assertEqual(len(self.desktop), 1)
        self.assertEqual(len(self.log), 1)
        self.assertEqual(self.thread, [])

    def test_state_changes_and_recovery_each_send_once(self):
        events = [('rate:fable', 2, self.rate, 30, False)]
        b.deliver_current(self.host, events, self.now)
        b.deliver_current(self.host, [('rate:fable', 3, self.rate, 30, False)], self.now + 60)
        b.deliver_current(self.host, [], self.now + 120)
        b.deliver_current(self.host, [], self.now + 180)
        self.assertEqual(len(self.desktop), 3)
        self.assertIn('NOTE  Fable: Previous warning ended', self.desktop[-1])
        self.assertIn('Do: nothing needed', self.desktop[-1])

    def test_many_events_are_one_desktop_and_one_thread_message(self):
        events = [('rate:fable', 2, self.rate, 30, False), ('trip:codex', 4, self.stop, 30, True)]
        b.deliver_current(self.host, events, self.now)
        self.assertEqual(len(self.desktop), 1)
        self.assertEqual(len(self.thread), 1)
        self.assertTrue(self.desktop[0].startswith('Usage,'))
        self.assertNotIn('\n', self.thread[0])
        self.assertTrue(all('\n' not in line for line in self.log))

    def test_failed_desktop_retries_current_amount_not_stale_reading(self):
        self.host.desktop_notify = lambda _: False
        events = [('rate:fable', 2, self.rate, 30, False)]
        b.deliver_current(self.host, events, self.now)
        newer = self.rate.replace('1.10 points', '2.20 points')
        self.host.desktop_notify = lambda line: self.desktop.append(line) or True
        b.deliver_current(self.host, [('rate:fable', 2, newer, 30, False)], self.now + 1801)
        self.assertEqual(len(self.desktop), 1)
        self.assertIn('Used: 2.20', self.desktop[0])
        self.assertNotIn('Used: 1.10', self.desktop[0])

    def test_ladder_batch_formats_desktop_but_keeps_thread_log_raw(self):
        state = {}
        b.ladder_delivery(self.host, state, [self.rate, self.stop], self.now)
        self.assertEqual(len(self.desktop), 1)
        self.assertTrue(self.desktop[0].startswith('Usage,'))
        self.assertIn('STOPPED  Codex', self.desktop[0])
        self.assertEqual(self.thread[0], self.rate + ' ' + self.stop)
        self.assertIn('delivery=steered', self.log[0])

    def test_expired_ladder_backlog_replaced_with_one_current_summary(self):
        state = {'pending': [dict(at=self.now-1801, line='Fable old warning', receipts={})], 'current': [self.rate]}
        b.ladder_delivery(self.host, state, [], self.now)
        self.assertEqual(len(self.desktop), 1)
        self.assertNotIn('old warning', self.desktop[0])
        self.assertEqual(self.thread, [self.rate])
        self.assertEqual(state['pending'], [])

    def test_old_ladder_warning_dropped_if_no_current_risk(self):
        state = {'pending': [dict(at=self.now-1801, line='Fable old warning', receipts={})], 'current': []}
        b.ladder_delivery(self.host, state, [], self.now)
        self.assertEqual(self.desktop, [])
        self.assertEqual(self.thread, [])
        self.assertEqual(state['pending'], [])

    def test_failed_recovery_popup_is_persisted_then_retried(self):
        b.deliver_current(self.host, [('rate:fable', 2, self.rate, 30, False)], self.now)
        self.host.desktop_notify = lambda _: False
        b.deliver_current(self.host, [], self.now + 60)
        self.host.desktop_notify = lambda line: self.desktop.append(line) or True
        b.deliver_current(self.host, [], self.now + 90)
        self.assertEqual(len(self.desktop), 2)
        self.assertIn('Previous warning ended', self.desktop[-1])
        b.deliver_current(self.host, [], self.now + 120)
        self.assertEqual(len(self.desktop), 2)

    def test_retry_after_risk_cleared_sends_current_recovery_not_old_stop(self):
        state = {'pending': [dict(at=self.now-60, line=self.stop, receipts={})], 'current': []}
        b.ladder_delivery(self.host, state, [], self.now)
        self.assertEqual(len(self.desktop), 1)
        self.assertIn('NOTE  Codex', self.desktop[0])
        self.assertNotIn('STOPPED', self.desktop[0])
        self.assertIn('recovered', self.thread[0])

    def test_higher_crossed_level_is_first_in_same_check(self):
        low = self.rate.replace('5m 2.5×', '5m 2×')
        high = self.rate.replace('5m 2.5×', '5m 2.75×')
        output = human.render([('low', 2, low), ('high', 2, high)], self.now)
        self.assertLess(output.index('rate over 2.75x'), output.index('rate over 2x'))

    def test_shared_claude_account_meter_is_visible_alone_and_in_batch(self):
        shared = 'Claude example@test 5-hour: 95 of 100 meter points used; 1h until reset.'
        alone = human.render([('shared', 3, shared)], self.now)
        self.assertIn('WARNING  Claude non-Fable:', alone)
        combined = human.render([('shared', 3, shared), ('rate:fable', 2, self.rate)], self.now)
        self.assertIn('Claude non-Fable:', combined)
        self.assertIn('Fable:', combined)
        self.assertEqual(combined.count('Used:'), 2)

    def test_account_out_names_account_limit_reset_and_automatic_fallback(self):
        line = human.pack('OUT', 'Codex', 'account-d@example.com 5-hour limit used up',
                          '100 of 100 points (account 5-hour meter)',
                          'Tue 17:58 ET (in 3h 38m)',
                          'nothing needed; Codex continues on account-c@example.com')
        output = human.render([('account:acct3', 4, line)], self.now)
        self.assertIn('OUT  Codex: account-d@example.com 5-hour limit used up', output)
        self.assertIn('Limit resets: Tue 17:58 ET', output)
        self.assertIn('continues on account-c@example.com', output)
        self.assertNotIn('not available', output)

    def test_no_human_event_with_unknown_amount_or_end(self):
        self.assertEqual(human.render([('account:out', 4, 'Codex: OUT OF USAGE on account x')], self.now), '')
        self.assertEqual(human.render([('x', 2, 'Fable: Usage readings need checking')], self.now), '')

    def test_matrix_failure_does_not_repeat_successful_desktop(self):
        with patch.object(b, 'matrix_notice', return_value=False):
            b.deliver_current(self.host, [('rate:fable', 2, self.rate, 30, False)], self.now)
            b.deliver_current(self.host, [('rate:fable', 2, self.rate, 30, False)], self.now+1801)
        self.assertEqual(len(self.desktop), 1)
        self.assertEqual(len(self.thread), 1)

    def test_multiline_raw_limit_is_logged_on_one_line(self):
        b.deliver_current(self.host, [('account:fable', 3, self.rate+'\nextra detail', 30, False)], self.now)
        self.assertTrue(all('\n' not in line for line in self.log))

    def test_production_copies_each_matrix_batch_in_same_human_form(self):
        # Notices are category-only, so the batch uses category keys.
        self.host.MATRIX_COPY_ALL = True
        events = [('rate:fable', 2, self.rate, 30, False), ('trip:codex', 4, self.stop, 30, False)]
        b.deliver_current(self.host, events, self.now)
        self.assertEqual(len(self.desktop), 1)
        self.assertEqual(len(self.thread), 1)
        self.assertEqual(self.thread[0], 'For you:\n' + self.desktop[0])
        self.assertIn('STOPPED  Codex', self.desktop[0])
        self.assertIn('WARNING  Fable', self.desktop[0])
        b.deliver_current(self.host, events, self.now+1800)
        self.assertEqual(len(self.thread), 1)
        self.assertEqual(len(self.desktop), 1)

    def test_per_account_events_send_no_notice_on_any_channel(self):
        self.host.MATRIX_COPY_ALL = True
        with patch.object(b, 'matrix_notice', return_value=True) as matrix:
            events = [('account:fable', 1, self.rate, 30, False), ('account:codex', 4, self.stop, 30, False),
                      ('missing:claude:x:fable', 3, self.rate, 30, False)]
            b.deliver_current(self.host, events, self.now)
            b.deliver_current(self.host, events, self.now + 1800)
        self.assertEqual((self.desktop, self.thread, self.log), ([], [], []))
        matrix.assert_not_called()
        saved = json.loads((self.root / 'current-notices.json').read_text())
        self.assertFalse(any(k.startswith(('account:', 'missing:')) for k in saved['records']))

    def test_preparation_budget_levels_name_total_and_keep_fixed_close(self):
        reading = dict(category='fable', name='Preparation total (final third reserved)',
                       start=self.now-7200, end=b.stamp('2025-11-13T18:00:00-05:00'),
                       used=160., allowed=205.93, run=True, prep_total=True)
        with patch.object(b, 'spent', return_value=10.2), patch.object(b, 'notice_rate', return_value=7.92):
            for delta in (0, 60, 120, 180):
                events = b.warnings([reading], {}, self.now+delta, {})
                self.assertEqual([(e[0], e[1], e[3], e[4]) for e in events],
                                 [('fable:'+reading['name'], 2, 1800, False)])
                text = human.render([(events[0][0], events[0][1], events[0][2])], self.now+delta)
                self.assertIn('Preparation total (final third reserved): 75% budget level', text)
                self.assertIn('total 160.00 of 205.93 points, 78% of the budget', text)
                self.assertIn('Budget period closes: Thu 18:00 ET', text)
                self.assertNotIn('target rate', text)
                self.assertNotIn('projected', text)
                self.assertNotIn('Ends:', text)

    def test_run_box_usage_warning_and_allowance_stop_name_exact_box(self):
        reading = dict(category='non-fable', name='Measured mock',
                       end=self.now+3600, used=9., allowed=10., run=True)
        for stopped, level in ((False, 'WARNING'), (True, 'STOPPED')):
            text = human.render([('x', 4 if stopped else 3, human.box_notice(reading, self.now, stopped))], self.now)
            self.assertTrue(text.startswith(level+'  Claude non-Fable: Measured mock:'))
            self.assertIn('9.00 of 10.00 points', text)
            self.assertIn('Run box closes: Tue 15:20 ET', text)
            self.assertNotIn('Ends:', text)

    def test_half_used_preparation_note_is_suppressed_without_projection(self):
        reading = dict(category='codex', name='Preparation total', start=self.now-7200,
                       end=self.now+3600, used=50., allowed=100., run=True, prep_total=True)
        with patch.object(b, 'spent', return_value=0):
            events = b.warnings([reading], {}, self.now)
        self.assertEqual(events, [])
        self.assertEqual(reading['used'], 50.)

    def test_preparation_note_pending_retry_is_silently_removed_but_release_survives(self):
        reading = dict(category='codex', name='Preparation total (final third reserved)',
                       start=self.now-7200, end=self.now+3600, used=146.53, allowed=291.27,
                       run=True, prep_total=True, soft_credit=1.1972)
        note = human.box_notice(reading, self.now, level='NOTE')
        path = self.root/'current-notices.json'
        self.host.atomic_write(path, json.dumps({'records': {'codex:'+reading['name']:
            dict(level=1, line=note, at=self.now, receipts={}, trip=False)}}))
        # None is the production pending-retry path; no synthetic recovery notice.
        self.host.GUARD_STATE = self.root/'hard-stops.json'
        self.host.guard_state = lambda path: {'latches': {}}
        b.deliver_current(self.host, None, self.now+1)
        self.assertEqual(self.host.read_state(path)['records'], {})
        b.deliver_current(self.host, [('codex:'+reading['name'], 1, note, 1800, False)], self.now+2)
        self.assertEqual((self.desktop, self.thread, self.log), ([], [], []))
        release = human.pack('RELEASED', 'Codex', 'hard stops cleared', '0 of 1 stops remain',
                             'Released: Tue 14:20 ET', 'nothing needed')
        b.deliver_current(self.host, [('release:codex', 1, release, 1800, False)], self.now+3)
        self.assertIn('RELEASED  Codex', self.desktop[-1])

    def test_reported_sixty_three_percent_case_emits_no_preparation_warning(self):
        reading = dict(category='fable', name='Preparation total', used=19.5, allowed=205.93,
                       start=self.now-3600, end=b.stamp('2025-11-13T18:00:00-05:00'), run=True, prep_total=True)
        with patch.object(b, 'spent', return_value=5.) as spending:
            self.assertEqual(b.warnings([reading], {}, self.now), [])
        spending.assert_not_called()  # Budget notices never estimate a rate or elapsed active time.
        policy = b.load(Path(__file__).parent/'fixtures/boxes-preparation-v2.toml')
        context = dict(R=dict.fromkeys(b.CATEGORIES, 7.92), start=self.now-7200,
                       scale=1., final=False, approved=True, name='Preparation')
        samples = {'events': [[self.now-i*300-1, 'fable', (5/12)*1e6/policy['rate_points_per_million']['fable']]
                              for i in range(12)]}
        with patch.object(b, 'rate_context', return_value=context):
            lines, trips = b.ladder_evaluate(policy, b.empty_ledger(), samples, {}, self.now)
        self.assertEqual(lines, [])
        self.assertEqual(trips, [])

    def test_preparation_warning_levels_unchanged_and_routine_notes_suppressed(self):
        reading = dict(category='codex', name='Preparation total', start=self.now-3600,
                       end=self.now+200000, used=0, allowed=100., run=True, prep_total=True)
        for used, expected in ((49.99, None), (50., None), (74.99, None), (75., 2), (89.99, 2), (90., 3), (100., 3)):
            events = b.warnings([dict(reading, used=used)], {}, self.now)
            self.assertEqual(events[0][1] if events else None, expected)
            if events:
                self.assertNotIn('pace', events[0][2])
                self.assertNotIn('projected', events[0][2])

    def test_preparation_total_cannot_enter_passive_projection_fallback(self):
        reading = dict(category='fable', name='Preparation total', start=self.now-3600,
                       end=self.now+200000, used=19.5, allowed=205.93, run=True, prep_total=True)
        with patch.object(b, 'readings', return_value=[reading]):
            self.assertEqual(b.rate_events({'runs': []}, {}, {}, self.now), [])

    def test_rate_notice_uses_full_precision_base_and_actual_box(self):
        line = ('STOP Fable: 5m confirmed 3.15× ≥ 3× (2.00 points / 2.90 limit); blocked. '
                'Target rate: 0.635 points; Box: Mock scale 2. Soft credit: 1.0000 points.')
        text = human.render([('stop', 4, line)], self.now)
        self.assertIn('Mock scale 2: 5-minute rate over 3x', text)
        self.assertIn('2.00 points vs 0.64 at target rate', text)
        self.assertIn('157 target %, after 1.0000 points soft credit', text)
        self.assertIn('stop at 2.90 points', text)
        self.assertNotIn('Ends:', text)

    def test_rate_back_below_reports_target_percent(self):
        line = ('Fable 15m rate back below warning (1.00 points / 2.00 planned). Nothing needed. '
                'Target rate: 2 points; Box: Preparation.')
        text = human.render([('note', 1, line)], self.now)
        self.assertTrue(text.startswith('NOTE  Fable: Preparation: 15-minute rate back below warning'))
        self.assertIn('50 target %', text)
        self.assertIn('Do: nothing needed', text)
        self.assertNotIn('Ends:', text)

    def test_display_rate_reads_frozen_horizon_scale_and_final_without_mutation(self):
        import copy
        policy = b.load(Path(__file__).parent/'fixtures/boxes-preparation-v2.toml')
        policy['preparation']['active_hours'] = 26
        prep = policy['preparation']
        run = policy['runs'][0]
        run['scale'] = 2
        final = policy['runs'][1]
        ledger = {'preparation': {str(prep['start']): {'P': {'fable': 205.93}}},
                  'runs': {b.run_id(final): {'allow': {'fable': 32.5}}}}
        original = copy.deepcopy(ledger)
        self.assertAlmostEqual(b.notice_rate(policy, ledger, 'fable', run['start']+60), 205.93/26*2)
        self.assertAlmostEqual(b.notice_rate(policy, ledger, 'fable', run['end']+60), 205.93/26)
        self.assertAlmostEqual(b.notice_rate(policy, ledger, 'fable', final['start']+60), 10)
        self.assertIsNone(b.notice_rate(policy, {}, 'fable', self.now))
        self.assertEqual(ledger, original)

    def test_real_ladder_output_carries_box_and_base_without_changing_trip_math(self):
        policy = b.load(Path(__file__).parent/'fixtures/boxes-preparation-v2.toml')
        policy['approved'] = True
        ledger = b.empty_ledger()
        rows = [dict(provider=s['provider'], email=s['email'], u7=.2, uf=.1)
                for s in policy['sources']]
        b.observe(ledger, rows, self.now-7200, policy)
        b.observe(ledger, rows, self.now, policy)
        ledger['preparation'] = {str(policy['preparation']['start']):
                                dict(at=policy['preparation']['start'], C=dict.fromkeys(b.CATEGORIES, 702.),
                                     P=dict.fromkeys(b.CATEGORIES, 468.))}
        ledger['meter_intervals'] = [[self.now-59, self.now-1, 'fable', 5., 'claude:account-b@example.com']]
        samples = {'events': [[self.now-1, 'fable', 5e6/policy['rate_points_per_million']['fable']]]}
        lines, trips = b.ladder_evaluate(policy, ledger, samples, {}, self.now)
        rate_lines = [line for line in lines if 'Fable 5m' in line or 'Fable: 5m' in line]
        self.assertEqual(len(rate_lines), 4)  # Three fixed notifications plus the same confirmed stop.
        self.assertTrue(all('Target rate: 1 points; Spend:' in line and 'Box: Preparation.' in line for line in rate_lines))
        trip = next(item for item in trips if item['name']=='Preparation: 5-minute rate')
        self.assertEqual(trip['allowed'], 3.)
        self.assertEqual(trip['used'], 4.)  # Existing conservative meter rounding is unchanged.
        rendered = human.render([(str(i), 2, line) for i,line in enumerate(rate_lines)], self.now)
        self.assertIn('Preparation: 5-minute rate', rendered)
        self.assertIn('400 target %', rendered)
        self.assertNotIn('Ends:', rendered)

    def test_small_rate_percentages_use_unrounded_amounts(self):
        line = ('Fable 5m 2.75× crossed (3.00×; 0.00 points / 0.00 planned).' +
                human.rate_annotation('Preparation', .003, .001))
        text = human.render([('small', 2, line)], self.now)
        self.assertIn('0.003 points vs 0.001 at target rate', text)
        self.assertIn('300 target %', text)

    def test_box_stop_and_old_pending_stop_use_short_human_release_commands(self):
        reading = dict(category='fable', name='Preparation total', used=21., allowed=20.,
                       run=True, end=self.now+3600)
        legacy_line = human.pack('STOPPED', 'Fable', 'Preparation total: cap reached',
            '21 of 20 points', 'stays blocked until released',
            'at a terminal run  claude-usage --uncap fable  and type UNCAP')
        for line in (human.box_notice(reading, self.now, True), legacy_line):
            text = human.render([('stop', 4, line)], self.now)
            self.assertIn('Do: '+human.release_action(), text)
            self.assertNotIn('--uncap', text)
            self.assertNotIn('type UNCAP', text)

    def test_soft_shortfall_action_preserves_required_credit_and_every_category_scope(self):
        for action in ('add at least 42.5 more minutes with --soft-uncap', human.release_action(42.5)):
            line = human.pack('STOPPED', 'Codex', 'soft credit added, still blocked',
                              '0.5 points short', 'stays blocked until credit covers overage', action)
            text = human.render([('short', 4, line)], self.now)
            self.assertIn('uncap 42.5', text)
            self.assertIn('at least 42.5 more minutes', text)
            self.assertIn('every category', text)
            self.assertNotIn('--soft-uncap', text)

    def test_released_notice_needs_no_release_command(self):
        line = human.pack('RELEASED', 'Codex', 'soft credit added, requests resumed',
                          '5 soft-credit points', 'Released: Tue 14:20 ET', 'nothing needed')
        self.assertIn('Do: nothing needed', human.render([('released', 1, line)], self.now))

    def test_preparation_recovery_does_not_relabel_recovery_time_as_budget_close(self):
        reading = dict(category='fable', name='Preparation total', used=17.5, allowed=205.93,
                       run=True, prep_total=True, end=b.stamp('2025-11-13T18:00:00-05:00'))
        line = human.box_notice(reading, self.now)
        b.deliver_current(self.host, [('rate:fable', 2, line, 900, False)], self.now)
        b.deliver_current(self.host, [], self.now+60)
        text = self.desktop[-1]
        self.assertIn('Previous warning ended: Preparation total', text)
        self.assertIn('Recovered: Tue 14:21 ET', text)
        self.assertNotIn('Budget period closes: Tue', text)

    def test_warning_to_budget_note_recovers_without_routine_note(self):
        reading = dict(category='fable', name='Preparation total', used=80., allowed=100.,
                       run=False, prep_total=True, end=self.now+7200)
        warn = human.box_notice(reading, self.now)
        b.deliver_current(self.host, [('budget:fable', 2, warn, 900, False)], self.now)
        reading['used'] = 60.
        note = human.box_notice(reading, self.now+60, level='NOTE')
        with patch.object(b, 'matrix_notice', return_value=False):
            b.deliver_current(self.host, [('budget:fable', 1, note, 900, False)], self.now+60)
        state = self.host.read_state(self.root/'current-notices.json')['records']
        self.assertIn('budget:fable:ended', state)
        self.assertNotIn('budget:fable', state)
        b.deliver_current(self.host, [('budget:fable', 1, note, 900, False)], self.now+90)
        self.assertEqual(sum('Previous warning ended' in line for line in self.desktop), 1)

    def test_passive_projection_keeps_cap_forecast_and_named_run_close(self):
        policy = b.load(Path(__file__).parent/'fixtures/boxes-v2.toml')
        run = policy['runs'][0]
        now = run['start']+600
        reading = dict(category='fable', name=run['name'], used=1., allowed=20., run=True,
                       start=run['start'], end=run['end'])
        ledger = {'forgiven': {}, 'sources': {}}
        samples = {'events': [[now-5, 'fable', 10e6/policy['rate_points_per_million']['fable']]]}
        with patch.object(b, 'readings', return_value=[reading]):
            events = b.rate_events(policy, ledger, samples, now)
        self.assertEqual(len(events), 1)
        text = human.render([(events[0][0], events[0][1], events[0][2])], now)
        self.assertIn(run['name'] + ': Five-minute rate projection', text)
        self.assertIn('projected to exhaust', text)
        self.assertIn('before close', text)
        self.assertIn('Run box closes:', text)


if __name__ == '__main__':
    unittest.main()
