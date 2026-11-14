"""Usageboxing regressions: synthetic meters, no real proxies or notifications."""
from contextlib import redirect_stdout, redirect_stderr
from datetime import datetime, timedelta
import io
import json
from pathlib import Path
import subprocess
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from test_usage import cu, SECRET, DATA, no_category_dispatch, quiet_speaker


class BoxTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.now = datetime(2025, 11, 11, 4, tzinfo=cu.ET)
        self.pool = 'claude:main@test:all'
        self.box = dict(name='tonight', start=(self.now - timedelta(hours=2)).timestamp(),
                        end=(self.now + timedelta(hours=3)).timestamp(), allow={self.pool: 20})
        self.config = self.root / 'boxes.toml'
        self.state = self.root / 'state.json'
        self.out = io.StringIO()
        self.addCleanup(patch.stopall)
        patch.object(cu, 'BOXES', self.config).start()
        patch.object(cu, 'BOX_STATE', self.state).start()
        patch.object(cu, 'GUARD_STATE', self.root / 'hard-stops.json').start()
        # parse() reads Fable exclusions from the Claude auth folder: give it an empty one.
        patch.object(cu, 'AUTH_DIRS', {'claude': self.root / 'no-claude-auth', 'codex': self.root / 'no-codex-auth'}).start()
        patch.object(cu, 'LOG', self.root / 'log.tsv').start()
        patch.object(cu, 'fetch', side_effect=AssertionError('real fetch forbidden')).start()
        patch.object(cu, 'fetch_modal', side_effect=OSError('no modal')).start()
        patch.object(cu.subprocess, 'run', side_effect=AssertionError('real subprocess forbidden')).start()
        patch.object(cu.urllib.request, 'urlopen', side_effect=AssertionError('real network forbidden')).start()
        quiet_speaker(self)
        no_category_dispatch(self)

    def row(self, u7=.5, **changes):
        return dict(dict(provider='claude', email='main@test', status='active',
                         u7=u7, r7=(self.now + timedelta(days=4)).timestamp(),
                         u5=None, r5=None, uf=None, rf=None), **changes)

    def history(self, row=None, baseline=.4):
        return [dict(row or self.row(), u7=baseline, time=self.box['start'] + i * 600)
                for i in range(12)]

    def reading(self, row=None, history=None, now=None):
        row = row or self.row()
        return cu.box_reading(self.box, self.pool, [row],
                              self.history(row) if history is None else history, now or self.now)

    def configure(self, approved=True):
        self.config.write_text(f'''approved = {str(approved).lower()}
[[boxes]]
name = "tonight"
start = "2025-11-11T02:00:00-05:00"
end = "2025-11-11T07:00:00-05:00"
[boxes.allow]
"claude:main@test:all" = 20
''')

    def events(self, rows=None, history=None, notice=None):
        rows = [self.row()] if rows is None else rows
        return cu.box_events(rows, self.history() if history is None else history,
                             self.now, [self.box], notice)

    def deliver(self, events, now=None, discord=None, desktop=None):
        with redirect_stdout(self.out), redirect_stderr(self.out):
            return cu.deliver_events(events, now or self.now, self.state,
                                     discord_sender=discord or (lambda line: True),
                                     desktop_sender=desktop or (lambda line: True))

    def test_meter_points_are_incremental_and_plain(self):
        reading = self.reading()
        self.assertAlmostEqual(reading['used'], 10)
        self.assertFalse(reading['incomplete'])
        line = cu.box_line(self.box, self.pool, reading, self.now)
        for text in ('Claude main@test all models', 'tonight', '10.0 of 20 points used', '3h left'):
            self.assertIn(text, line)
        self.assertLessEqual(len(line.splitlines()), 2)
        self.assertNotIn('last 30', line)
        self.assertNotIn(SECRET, line)

    def test_projection_warns_before_half_and_repeats_every_30_minutes(self):
        self.box['allow'][self.pool] = 25
        history = self.history(baseline=.49)
        row = self.row(.59)
        events = self.events([row], history)
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0][3], 1800)
        self.assertIn('At this rate it will exceed 25', events[0][2])
        self.assertTrue(self.deliver(events))
        self.assertEqual(self.deliver(events, self.now + timedelta(minutes=20)), [])
        self.assertTrue(self.deliver(events, self.now + timedelta(minutes=30)))

    def test_each_threshold_escalates_without_waiting_for_cooldown(self):
        for delta, expected, interval in ((10, 1, 3600), (15, 2, 1800), (18, 3, 600), (20, 4, 600)):
            row = self.row(.4 + delta / 100)
            # No recent surge: spend was gradual and the box is nearly over.
            history = [dict(row, u7=.4 + delta / 100 * i / 12,
                            time=self.box['start'] + i * 600) for i in range(12)]
            with patch.object(cu, 'box_reading', return_value=dict(
                    used=delta, incomplete=False, status='active', eta=None)):
                events = self.events([row], history)
            self.assertEqual(events[0][1], expected)
            self.assertEqual(events[0][3], interval)
            self.assertTrue(self.deliver(events))

    def test_at_90_percent_repeat_every_check(self):
        events = [('box', 3, '18 of 20; pause soon', 600)]
        self.deliver(events)
        self.assertTrue(self.deliver(events, self.now + timedelta(minutes=10)))

    def test_reset_does_not_refund_spent_points(self):
        row = self.row(.05, r7=(self.now + timedelta(days=7)).timestamp())
        history = self.history(self.row(.95), baseline=.90)
        history[6]['u7'] = .95
        for p in history[7:]:
            p.update(u7=.02, r7=row['r7'])
        reading = self.reading(row, history)
        self.assertAlmostEqual(reading['used'], 10)
        self.assertTrue(reading['incomplete'])
        self.assertIn('at least 10.0', cu.box_line(self.box, self.pool, reading, self.now))

    def test_unexplained_refund_never_creates_free_budget_or_negative_rate(self):
        history = self.history(baseline=.4)
        history[5]['u7'] = .5
        for p in history[6:]:
            p['u7'] = .3
        reading = self.reading(self.row(.35), history)
        self.assertAlmostEqual(reading['used'], 15)
        self.assertTrue(reading['incomplete'])
        self.assertGreaterEqual(reading['rate'], 0)

    def test_missing_baseline_and_long_gap_are_explicitly_incomplete(self):
        for history in ([], [dict(self.row(.4), time=self.box['start'] + 600)]):
            reading = self.reading(history=history)
            self.assertTrue(reading['incomplete'])
            self.assertIn('at least', cu.box_line(self.box, self.pool, reading, self.now))
            self.assertTrue(self.events(history=history))

    def test_unknown_meter_is_never_zero(self):
        self.pool = 'codex:main@test:5h'
        self.box['allow'] = {self.pool: 40}
        reading = self.reading()
        self.assertIsNone(reading['used'])
        line = cu.box_line(self.box, self.pool, reading, self.now)
        self.assertIn('usage unknown out of 40', line)
        self.assertIn('3h left', line)
        self.assertTrue(self.events())

    def test_current_missing_meter_is_incomplete_even_with_recent_history(self):
        reading = self.reading(self.row(None))
        self.assertTrue(reading['incomplete'])

    def test_stale_expired_meter_cannot_make_an_eta(self):
        reading = self.reading(self.row(.5, r7=self.now.timestamp() - 60))
        self.assertTrue(reading['incomplete'])
        self.assertIsNone(reading['eta'])

    def test_accounts_and_providers_are_isolated(self):
        history = self.history()
        history += [dict(p, provider='codex', u7=.0) for p in history]
        history += [dict(p, email='other@test', u7=.0) for p in history]
        self.assertAlmostEqual(self.reading(history=history)['used'], 10)

    def test_future_and_ended_boxes_do_not_send_active_warnings(self):
        for start, end, expected in ((1, 2, 'future'), (-4, -1, 'ended')):
            self.box['start'] = (self.now + timedelta(hours=start)).timestamp()
            self.box['end'] = (self.now + timedelta(hours=end)).timestamp()
            reading = self.reading()
            self.assertEqual(reading['status'], expected)
            self.assertEqual(self.events(), [])

    def test_ended_box_without_readings_never_warns_as_active(self):
        self.box['end'] = self.now.timestamp() - 1
        reading = self.reading(history=[])
        self.assertEqual(reading['status'], 'ended')
        self.assertEqual(self.events(rows=[], history=[]), [])

    def test_fable_and_codex_5h_are_measured_in_their_own_points(self):
        for pool, field, provider in (('claude:main@test:fable', 'uf', 'claude'),
                                      ('codex:main@test:5h', 'u5', 'codex')):
            self.pool = pool
            self.box['allow'] = {pool: 20}
            row = self.row(**{field: .5, 'provider': provider})
            history = [dict(p, **{field: .4, 'provider': provider}) for p in self.history()]
            self.assertAlmostEqual(self.reading(row, history)['used'], 10)

    def test_edits_to_allowance_create_a_fresh_warning_immediately(self):
        self.deliver(self.events())
        self.box['allow'][self.pool] = 5
        self.assertTrue(self.deliver(self.events()))

    def test_zero_allowance_and_overrun_have_action(self):
        for allowed in (0, 5, 10):
            self.box['allow'][self.pool] = allowed
            line = cu.box_line(self.box, self.pool, self.reading(), self.now)
            self.assertIn('stays blocked until a human uncaps', line)
            self.assertEqual(self.events()[0][1], 4)

    def test_all_boxes_checked_independently_for_same_pool(self):
        second = dict(self.box, name='final run', allow={self.pool: 100})
        events = cu.box_events([self.row()], self.history(), self.now, [self.box, second])
        self.assertEqual(len(events), 1)
        second['allow'][self.pool] = 12
        events = cu.box_events([self.row()], self.history(), self.now, [self.box, second])
        self.assertEqual(len(events), 2)
        self.assertNotEqual(events[0][0], events[1][0])

    def test_eta_beyond_box_is_not_published(self):
        self.box['allow'][self.pool] = 10000
        reading = self.reading()
        self.assertIsNone(reading['eta'])
        self.assertEqual(self.events(), [])

    def test_discord_failure_retries_even_when_desktop_succeeded(self):
        event = [('one', 1, 'fake', 3600)]
        self.deliver(event, discord=lambda line: False)
        desktop, discord = [], []
        self.deliver(event, self.now + timedelta(minutes=10),
                     discord=lambda line: discord.append(line) or True,
                     desktop=lambda line: desktop.append(line) or True)
        self.assertEqual(discord, ['fake'])
        self.assertEqual(desktop, [])
        self.assertEqual(self.state.stat().st_mode & 0o777, 0o600)

    def test_desktop_failure_retries_even_when_discord_succeeded(self):
        event = [('one', 1, 'fake', 3600)]
        self.deliver(event, desktop=lambda line: False)
        calls = []
        self.deliver(event, self.now + timedelta(minutes=10), desktop=lambda line: calls.append(line) or True)
        self.assertEqual(calls, ['fake'])

    def test_recovered_then_recurring_risk_warns_again_immediately(self):
        event = [('one', 1, 'fake', 3600)]
        self.deliver(event)
        self.deliver([])
        self.assertTrue(self.deliver(event, self.now + timedelta(minutes=10)))

    def test_corrupt_state_and_failed_deliveries_are_retryable(self):
        self.state.write_text('{broken')
        event = [('one', 1, 'fake', 3600)]
        self.deliver(event, discord=lambda line: False, desktop=lambda line: False)
        self.assertTrue(self.deliver(event))

    def test_config_absent_unapproved_invalid_and_native_datetimes(self):
        self.assertEqual(cu.read_boxes()[0], [])
        self.configure(False)
        boxes, notice = cu.read_boxes()
        self.assertEqual(len(boxes), 1)
        self.assertIn('Proposed boxes only', notice)
        self.assertEqual(len(self.events(notice=notice)), 1)
        self.configure()
        self.assertIsNone(cu.read_boxes()[1])
        self.config.write_text(self.config.read_text().replace('"2025-11-11T02:00:00-05:00"', '2025-11-11T02:00:00-05:00'))
        self.assertIsNone(cu.read_boxes()[1])
        self.config.write_text(SECRET)
        boxes, notice = cu.read_boxes()
        self.assertEqual(boxes, [])
        self.assertNotIn(SECRET, notice)

    def test_invalid_config_never_silently_activates_a_subset(self):
        self.configure()
        good = self.config.read_text()
        for old, new in (('= 20', '= -1'), ('= 20', '= true'), ('= 20', '= nan'),
                         ('main@test:all', 'main@test:nope'), ('claude:', 'openai:'),
                         ('02:00:00-05:00', '08:00:00-05:00'), ('02:00:00-05:00', '02:00:00')):
            with self.subTest(new=new):
                self.config.write_text(good.replace(old, new))
                self.assertEqual(cu.read_boxes()[0], [])

    def test_hourly_schedule_has_independent_windows_and_shows_only_current_hour(self):
        self.configure()
        self.config.write_text(self.config.read_text().replace('[boxes.allow]', 'repeat_minutes = 60\n[boxes.allow]'))
        boxes, notice = cu.read_boxes()
        self.assertIsNone(notice)
        self.assertEqual(len(boxes), 5)
        self.assertTrue(all(b['end'] - b['start'] == 3600 for b in boxes))
        self.assertEqual(len({cu.latch_id(b, self.pool) for b in boxes}), 5)
        current = cu.current_boxes(boxes, self.now)
        self.assertEqual(len(current), 1)
        self.assertEqual(current[0]['start'], self.now.timestamp())
        self.assertIn('Tue 04:00–05:00', current[0]['name'])

    def test_hourly_schedule_does_not_carry_spending_into_next_hour(self):
        self.configure()
        self.config.write_text(self.config.read_text().replace('[boxes.allow]', 'repeat_minutes = 60\n[boxes.allow]'))
        boxes, _ = cu.read_boxes()
        history = [dict(self.row(.5), time=self.now.timestamp() - 600)]
        current = cu.current_boxes(boxes, self.now)[0]
        reading = cu.box_reading(current, self.pool, [self.row(.5)], history, self.now)
        self.assertAlmostEqual(reading['used'], 0)
        self.assertFalse(reading['incomplete'])

    def test_invalid_hourly_schedule_is_rejected(self):
        self.configure()
        original = self.config.read_text()
        for repeat in ('0', '30', 'true', '-60'):
            self.config.write_text(original.replace('[boxes.allow]', f'repeat_minutes = {repeat}\n[boxes.allow]'))
            self.assertEqual(cu.read_boxes()[0], [])

    def test_hourly_cli_does_not_dump_all_future_periods(self):
        self.configure()
        self.config.write_text(self.config.read_text().replace('[boxes.allow]', 'repeat_minutes = 60\n[boxes.allow]'))
        with patch.object(cu, 'fetch', return_value={'files': []}), redirect_stdout(self.out):
            self.assertEqual(cu.main([], clock=lambda: self.now), 0)
        self.assertIn('allowances renew hourly', self.out.getvalue())
        self.assertIn('Tue 04:00–05:00', self.out.getvalue())
        self.assertNotIn('Tue 06:00–07:00', self.out.getvalue())

    def test_caps_alarm_despite_error_status_banked_resets_or_bad_config(self):
        row = self.row(1.11, status='error', uf=1., rf=self.row()['r7'])
        events = self.events([row], notice='invalid boxes; fix file')
        self.assertEqual(len(events), 3)
        self.assertTrue(all(e[1] == 4 for e in events[1:]))
        self.assertTrue(all('OUT OF USAGE' in e[2] for e in events[1:]))
        self.assertTrue(all(e[3] == 1800 for e in events[1:]))

    def test_near_empty_fable_warns_even_without_an_approved_box(self):
        row = self.row(uf=.97, rf=self.row()['r7'])
        events = self.events([row], notice='Proposed boxes only')
        fable = [e for e in events if e[0].endswith(':fable')]
        self.assertEqual(len(fable), 1)
        self.assertIn('97 of 100 meter points used', fable[0][2])
        self.assertIn('Only 3 points remain', fable[0][2])
        self.assertEqual(fable[0][1:], (3, fable[0][2], 1800))

    def test_direct_sender_uses_existing_dm_and_never_t3_or_command_secrets(self):
        calls = []
        def run(args, **kwargs):
            calls.append((args, kwargs))
            return SimpleNamespace(returncode=0)
        self.assertTrue(cu.send_direct('test', run=run))
        args, kwargs = calls[0]
        self.assertIn('notify_discord', args[2])
        self.assertIn('DISCORD_DM_CHANNEL', args[2])
        self.assertNotIn('t3', args[2])
        self.assertNotIn(SECRET, repr(calls))
        self.assertEqual(kwargs['input'], 'Usage: test')
        self.assertEqual(kwargs['timeout'], 20)
        def timeout(*args, **kwargs):
            raise subprocess.TimeoutExpired(SECRET, 1)
        with redirect_stderr(self.out):
            self.assertFalse(cu.send_direct('test', run=timeout))
        self.assertNotIn(SECRET, self.out.getvalue())

    def test_plain_cli_shows_boxes_and_reads_each_proxy_exactly_once(self):
        self.configure()
        with patch.object(cu, 'fetch', side_effect=[DATA, {'files': []}]) as fetch, \
                patch.object(cu, 'parse', return_value=[self.row()]), redirect_stdout(self.out):
            self.assertEqual(cu.main([], clock=lambda: self.now), 0)
        self.assertEqual(fetch.call_count, 2)
        self.assertEqual(fetch.call_args_list[0].args, ())
        self.assertEqual(fetch.call_args_list[1].args, (cu.CODEX_URL, cu.CODEX_KEY))
        self.assertIn('tonight', self.out.getvalue())
        self.assertIn('of 20 points', self.out.getvalue())
        self.assertFalse(cu.LOG.exists())

    def test_explicit_acceptance_only_changes_proposal_flag(self):
        self.configure(False)
        before = self.config.read_text()
        with patch.object(cu, 'fetch', return_value={'files': []}), redirect_stdout(self.out):
            self.assertEqual(cu.main(['--accept-defaults'], clock=lambda: self.now), 0)
        self.assertEqual(self.config.read_text(), before.replace('approved = false', 'approved = true'))

    def test_both_proxy_failures_still_run_modal_and_report_monitor_failure(self):
        self.configure()
        with patch.object(cu, 'fetch', side_effect=OSError(SECRET)), \
                patch.object(cu.subprocess, 'run', return_value=SimpleNamespace(returncode=0)) as stops, \
                patch.object(cu, 'deliver_events') as delivery, patch.object(cu, 'modal_check') as modal, \
                redirect_stderr(self.out):
            self.assertEqual(cu.main(['--warn'], clock=lambda: self.now), 1)
        self.assertEqual(modal.call_count, 1)
        # No hard stops: neither proxy is stopped; the unreadable box is read out instead.
        self.assertEqual(stops.call_count, 0)
        self.assertEqual(self.spoken, ['tonight, readings incomplete, all models.'])
        events = delivery.call_args.args[0]
        self.assertEqual(len([e for e in events if e[0].startswith('proxy:')]), 2)
        self.assertEqual([e for e in events if e[0] == 'guard-failed'], [])
        self.assertNotIn(SECRET, self.out.getvalue())

    def test_unverifiable_guard_speaks_and_stops_nothing(self):
        self.configure()
        with patch.object(cu, 'fetch', return_value={'files': []}), \
                patch.object(cu, 'enforce_boxes', side_effect=ValueError('guard unreadable')), \
                patch.object(cu.subprocess, 'run', return_value=SimpleNamespace(returncode=0)) as stops, \
                patch.object(cu, 'deliver_events') as delivery, patch.object(cu, 'modal_check'), \
                redirect_stderr(self.out):
            self.assertEqual(cu.main(['--warn'], clock=lambda: self.now), 1)
        self.assertEqual(stops.call_count, 0)
        self.assertEqual(self.spoken, ['Usage accounting could not be verified.'])
        [failed] = [e for e in delivery.call_args.args[0] if e[0] == 'guard-failed']
        self.assertEqual(failed[1], 4)
        self.assertIn('nothing was stopped', failed[2])


