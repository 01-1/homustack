"""Offline category-only notifications; no provider calls or live state writes."""
import json
import unittest
from unittest.mock import patch
import claude_usage_boxes as b
import test_messages

class CategoryOnly(unittest.TestCase):
    setUp=test_messages.Messages.setUp

    def test_account_generation_silent_for_near_full_out_and_missing(self):
        # Forbidden helpers establish that account notification generation never
        # reads/mutates accounting or tries an I/O path.
        host=type('Host',(),{'pool_points':lambda *a:(_ for _ in ()).throw(AssertionError())})()
        self.assertEqual(b.account_events(host,[dict(provider='codex',email='x',u5=.91)],[],{},self.now),[])

    def test_pending_account_retry_removed_without_recovery_and_category_preserved(self):
        path=self.host.LOG.parent/'current-notices.json'
        account='NOTE  Codex: x 5-hour limit nearly used up | Used: 91 of 100 points | Ends: later | Do: nothing needed'
        state={'records':{'account:codex:x:5h:codex':dict(level=1,line=account,at=self.now,receipts={},trip=False),
                          'missing:claude:x:fable':dict(level=1,line=account,at=self.now,receipts={},trip=False)}}
        self.host.atomic_write(path,json.dumps(state))
        b.deliver_current(self.host,[('account:codex:x:5h:codex',1,account,100,False),
                                  ('rate:fable',2,self.rate,100,False)],self.now)
        saved=json.loads(path.read_text())
        self.assertFalse(any(k.startswith(('account:','missing:')) for k in saved['records']))
        self.assertIn('rate:fable',saved['records'])
        self.assertTrue(self.desktop)
        self.assertFalse(any('nearly used up' in x or 'Previous warning ended' in x for x in self.desktop))

    def test_retry_only_drops_retired_missing_meter(self):
        path=self.host.LOG.parent/'current-notices.json'
        self.host.atomic_write(path,json.dumps({'records':{'missing:codex:x:codex':dict(
            level=1,line=self.rate,at=self.now,receipts={},trip=False)}}))
        self.host.guard_state=lambda _: {'latches':{}}
        self.host.GUARD_STATE=self.host.LOG.parent/'guard.json'
        b.deliver_current(self.host,None,self.now)
        self.assertFalse(json.loads(path.read_text())['records'])
        self.assertFalse(self.desktop)
        self.assertFalse(self.thread)

if __name__=='__main__':unittest.main()
