"""One offline file; no proxies, credentials, subprocesses or live notices."""
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import Mock,patch

import claude_usage_boxes as b
import claude_usage_messages as human


class CategoryDelivery(unittest.TestCase):
    def test_optional_category_retry_survives_existing_channel_success(self):
        host=SimpleNamespace(desktop_notify=Mock(return_value=True),MATRIX_COPY_ALL=False,LOG=Path('/nonexistent/claude-usage-test/log.tsv'))
        line=human.pack('WARNING','Fable','Preparation: 15-minute rate',
                        '4 points vs 3 at target rate (last 15 min)','Rolling rate window','slow down')
        fake=SimpleNamespace(enabled=lambda:True,notices=Mock(side_effect=[False,True]))
        state={}
        with patch.dict('sys.modules',{'claude_usage_category':fake}), \
             patch.object(b,'matrix_notice',return_value=True), \
             patch.object(b,'thread_notice',return_value='steered') as thread, \
             patch.object(b,'append_alert',return_value=True) as alert:
            b.ladder_delivery(host,state,[line],1000)
            self.assertEqual(len(state['pending']),1)
            self.assertNotIn('category',state['pending'][0]['receipts'])
            b.ladder_delivery(host,state,[],1001)
        self.assertEqual(state['pending'],[])
        self.assertEqual(thread.call_count,1)
        self.assertEqual([c.args[1] for c in alert.call_args_list],[Path('/nonexistent/claude-usage-test/usage-alerts.log')])

    def test_disabled_category_does_not_change_existing_receipt_contract(self):
        host=SimpleNamespace(desktop_notify=Mock(return_value=True),MATRIX_COPY_ALL=False,LOG=Path('/nonexistent/claude-usage-test/log.tsv'))
        fake=SimpleNamespace(enabled=lambda:False,notices=Mock())
        with patch.dict('sys.modules',{'claude_usage_category':fake}), \
             patch.object(b,'matrix_notice',return_value=True), \
             patch.object(b,'thread_notice',return_value='steered'), \
             patch.object(b,'append_alert',return_value=True) as alert:
            state={};b.ladder_delivery(host,state,['Fable unknown legacy'],1000)
        self.assertEqual(state['pending'],[]);fake.notices.assert_not_called()
        self.assertEqual([c.args[1] for c in alert.call_args_list],[Path('/nonexistent/claude-usage-test/usage-alerts.log')])
