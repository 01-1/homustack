"""Preparation reserve and immutable rate-ladder regressions; no live traffic."""
import copy
from datetime import datetime
import io
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from contextlib import redirect_stdout
import unittest

import claude_usage_boxes as b
import test_categories as fixtures


class LadderTests(unittest.TestCase):
    ledger = fixtures.Categories.ledger
    record = fixtures.Categories.record
    assertAuthUntouched = fixtures.Categories.assertAuthUntouched

    def setUp(self):
        fixtures.Categories.setUp(self)
        self.policy = b.load(Path(__file__).parent / 'fixtures/boxes-preparation-v2.toml')
        self.policy['approved'] = True
        for run in self.policy['runs']:
            run['approved'] = True
        self.data = self.ledger()
        self.record(self.data)
        prep = self.policy['preparation']
        # R=12 points/hour gives clear synthetic thresholds; tests below also
        # exercise the real snapshot capacities/precision gates.
        self.data['preparation'] = {str(prep['start']): dict(
            at=prep['start'], C=dict.fromkeys(b.CATEGORIES, 702.),
            P=dict.fromkeys(b.CATEGORIES, 468.), resets=dict(fable=3, **{'non-fable': 0}, codex=2))}
        self.run = b.active_run(self.policy, self.now)
        self.data['runs'][b.run_id(self.run)] = dict(allow=dict(self.run['allow']), baseline=self.run['start'])
        self.state = {}

    def sample(self, category, minutes, multiple):
        rate = b.rate_context(self.policy, self.data, self.now)['R'][category]
        amount = rate * minutes / 60 * multiple
        factor = self.policy['rate_points_per_million'][category]
        return {'events': [[self.now - 1, category, amount / factor * 1e6]]}

    def confirmed(self, category, minutes, multiple):
        rate = b.rate_context(self.policy, self.data, self.now)['R'][category]
        source = next(s for s in self.policy['sources'] if s['weight'] == 1 and (s['provider'] == 'codex') == (category == 'codex'))
        amount = rate * minutes / 60 * multiple + source['weight'] * (3 if category == 'non-fable' else 1)
        self.data['meter_intervals'] = [[self.now - minutes * 60 + 1, self.now - 1,
                                         category, amount, source['provider'] + ':' + source['email']]]

    def test_fixed_levels_and_stop_defaults_match_vi(self):
        self.assertEqual(b.RATE_LADDER, ((5, (2., 2.5, 2.75), 3.),
                                         (15, (1.5, 1.75, 1.875), 2.),
                                         (30, (1.25, 1.35, 1.45), 1.5),
                                         (60, (1.1, 1.2, 1.25), 1.3)))

    def test_raised_stops_extend_at_previous_final_spacing(self):
        self.assertEqual(b.notify_levels(5, 4), (2, 2.5, 2.75, 3, 3.25, 3.5, 3.75))
        self.assertEqual(b.notify_levels(15, 2.5), (1.5, 1.75, 1.875, 2, 2.125, 2.25, 2.375))
        self.assertEqual(b.notify_levels(30, 1.7), (1.25, 1.35, 1.45, 1.5, 1.55, 1.6, 1.65))
        self.assertEqual(b.notify_levels(60, 1.5), (1.1, 1.2, 1.25, 1.3, 1.35, 1.4, 1.45))

    def test_lowered_stop_does_not_change_fixed_notification_definition(self):
        self.assertEqual(b.notify_levels(5, 2.6), (2, 2.5, 2.75))
        self.policy['preparation']['stops']['5'] = 2.6
        self.confirmed('fable', 5, 4)
        lines, trips = b.ladder_evaluate(self.policy, self.data, {}, self.state, self.now)
        self.assertTrue(any('5m 2.5× crossed' in line for line in lines))
        self.assertFalse(any('5m 2.75× crossed' in line for line in lines))
        self.assertTrue(trips)

    def test_notifications_cannot_be_added_by_config(self):
        text = Path(__file__).with_name('boxes.proposed.toml').read_text()
        path = self.root / 'invalid.toml'
        path.write_text(text.replace('active_hours = 26', 'active_hours = 26\nnotify_levels = [10]'))
        with self.assertRaisesRegex(ValueError, 'notify levels are fixed'):
            b.load(path)

    def test_scale_weighted_hours_are_thirty_nine(self):
        self.assertEqual(self.policy['preparation']['active_hours'], 9 + 12 + 12 + 6)
        self.assertEqual(b.rate_context(self.policy, self.data, self.now)['R']['fable'], 468 / 39)

    def test_c_p_and_reserve_frozen_not_recomputed_after_spend(self):
        self.data.pop('preparation')
        original = copy.deepcopy(b.preparation_snapshot(self.policy, self.data, self.now))
        self.assertEqual(original['resets'], dict(fable=3, **{'non-fable': 0}, codex=2))
        self.assertAlmostEqual(original['P']['fable'], original['C']['fable'] * 2 / 3)
        self.rows[0]['uf'] += .1
        self.record(self.data, at=self.now + 60)
        self.assertEqual(b.preparation_snapshot(self.policy, self.data, self.now + 60), original)

    def test_prep_total_counts_spend_even_after_human_rate_release(self):
        at = self.policy['preparation']['start']
        self.data['events'] = [[at - 1, 'fable', 100], [at + 1, 'fable', 4], [self.now - 1, 'fable', 3]]
        self.data['forgiven']['fable'] = self.now
        reading = b.prep_readings(self.policy, self.data, self.now)[0]
        self.assertEqual(reading['used'], 7)
        self.assertEqual(reading['allowed'], 468)

    def test_prep_total_is_a_real_category_hard_box(self):
        start = self.policy['preparation']['start']
        self.data['events'] = [[start + 1, 'codex', 469]]
        items = b.prep_readings(self.policy, self.data, self.now)
        b.enforce(self.host, self.policy, self.data, items, self.now)
        guard = self.host.guard_state(self.host.GUARD_STATE)
        self.assertEqual({item['category'] for item in guard['latches'].values()}, {'codex'})

    def test_ladder_replaces_rolling_but_preserves_rehearsal_total(self):
        items = b.readings(self.policy, self.data, self.now)
        self.assertEqual(len(items), 6)
        self.assertFalse(any(r['name'].startswith('last ') for r in items))
        run_items = [r for r in items if not r.get('prep_total')]
        self.assertEqual([r['allowed'] for r in run_items], [20, 10, 30])
        later = self.run['end'] + 60
        self.record(self.data, at=later)
        self.assertEqual(len(b.readings(self.policy, self.data, later)), 3)

    def test_rolling_caps_return_after_final_run_ends(self):
        later = self.policy['preparation']['final_end'] + 60
        self.record(self.data, at=later)
        self.assertFalse(b.preparation_active(self.policy, later))
        self.assertEqual(len(b.readings(self.policy, self.data, later)), 12)

    def test_scale_changes_rate_without_increasing_prep_or_run_budget(self):
        for scale in (1, 2, 4):
            self.run['scale'] = scale
            context = b.rate_context(self.policy, self.data, self.now)
            self.assertEqual(context['R']['fable'], 12 * scale)
            self.assertEqual(self.data['preparation'][str(self.policy['preparation']['start'])]['P']['fable'], 468)
            self.assertEqual(b.readings(self.policy, self.data, self.now)[0]['allowed'], 20)
        after = self.run['end'] + 60
        self.record(self.data, at=after)
        self.assertEqual(b.rate_context(self.policy, self.data, after)['R']['fable'], 12)

    def test_each_notify_level_fires_on_crossing_once(self):
        for multiple, count in ((2, 1), (2.5, 1), (2.75, 1), (2.8, 0)):
            samples = self.sample('fable', 5, multiple)
            lines, trips = b.ladder_evaluate(self.policy, self.data, samples, self.state, self.now)
            self.assertEqual(len(lines), count)
            self.assertEqual(trips, [])

    def test_jumping_multiple_levels_keeps_them_all(self):
        lines, trips = b.ladder_evaluate(self.policy, self.data, self.sample('fable', 5, 3), self.state, self.now)
        self.assertEqual(len(lines), 3)
        self.assertEqual(trips, [])  # estimates never stop

    def test_notifications_rearm_only_after_full_window_below_lowest(self):
        levels = {}
        self.assertTrue(b.level_crossing(levels, 'x', 2.5, 2.5, 2, 5, self.now, 'same'))
        self.assertFalse(b.level_crossing(levels, 'x', 1, 2.5, 2, 5, self.now + 1, 'same'))
        self.assertFalse(b.level_crossing(levels, 'x', 2.5, 2.5, 2, 5, self.now + 299, 'same'))
        self.assertFalse(b.level_crossing(levels, 'x', 1, 2.5, 2, 5, self.now + 300, 'same'))
        self.assertFalse(b.level_crossing(levels, 'x', 1, 2.5, 2, 5, self.now + 599, 'same'))
        self.assertFalse(b.level_crossing(levels, 'x', 1, 2.5, 2, 5, self.now + 600, 'same'))
        self.assertTrue(b.level_crossing(levels, 'x', 2.5, 2.5, 2, 5, self.now + 601, 'same'))

    def test_confirmed_each_window_can_stop_and_estimates_cannot(self):
        for minutes, _, stop in b.RATE_LADDER:
            with self.subTest(minutes=minutes):
                self.confirmed('codex', minutes, stop)
                lines, trips = b.ladder_evaluate(self.policy, self.data, {}, {}, self.now)
                self.assertEqual(len(trips), 1)
                self.assertEqual(trips[0]['category'], 'codex')
                self.assertTrue(any(line.startswith('STOP') for line in lines))
                self.data['meter_intervals'] = []
                _, trips = b.ladder_evaluate(self.policy, self.data, self.sample('codex', minutes, stop * 10), {}, self.now)
                self.assertEqual(trips, [])

    def test_rate_stop_latches_and_speaks_the_category(self):
        self.confirmed('fable', 5, 3)
        _, trips = b.ladder_evaluate(self.policy, self.data, {}, {}, self.now)
        b.enforce(self.host, self.policy, self.data, trips, self.now)
        guard = fixtures.cu.guard_state(self.host.GUARD_STATE)
        self.assertEqual({v['category'] for v in guard['latches'].values()}, {'fable'})
        self.assertEqual(len(self.spoken), 1)
        self.assertRegex(self.spoken[0], r'^\d+ minute, \d+ percent, Fable\.$')
        self.assertAuthUntouched()

    def test_five_minute_precision_gate_disables_non_fable_at_small_rate(self):
        context = dict(R=dict(fable=6, **{'non-fable': 1}, codex=6), final=False)
        self.assertTrue(b.five_minute_stop(self.policy, context, 'fable'))
        self.assertFalse(b.five_minute_stop(self.policy, context, 'non-fable'))
        self.assertTrue(b.five_minute_stop(self.policy, context, 'codex'))

    def test_confirmed_intervals_cannot_assign_ten_minutes_to_last_minute(self):
        self.data['meter_intervals'] = [[self.now - 600, self.now, 'codex', 100, 'source']]
        self.assertEqual(b.confirmed_window(self.data, 'codex', self.now - 300, self.now), 0)
        self.assertEqual(b.confirmed_window(self.data, 'codex', self.now - 900, self.now), 100)

    def test_gaps_disable_rate_stops_without_suppressing_estimate_alerts(self):
        self.confirmed('fable', 5, 3)
        self.data['gaps'] = [[self.now - 60, 'fable', 'source']]
        lines, trips = b.ladder_evaluate(self.policy, self.data, {}, {}, self.now)
        self.assertEqual(trips, [])
        self.assertEqual(len(lines), 3)

    def test_dry_run_logs_would_stop_without_latching(self):
        self.policy['preparation']['rate_stops_dry_run'] = True
        self.confirmed('codex', 5, 3)
        lines, trips = b.ladder_evaluate(self.policy, self.data, {}, self.state, self.now)
        self.assertEqual(trips, [])
        self.assertTrue(any('WOULD STOP' in line for line in lines))
        lines, _ = b.ladder_evaluate(self.policy, self.data, {}, self.state, self.now + 1)
        self.assertFalse(any('WOULD STOP' in line for line in lines))
        self.assertFalse(self.host.GUARD_STATE.exists())

    def test_final_ladder_notify_only_even_at_hundred_times_rate(self):
        final = self.policy['runs'][1]
        self.now = final['start'] + 1800
        self.record(self.data)
        self.data['runs'][b.run_id(final)] = dict(allow=dict.fromkeys(b.CATEGORIES, 100), baseline=final['start'])
        context = b.rate_context(self.policy, self.data, self.now)
        self.assertTrue(context['final'])
        self.assertAlmostEqual(context['R']['fable'], 100 / 3.25)
        self.confirmed('fable', 5, 100)
        lines, trips = b.ladder_evaluate(self.policy, self.data, {}, {}, self.now)
        self.assertTrue(lines)
        self.assertEqual(trips, [])
        self.assertFalse(any('STOP' in line for line in lines))

    def test_human_uncap_restarts_rate_windows_not_preparation_total(self):
        self.confirmed('codex', 5, 3)
        _, trips = b.ladder_evaluate(self.policy, self.data, {}, self.state, self.now)
        self.assertEqual(len(trips), 1)
        self.data['forgiven']['codex'] = self.now
        lines, trips = b.ladder_evaluate(self.policy, self.data, {}, self.state, self.now)
        self.assertEqual(trips, [])
        self.assertEqual(lines, [])

    def test_summary_contains_h_c_p_r_resets_and_precision(self):
        self.data.pop('preparation')
        text = '\n'.join(b.preparation_summary(self.policy, self.data, self.now))
        for required in ('H=39', 'C=', 'P=', 'R=', 'banked resets=3', 'banked resets=2', 'notify-only'):
            self.assertIn(required, text)

    def test_all_new_levels_are_one_thread_message_per_tick(self):
        seen = dict(desktop=[], thread=[], file=[])
        senders = {c: (lambda text, c=c: seen[c].append(text) or ('steered' if c == 'thread' else True)) for c in seen}
        b.ladder_delivery(self.host, self.state, ['Fable5m2x', 'Fable5m2.5x', 'Codex15m1.5x'], self.now, senders)
        self.assertTrue(all(len(v) == 1 for v in seen.values()))
        self.assertIn('Codex15m1.5x', seen['thread'][0])
        self.assertIn('delivery=steered', seen['file'][0])
        self.assertEqual(self.state['pending'], [])

    def test_failed_channel_retries_without_duplicate_desktop(self):
        counts = dict(desktop=0, thread=0, file=0)
        def sender(c, fail=False):
            def send(text):
                counts[c] += 1
                return False if fail else 'steered' if c == 'thread' else True
            return send
        with redirect_stdout(io.StringIO()):
            b.ladder_delivery(self.host, self.state, ['Fable crossed'], self.now,
                              {c: sender(c, c == 'thread') for c in counts})
        self.assertEqual(len(self.state['pending']), 1)
        b.ladder_delivery(self.host, self.state, [], self.now + 30, {c: sender(c) for c in counts})
        self.assertEqual(counts, dict(desktop=1, thread=2, file=2))
        self.assertEqual(self.state['pending'], [])

    def test_local_meter_check_does_not_log_tsv_or_call_modal(self):
        config = self.root / 'boxes.toml'
        config.write_text(Path(__file__).with_name('boxes.proposed.toml').read_text().replace('approved = false', 'approved = true'))
        ledger_path = self.root / 'category-ledger.json'
        ledger_path.write_text(json.dumps(self.data))
        args = SimpleNamespace(boxes=config, uncap=None, accept_defaults=False, passive=False, test_box=None,
                               log=False, warn=False, meter_check=True)
        self.host.fetch = lambda *args: None
        with patch.object(b, 'deliver_current'), patch.object(self.host, 'parse', return_value=self.rows[:2]), \
                patch.object(self.host, 'parse_codex', return_value=self.rows[2:]), \
                patch.object(self.host, 'fetch') as fetch, patch.object(self.host, 'modal_check') as modal:
            self.assertEqual(b.cli(self.host, args, lambda: datetime.fromtimestamp(self.now, self.host.ET)), 0)
            self.assertEqual(fetch.call_count, 2)
            self.assertFalse(modal.called)
        self.assertFalse(self.host.LOG.exists())

    def test_human_release_accepts_old_reset_gap_without_refunding_prep(self):
        at = self.policy['preparation']['start']
        self.data['events'] = [[at + 1, 'fable', 20]]
        self.data['gaps'] = [[self.now - 60, 'fable', 'source']]
        before = b.prep_readings(self.policy, self.data, self.now)[0]
        self.assertTrue(before['incomplete'])
        self.data['forgiven']['fable'] = self.now
        after = b.prep_readings(self.policy, self.data, self.now)[0]
        self.assertFalse(after['incomplete'])
        self.assertEqual(after['used'], 20)

    def test_quantized_non_fable_catchup_never_causes_false_rate_stop(self):
        source = self.policy['sources'][0]
        key = source['provider'] + ':' + source['email']
        self.data['meter_intervals'] = [[self.now - 60, self.now, 'non-fable', 2, key]]
        self.assertEqual(b.confirmed_window(self.data, 'non-fable', self.now - 300, self.now, self.policy), 0)
        _, trips = b.ladder_evaluate(self.policy, self.data, {}, {}, self.now)
        self.assertEqual(trips, [])

    def test_stop_lower_bound_removes_uncertainty_per_account_not_per_poll(self):
        key = 'codex:account-c@example.com'
        self.data['meter_intervals'] = [[self.now - 240, self.now - 180, 'codex', 1, key],
                                        [self.now - 120, self.now - 60, 'codex', 2, key]]
        self.assertEqual(b.confirmed_window(self.data, 'codex', self.now - 300, self.now, self.policy), 2)

    def test_dry_run_stop_rearms_after_low_window(self):
        self.policy['preparation']['rate_stops_dry_run'] = True
        self.confirmed('codex', 5, 3)
        lines, _ = b.ladder_evaluate(self.policy, self.data, {}, self.state, self.now)
        self.assertTrue(any('WOULD STOP' in line for line in lines))
        first = self.now
        self.data['meter_intervals'] = []
        for seconds in range(30, 631, 30):
            b.ladder_evaluate(self.policy, self.data, {}, self.state, first + seconds)
        self.now = first + 660
        self.confirmed('codex', 5, 3)
        lines, _ = b.ladder_evaluate(self.policy, self.data, {}, self.state, self.now)
        self.assertTrue(any('WOULD STOP' in line for line in lines))

    def test_old_poll_time_intervals_are_cleared_on_version_migration(self):
        self.data['meter_intervals'] = [[self.now - 60, self.now, 'codex', 100, 'codex:account-c@example.com']]
        self.data['meter_interval_version'] = 1
        old_events = list(self.data['events'])
        self.record(self.data, at=self.now + 60)
        self.assertEqual(self.data['meter_intervals'], [])
        self.assertEqual(self.data['events'], old_events)
        self.assertEqual(self.data['meter_interval_version'], 2)

    def test_provider_observation_endpoints_not_poll_times_bound_intervals(self):
        rows = copy.deepcopy(self.rows)
        for row in rows:
            row['observed_at'] = self.now - 1200
        ledger = b.empty_ledger()
        b.observe(ledger, rows, self.now - 600, self.policy)
        # A new poll before the next response must not move the usage baseline.
        b.observe(ledger, rows, self.now - 60, self.policy)
        rows[2].update(u7=.3, observed_at=self.now)
        b.observe(ledger, rows, self.now, self.policy)
        intervals = ledger['meter_intervals']
        self.assertEqual(len(intervals), 1)
        self.assertEqual(intervals[0][:2], [self.now - 1200, self.now])
        self.assertEqual(b.confirmed_window(ledger, 'codex', self.now - 300, self.now, self.policy), 0)

    def test_non_fable_stops_require_same_response_paired_meters(self):
        source = self.policy['sources'][0]
        root = self.ledger(start=self.now - 60)
        rows = copy.deepcopy(self.rows)
        rows[0].update(u7=.3, observed_at=self.now)
        b.observe(root, rows, self.now, self.policy)
        self.assertFalse(any(e[2] == 'non-fable' for e in root['meter_intervals']))
        rows[0].update(nf_points=40, nf_observed_at=self.now)
        b.observe(root, rows, self.now + 1, self.policy)
        rows[0].update(nf_points=50, nf_observed_at=self.now + 60)
        b.observe(root, rows, self.now + 60, self.policy)
        self.assertEqual(len([e for e in root['meter_intervals'] if e[2] == 'non-fable']), 1)

    def test_parser_derives_nf_from_one_snapshot_not_mixed_global_and_model(self):
        prefix = self.host.PREFIX
        raw = {'files': [dict(provider='claude', email='test', quota=dict(
            observed_at='2025-11-11T21:00:00Z', signals={prefix+'7d-Utilization': '.72', prefix+'7d_oi-Utilization': '.99'}),
            model_quotas={'claude-fable-5-2': dict(observed_at='2025-11-11T20:00:00Z', signals={prefix+'7d-Utilization': '.70', prefix+'7d_oi-Utilization': '.95'})})]}
        row = self.host.parse(raw)[0]
        self.assertAlmostEqual(row['nf_points'], 45)
        self.assertEqual(row['nf_observed_at'], b.stamp('2025-11-11T21:00:00Z'))
        del raw['files'][0]['quota']['signals'][prefix+'7d_oi-Utilization']
        row = self.host.parse(raw)[0]
        self.assertAlmostEqual(row['nf_points'], 45)  #140-95, from the older paired model snapshot
        self.assertEqual(row['nf_observed_at'], b.stamp('2025-11-11T20:00:00Z'))


if __name__ == '__main__':
    unittest.main()