class HardStopTests(unittest.TestCase):
    """Enforcement against disposable auth files, never live credentials."""
    row = BoxTests.row
    history = BoxTests.history

    def setUp(self):
        BoxTests.setUp(self)
        self.auth = self.root / 'claude' / 'auth'
        self.auth.mkdir(parents=True)
        self.codex_auth = self.root / 'codex' / 'auth'
        self.codex_auth.mkdir(parents=True)
        self.dirs = {'claude': self.auth, 'codex': self.codex_auth}
        self.credential = self.auth / 'main.json'
        self.original = dict(type='claude', email='main@test', disabled=False, access_token=SECRET,
                             refresh_token='REFRESH-SECRET', extra={'preserve': True})
        self.credential.write_text(json.dumps(self.original))
        (self.codex_auth / 'other.json').write_text(json.dumps(dict(type='codex', email='other@test', disabled=False)))
        self.guard = self.root / 'hard-stops.json'
        self.box['allow'][self.pool] = 5

    def enforce(self, boxes=None, rows=None, history=None, now=None, notice=None):
        return cu.enforce_boxes([self.box] if boxes is None else boxes,
                                [self.row()] if rows is None else rows,
                                self.history() if history is None else history,
                                now or self.now, notice, self.guard, self.dirs)

    def uncap(self, pool=None, confirm=True):
        return cu.uncap_pool(pool or self.pool, self.guard, self.dirs, confirmer=lambda label: confirm)

    def assertAuthUntouched(self, before=None):
        """No hard stops: the tool never edits an auth file to stop usage."""
        self.assertEqual(json.loads(self.credential.read_text()), self.original if before is None else before)
        self.assertEqual(json.loads((self.codex_auth / 'other.json').read_text()),
                         dict(type='codex', email='other@test', disabled=False))
        self.assertEqual(cu.guard_state(self.guard)['files'], {})

    def legacy_stop(self, pool=None):
        """Disable the account the way an older version of the tool did, recording that it owns the flag."""
        state = cu.guard_state(self.guard)
        cu.update_auth_guard(self.credential, {pool or self.pool}, state, self.guard)
        cu.atomic_write(self.guard, json.dumps(state, sort_keys=True) + '\n')

    def test_cap_speaks_the_box_and_leaves_every_auth_file_untouched(self):
        before = self.credential.stat()
        events = self.enforce()
        self.assertAuthUntouched()
        after = self.credential.stat()
        self.assertEqual((after.st_ino, after.st_mtime_ns, after.st_mode), (before.st_ino, before.st_mtime_ns, before.st_mode))
        self.assertEqual(self.spoken, ['tonight, 200 percent, all models.'])
        self.assertEqual([(e[0], e[1], e[3]) for e in events], [('blocked:' + cu.latch_id(self.box, self.pool), 4, 1800)])
        self.assertNotIn(SECRET, self.guard.read_text() + repr(events) + repr(self.spoken))
        self.assertIn('allowance reached', self.guard.read_text())

    def test_box_edits_expiry_missing_config_and_meter_reset_never_uncap(self):
        self.enforce()
        self.box['allow'][self.pool] = 100
        self.enforce()
        self.enforce(boxes=[], rows=[], history=[], now=self.now + timedelta(days=20), notice='bad config')
        # The latch, and so the voice, survives all three; only a human uncap ends it.
        self.assertEqual(self.spoken, ['tonight, 200 percent, all models.'] * 3)
        self.assertEqual(len(cu.guard_state(self.guard)['latches']), 1)
        self.assertAuthUntouched()

    def test_unspent_hour_can_end_without_forcing_a_human_uncap(self):
        # Hourly boxes renew normally when no cap was hit. Only an actual cap
        # or unverifiable accounting latches a stop across the hour boundary.
        self.box['allow'][self.pool] = 100
        self.box['end'] = self.now.timestamp() - 1
        self.assertEqual(self.enforce(), [])
        self.assertEqual(self.spoken, [None])
        self.assertAuthUntouched()

    def test_ended_hour_that_reached_its_allowance_still_latches(self):
        self.box['end'] = self.now.timestamp() - 1
        history = self.history()
        for p in history[6:]:
            p['u7'] = .5
        self.enforce(history=history)
        self.assertEqual(self.spoken, ['tonight, 200 percent, all models.'])
        self.assertIn('allowance reached', self.guard.read_text())
        self.assertAuthUntouched()

    def test_new_hour_never_reopens_a_pool_blocked_in_the_previous_hour(self):
        self.enforce()
        previous = dict(self.box)
        following = dict(self.box, name='next hour', start=self.now.timestamp(),
                         end=self.now.timestamp() + 3600)
        history = self.history() + [dict(self.row(), time=self.now.timestamp())]
        self.enforce(boxes=[previous, following], history=history, now=self.now + timedelta(minutes=10))
        self.assertEqual(self.spoken, ['tonight, 200 percent, all models.'] * 2)
        self.assertEqual([item['box'] for item in cu.guard_state(self.guard)['latches'].values()], ['tonight'])
        self.assertAuthUntouched()

    def test_block_message_contains_pool_amount_period_and_human_action(self):
        line = self.enforce()[0][2]
        for text in ('main@test', 'tonight', '10.0 points used of 5 allowed', '3h left', 'human uncap'):
            self.assertIn(text, line)
        self.assertEqual(len(line.splitlines()), 2)

    def test_failed_human_confirmation_never_releases(self):
        self.enforce()
        self.assertFalse(self.uncap(confirm=False))
        self.assertEqual(self.spoken, ['tonight, 200 percent, all models.'])  # never silenced
        self.assertTrue(cu.guard_state(self.guard)['latches'])
        self.assertAuthUntouched()

    def test_human_uncap_restores_only_owned_flags_and_does_not_immediately_relock(self):
        self.enforce()
        refreshed = json.loads(self.credential.read_text())
        refreshed['access_token'] = 'NEW-REFRESHED-TOKEN'
        self.credential.write_text(json.dumps(refreshed))
        self.assertTrue(self.uncap())
        self.assertFalse(json.loads(self.credential.read_text())['disabled'])
        self.assertEqual(json.loads(self.credential.read_text())['access_token'], 'NEW-REFRESHED-TOKEN')
        self.assertEqual(self.enforce(), [])
        self.assertFalse(json.loads(self.credential.read_text())['disabled'])
        self.assertEqual(self.spoken, ['tonight, 200 percent, all models.', None, None])
        released = cu.guard_state(self.guard)['released']
        self.assertEqual(cu.box_events([self.row()], self.history(), self.now, [self.box], released=released), [])

    def test_preexisting_disabled_account_is_never_reenabled(self):
        data = dict(self.original, disabled=True)
        self.credential.write_text(json.dumps(data))
        self.enforce()
        self.assertTrue(self.uncap())
        self.assertTrue(json.loads(self.credential.read_text())['disabled'])

    def test_fable_cap_speaks_fable_and_excludes_no_model(self):
        self.pool = 'claude:main@test:fable'
        self.box['allow'] = {self.pool: 5}
        row = self.row(uf=.5, rf=self.row()['r7'])
        history = [dict(p, uf=.4, rf=row['rf']) for p in self.history()]
        self.enforce(rows=[row], history=history)
        self.assertAuthUntouched()
        self.assertEqual(self.spoken, ['tonight, 200 percent, Fable.'])
        self.assertTrue(self.uncap())
        self.assertEqual(self.spoken, ['tonight, 200 percent, Fable.', None])
        self.assertAuthUntouched()

    def test_legacy_exclusions_and_new_rules_survive_manual_uncap(self):
        legacy = dict(self.original, **{'excluded-models': ['opus*']})
        self.credential.write_text(json.dumps(legacy))
        self.pool = 'claude:main@test:fable'
        self.box['allow'] = {self.pool: 0}
        self.enforce(rows=[self.row(uf=.5)], history=[])
        self.assertAuthUntouched(legacy)
        self.assertEqual(self.spoken, ['tonight, readings incomplete, Fable.'])
        data = dict(legacy, excluded_models=['new-rule'])
        self.credential.write_text(json.dumps(data))
        self.assertTrue(self.uncap())
        data = json.loads(self.credential.read_text())
        self.assertEqual(data['excluded_models'], ['new-rule'])
        self.assertEqual(data['excluded-models'], ['opus*'])
        self.assertIsNone(self.spoken[-1])

    def test_global_exclusions_are_never_overridden_by_a_cap(self):
        config = self.auth.parent / 'config.yaml'
        config.write_text('oauth-excluded-models:\n  claude: [restricted]\n')
        self.pool = 'claude:main@test:fable'
        self.box['allow'] = {self.pool: 0}
        self.enforce(rows=[self.row(uf=.5)], history=[])
        # A per-account exclusion would override the global one; none is written.
        self.assertAuthUntouched()
        self.assertEqual(config.read_text(), 'oauth-excluded-models:\n  claude: [restricted]\n')
        self.assertEqual(self.spoken, ['tonight, readings incomplete, Fable.'])

    def test_incomplete_accounting_is_a_latched_spoken_alert(self):
        self.box['allow'][self.pool] = 100
        events = self.enforce(history=[])
        self.assertEqual(self.spoken, ['tonight, readings incomplete, all models.'])
        self.assertIn('readings incomplete', self.guard.read_text())
        self.assertIn('at least 0.0 points used of 100 allowed', events[0][2])
        self.assertIn('(readings incomplete)', events[0][2])
        self.assertAuthUntouched()

    def test_proposals_never_change_credentials(self):
        self.enforce(notice='Proposed boxes only')
        self.assertEqual(json.loads(self.credential.read_text()), self.original)
        self.assertFalse(cu.guard_state(self.guard)['latches'])
        self.assertEqual(self.spoken, [None])

    def test_one_uncap_cannot_release_another_box_or_pool(self):
        other_box = dict(self.box, name='another period')
        self.enforce(boxes=[self.box, other_box])
        # An explicit pool uncap releases its current locks, not future boxes.
        self.assertTrue(self.uncap())
        self.assertEqual(self.spoken, ['tonight, 200 percent, all models. another period, 200 percent, all models.', None])
        third = dict(self.box, name='new period')
        self.enforce(boxes=[third])
        self.assertEqual(self.spoken[-1], 'new period, 200 percent, all models.')
        self.assertEqual([item['box'] for item in cu.guard_state(self.guard)['latches'].values()], ['new period'])
        self.assertAuthUntouched()

    def test_corrupt_guard_state_is_not_silently_reset(self):
        self.guard.write_text('{broken')
        with self.assertRaises(ValueError):
            self.enforce()
        self.assertEqual(self.guard.read_text(), '{broken')

    def test_auth_files_disabled_before_no_hard_stops_are_restored(self):
        self.legacy_stop()
        self.assertTrue(json.loads(self.credential.read_text())['disabled'])
        events = self.enforce()
        self.assertAuthUntouched()  # restored to the original, tokens and all, and no longer owned
        self.assertEqual(self.spoken, ['tonight, 200 percent, all models.'])
        self.assertTrue(events)

    def test_interrupted_restore_keeps_recoverable_latch_and_ownership(self):
        self.legacy_stop()
        real_write = cu.atomic_write
        def interrupt(path, text):
            if path == self.credential:
                raise OSError('simulated disk failure')
            return real_write(path, text)
        with patch.object(cu, 'atomic_write', side_effect=interrupt):
            with self.assertRaises(OSError):
                self.enforce()
        state = cu.guard_state(self.guard)
        self.assertTrue(state['latches'])
        self.assertTrue(state['files'][str(self.credential)]['disabled_owned'])
        self.assertTrue(json.loads(self.credential.read_text())['disabled'])
        self.enforce()
        self.assertAuthUntouched()
        self.assertTrue(self.uncap())
        self.assertAuthUntouched()
        self.assertIsNone(self.spoken[-1])


