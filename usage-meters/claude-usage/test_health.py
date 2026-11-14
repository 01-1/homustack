"""Debounced operational errors and production numeric-string meters."""
import unittest
from unittest.mock import patch
import claude_usage_boxes as b
import test_messages
import test_categories


class Health(unittest.TestCase):
    setUp = test_messages.Messages.setUp

    def test_two_failures_then_one_recovery_to_all_channels(self):
        b.check_health(self.host, 'accounting', TypeError('meter'), self.now)
        self.assertEqual(self.desktop, [])
        b.check_health(self.host, 'accounting', TypeError('meter'), self.now+60)
        self.assertEqual(len(self.desktop), 1)
        self.assertIn('WARNING  Usage tool', self.desktop[0])
        self.assertIn('TypeError', self.desktop[0])
        self.assertNotIn('Pause work', self.desktop[0])
        self.assertEqual(len(self.thread), 1)
        b.check_health(self.host, 'accounting', TypeError('meter'), self.now+120)
        self.assertEqual(len(self.desktop), 1)
        b.check_health(self.host, 'accounting', None, self.now+180)
        self.assertEqual(len(self.desktop), 2)
        self.assertIn('recovered', self.desktop[1])
        self.assertEqual(len(self.thread), 2)
        b.check_health(self.host, 'accounting', None, self.now+240)
        self.assertEqual(len(self.desktop), 2)

    def test_one_failure_clears_silently(self):
        b.check_health(self.host, 'accounting', ValueError('transient'), self.now)
        b.check_health(self.host, 'accounting', None, self.now+60)
        self.assertFalse(self.desktop)
        self.assertFalse(self.thread)

    def test_modal_warning_uses_matrix_copy_destinations(self):
        self.host.MATRIX_COPY_ALL=True
        self.assertTrue(b.billing_notice(self.host, 'Modal is BILLING real money: $1.25 billed this month.', self.now))
        self.assertEqual(len(self.desktop),1)
        self.assertIn('WARNING  Usage tool: Modal billing needs attention', self.desktop[0])
        self.assertIn('$1.25',self.desktop[0])
        self.assertEqual(self.thread,['For you:\n'+self.desktop[0]])

    def test_new_modal_bill_is_delivered_and_failed_channel_retries(self):
        self.host.MATRIX_COPY_ALL=True
        self.host.desktop_notify=lambda _:False
        b.billing_notice(self.host,'Modal is BILLING real money: $1.25 billed this month.',self.now)
        self.assertEqual(self.desktop,[])
        self.host.desktop_notify=lambda line:self.desktop.append(line) or True
        b.billing_notice(self.host,None,self.now+60)
        self.assertEqual(len(self.desktop),1)
        b.billing_notice(self.host,'Modal is BILLING real money: $1.75 billed this month.',self.now+120)
        self.assertEqual(len(self.desktop),2)
        self.assertIn('$1.75',self.desktop[-1])


class StringMeters(unittest.TestCase):
    setUp = test_categories.Categories.setUp
    ledger = test_categories.Categories.ledger

    def test_production_claude_strings_do_not_break_account_fallback(self):
        for row in self.rows:
            if row['provider'] == 'claude':
                row.update(u7='0.73', uf='0.99', u5=None)
        self.rows[1]['u5'] = '1.0'
        self.rows[1]['r5'] = self.now+3600
        ledger = self.ledger()
        events = b.account_events(self.host, self.rows, [], ledger, self.now)
        self.assertEqual(events,[])  # Account meters are accounting inputs only.
        self.assertAlmostEqual(b.gauges(self.rows[0])['non-fable'],47.)


    def test_carried_fable_meter_with_string_reset_time(self):
        self.rows[1].update(u7='0.13',uf=.26,u5='1.0',r5=str(self.now+3600))
        ledger=self.ledger()
        self.rows[1]['uf']=None
        b.observe(ledger,self.rows,self.now,self.policy)
        items=b.readings(self.policy,ledger,self.now)
        events=b.account_events(self.host,self.rows,items,ledger,self.now)
        self.assertEqual(events,[])  # Missing individual meter stays locally carried.
        self.assertIn('fable',ledger['sources']['claude:'+self.rows[1]['email']]['carried'])