class SpeakerTests(unittest.TestCase):
    """The over-cap voice that replaced hard stops, with every process call faked."""
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.proc = self.root / 'proc'
        self.started, self.killed = [], []
        self.addCleanup(patch.stopall)
        patch.object(cu, 'SPEECH_TEXT', self.root / 'state' / 'over-cap-speech.txt').start()
        patch.object(cu, 'SPEECH_PID', self.root / 'state' / 'over-cap-speech.pid').start()
        patch.object(cu, 'SPEECH_PY', self.root / 'no-venv' / 'python').start()
        patch.object(cu, 'SPEAKER', self.root / 'bin' / 'usage_speaker.py').start()
        patch.object(cu, 'PROC', self.proc).start()
        def popen(argv, **kwargs):
            self.started.append((argv, kwargs))
            return SimpleNamespace(pid=4242 + len(self.started))
        patch.object(cu.subprocess, 'Popen', side_effect=popen).start()
        patch.object(cu.subprocess, 'run', side_effect=AssertionError('real subprocess forbidden')).start()
        patch.object(cu.os, 'killpg', side_effect=lambda pid, sig: self.killed.append((pid, sig))).start()
        patch.object(cu.os, 'kill', side_effect=AssertionError('no signal may be sent')).start()

    def running(self, pid, argv):
        (self.proc / str(pid)).mkdir(parents=True)
        (self.proc / str(pid) / 'cmdline').write_bytes(b'\0'.join(a.encode() for a in argv) + b'\0')

    def test_voice_starts_once_and_later_phrases_update_the_running_speaker(self):
        cu.play_music('tonight, 200 percent, Fable.')
        [(argv, kwargs)] = self.started
        self.assertEqual(argv, ['/usr/bin/python3', str(cu.SPEAKER), str(cu.SPEECH_TEXT)])
        self.assertTrue(kwargs['start_new_session'])
        self.assertEqual(cu.SPEECH_TEXT.read_text(), 'tonight, 200 percent, Fable.\n')
        self.assertEqual(cu.SPEECH_PID.read_text(), '4243\n')
        self.running(4243, argv)
        cu.play_music('tonight, 210 percent, Fable.')
        self.assertEqual(len(self.started), 1)
        self.assertEqual(cu.SPEECH_TEXT.read_text(), 'tonight, 210 percent, Fable.\n')

    def test_stop_ends_only_our_speaker_and_removes_its_files(self):
        cu.play_music('tonight, 200 percent, Fable.')
        self.running(4243, self.started[0][0])
        cu.stop_music()
        self.assertEqual(self.killed, [(4243, 15)])
        self.assertFalse(cu.SPEECH_TEXT.exists() or cu.SPEECH_PID.exists())

    def test_stale_or_reused_pid_is_never_trusted(self):
        cu.SPEECH_PID.parent.mkdir(parents=True)
        for cmdline in (None, ['/usr/bin/bash', '-l'], ['/usr/bin/python3', str(cu.SPEAKER), '--once', 'old']):
            with self.subTest(cmdline=cmdline):
                self.started.clear()
                cu.SPEECH_PID.write_text('777\n')  # e.g. left over from before a reboot
                if cmdline is not None:
                    self.running(777, cmdline)
                cu.stop_music()
                self.assertEqual(self.killed, [])  # never signal a process that is not our speaker
                cu.SPEECH_PID.write_text('777\n')
                cu.play_music('tonight, 200 percent, Fable.')
                self.assertEqual(len(self.started), 1)  # and never stay silent because of one
                self.assertEqual(cu.SPEECH_PID.read_text(), '4243\n')
                if cmdline is not None:
                    (self.proc / '777' / 'cmdline').unlink()
                    (self.proc / '777').rmdir()

    def test_one_reminder_is_spoken_once_and_leaves_no_speaker_state(self):
        cu.say_once('Fable, 50 percent.')
        [(argv, kwargs)] = self.started
        self.assertEqual(argv, ['/usr/bin/python3', str(cu.SPEAKER), '--once', 'Fable, 50 percent.'])
        self.assertTrue(kwargs['start_new_session'])
        self.assertFalse(cu.SPEECH_PID.exists() or cu.SPEECH_TEXT.exists())

    def test_phrase_names_each_box_once_with_its_meter_and_percent(self):
        latches = {
            'a': dict(pool='claude:a@test:fable', box='tonight', used=10., allowed=5., reason='allowance reached'),
            'b': dict(pool='claude:b@test:fable', box='tonight', used=10., allowed=5., reason='allowance reached'),
            'c': dict(pool='codex:c@test:week', box='tonight', used=1., allowed=4., reason='allowance reached'),
            'd': dict(pool='claude:a@test:all', box='night', used=1., allowed=4., reason='readings incomplete'),
            'e': dict(pool='p', category='non-fable', box='Preparation: 5-minute rate', used=3.5, allowed=3.,
                      planned=1., soft_credit=.5, used_now=3.5),
            'f': dict(pool='p', category='fable', box='last 3h', used=None, allowed=2.)}
        self.assertEqual(cu.speech_phrase(latches),
                         'tonight, 200 percent, Fable. tonight, 25 percent, Codex. night, readings incomplete, all models. '
                         '5 minute, 300 percent, Opus. last 3h, over the cap, Fable.')


if __name__ == '__main__':
    unittest.main()
