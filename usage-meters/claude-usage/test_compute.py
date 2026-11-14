"""Synthetic billing only; never invokes a cloud CLI or sends a live notice."""
from datetime import datetime
from decimal import Decimal
from zoneinfo import ZoneInfo
from pathlib import Path
import json
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock
from unittest.mock import patch

import claude_usage_compute as c
from test_usage import cu


class ComputeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.host = SimpleNamespace(LOG=self.root/'log.tsv',guard_lock=cu.guard_lock,atomic_write=cu.atomic_write)
        start=patch.object(c,'START',datetime(2025, 11, 11, tzinfo=ZoneInfo('America/New_York')))  # the fixtures' tracking start
        start.start();self.addCleanup(start.stop)
        self.now = c.START.timestamp()+19*3600
        self.sender = Mock(return_value=True)
        self.creds=patch.object(c,'credential_status',return_value={name:True for name in c.READERS})
        self.creds.start();self.addCleanup(self.creds.stop)

    def refresh(self,value,**kwargs):
        return c.refresh(self.host,self.now,readers={'boat':lambda previous,now:dict(amount=str(value))},
                         sender=kwargs.get('sender',self.sender))

    def test_exact_thresholds_and_no_rounded_early_crossing(self):
        self.assertEqual([c.money(c.threshold(i)) for i in range(1,7)],
                         ['$5.00','$10.50','$16.55','$23.21','$30.53','$38.58'])
        self.assertEqual(c.threshold(4),Decimal('23.205'))
        self.refresh('23.204999')
        self.assertIn('crossed $16.55',self.sender.call_args.args[0])
        self.assertEqual(c.read(self.root/c.STATE_NAME)['fired'],3)

    def test_many_crossings_one_message_then_restart_does_not_repeat(self):
        self.refresh('31')
        self.sender.assert_called_once()
        text,key=self.sender.call_args.args
        self.assertIn('crossed $30.53',text)
        self.assertIn('next $38.58',text)
        self.assertEqual(len(text.splitlines()),1)
        self.refresh('31')
        self.sender.assert_called_once()
        self.assertEqual(c.read(self.root/c.STATE_NAME)['fired'],5)

    def test_enqueue_failure_keeps_pending_same_key_across_restart(self):
        self.sender.return_value=False
        self.refresh('12')
        first=self.sender.call_args.args
        self.assertEqual(c.read(self.root/c.STATE_NAME)['fired'],0)
        self.sender.return_value=True
        self.refresh('12')
        self.assertEqual(self.sender.call_args.args,first)
        self.assertEqual(c.read(self.root/c.STATE_NAME)['fired'],2)
        self.refresh('12')
        self.assertEqual(self.sender.call_count,2)

    def test_crash_after_enqueue_retries_idempotent_key(self):
        def crash(text,key):
            raise RuntimeError('crash after external enqueue')
        with self.assertRaises(RuntimeError):self.refresh('5',sender=crash)
        pending=c.read(self.root/c.STATE_NAME)['pending']
        self.refresh('5')
        self.assertEqual(self.sender.call_args.args,(pending['text'],pending['key']))
        self.assertEqual(c.read(self.root/c.STATE_NAME)['fired'],1)

    def test_provider_failure_retains_last_value_alerts_on_third_not_every_check(self):
        self.refresh('2')
        readers={'boat':Mock(side_effect=OSError('SECRET stderr')),'Modal':lambda p,n:dict(amount='4')}
        first=c.refresh(self.host,self.now,readers,self.sender)
        self.assertIn('$6.00 reported',first)
        self.assertIn('Unread now: boat',first)
        self.assertIn('Unread: boat',self.sender.call_args.args[0])
        self.assertNotIn('SECRET',self.sender.call_args.args[0])
        c.refresh(self.host,self.now+600,readers,self.sender)
        self.assertEqual(self.sender.call_count,1)
        c.refresh(self.host,self.now+1200,readers,self.sender)
        self.assertEqual(self.sender.call_count,2)
        self.assertIn('Three consecutive checks failed: boat',self.sender.call_args.args[0])
        c.refresh(self.host,self.now+1800,readers,self.sender)
        self.assertEqual(self.sender.call_count,2)

    def test_recovery_rearms_next_failure_episode(self):
        def fail(p,n):raise OSError('offline')
        for i in range(3):c.refresh(self.host,self.now+i,{'boat':fail},self.sender)
        self.refresh('1')
        for i in range(3):c.refresh(self.host,self.now+10+i,{'boat':fail},self.sender)
        self.assertEqual(self.sender.call_count,3)
        self.assertIn('Recovered: boat',self.sender.call_args_list[1].args[0])
        self.assertNotEqual(self.sender.call_args_list[0].args[1],self.sender.call_args_list[2].args[1])

    def test_all_unsigned_providers_stay_quiet_and_out_of_threshold_messages(self):
        names=['RunPod','Vast','AWS','Azure','OCI','Rackspace','Fly']
        unavailable=Mock(side_effect=OSError('must not poll unsigned provider'))
        readers={name:unavailable for name in names}
        for i in range(5):
            output=c.refresh(self.host,self.now+i,readers,self.sender,configured={})
        self.sender.assert_not_called();unavailable.assert_not_called()
        self.assertEqual(set(output.split('not set up: ')[1].rstrip('.').split(', ')),set(names))
        readers['boat']=lambda p,n:dict(amount='6')
        c.refresh(self.host,self.now+10,readers,self.sender,configured={'boat':True})
        self.sender.assert_called_once()
        text=self.sender.call_args.args[0]
        self.assertIn('crossed $5.00',text)
        for name in names:self.assertNotIn(name,text)

    def test_false_legacy_alarm_and_pending_notice_removed_without_recovery_notice(self):
        state=c.initial();state['providers']['Vast']=dict(failures=4,failure_since=1,failure_notified=True)
        state['pending']=dict(key='old',text='old false alarm',index=0,failures={'Vast':1})
        cu.atomic_write(self.root/c.STATE_NAME,json.dumps(state))
        c.refresh(self.host,self.now,{'Vast':Mock()},self.sender,configured={})
        self.sender.assert_not_called()
        state=c.read(self.root/c.STATE_NAME)
        self.assertIsNone(state['pending']);self.assertEqual(state['providers']['Vast']['failures'],0)

    def test_once_successful_provider_remains_real_outage_after_signout_then_recovers_once(self):
        self.refresh('2')
        fail=Mock(side_effect=OSError('signed out'))
        for i in range(4):c.refresh(self.host,self.now+i,{'boat':fail},self.sender,configured={})
        self.sender.assert_called_once()
        state=c.read(self.root/c.STATE_NAME)
        self.assertEqual(c.total(state),Decimal('2'))
        for i in range(2):c.refresh(self.host,self.now+10+i,{'boat':lambda p,n:dict(amount='2')},self.sender,configured={})
        self.assertEqual(self.sender.call_count,2)
        self.assertIn('Recovered: boat',self.sender.call_args.args[0])

    def test_failed_enqueue_then_recovery_drops_stale_unsent_failure(self):
        fail=Mock(side_effect=OSError('offline'))
        self.sender.return_value=False
        for i in range(3):c.refresh(self.host,self.now+i,{'boat':fail},self.sender,configured={'boat':True})
        self.assertEqual(self.sender.call_count,1)
        self.sender.reset_mock();self.sender.return_value=True
        c.refresh(self.host,self.now+10,{'boat':lambda p,n:dict(amount='2')},self.sender,configured={'boat':True})
        self.sender.assert_not_called()
        self.assertIsNone(c.read(self.root/c.STATE_NAME)['pending'])

    def test_present_credentials_alert_even_if_no_first_success(self):
        fail=Mock(side_effect=OSError('expired credential'))
        for i in range(3):c.refresh(self.host,self.now+i,{'RunPod':fail},self.sender,configured={'RunPod':True})
        self.sender.assert_called_once()
        self.assertIn('Three consecutive checks failed: RunPod',self.sender.call_args.args[0])

    def test_modal_et_boundary_before_credits_excludes_llm(self):
        data=[dict(interval_start='2025-11-11T04:00:00Z',resource='CPU',cost='100'),
              dict(interval_start='2025-11-11T05:00:00Z',resource='CPU',cost='2.30'),
              dict(interval_start='2025-11-11T06:00:00Z',resource='Memory',cost='1.20'),
              dict(interval_start='2025-11-11T06:00:00Z',resource='LLM Tokens',cost='50')]
        run=Mock(return_value=SimpleNamespace(returncode=0,stdout=json.dumps(data)))
        self.assertEqual(c.modal({},self.now,run)['amount'],'3.50')
        args=run.call_args.args[0]
        self.assertIn('--show-resources',args)
        self.assertIn('America/New_York',args)

    def test_boat_plan_value_weights_deleted_usage_extras_not_plan_fee_or_wallet_vm_twice(self):
        billing=dict(limits=dict(subscriptionCurrentPeriodEnd='2025-12-11T23:16:06Z',
            creditSecondsPerDollar=100000,creditUsedSeconds=200000,sandboxPlanDollars=20,
            keepGoing=dict(enabledAt='2025-11-11T23:17:00Z',usedDollars='.03')),
            walletStatus=dict(spending=dict(days=[dict(day='2025-11-10',startDollars=9,otherDollars=9),
                dict(day='2025-11-11',machineDollars=2,startDollars='.05',otherDollars='.056')])))
        listing=dict(sandboxes=[dict(id='large',type='large',billingMultiplier=2)])
        usage=dict(seconds=100000,dollars=1,billingMultiplier=2)
        run=Mock(side_effect=[SimpleNamespace(returncode=0,stdout=json.dumps(d)) for d in [billing,listing,usage]])
        result=c.boat({},self.now,run)
        self.assertEqual(c.amount(result['amount']),Decimal('2.136'))
        self.assertTrue(all('--no-update' in x.args[0] for x in run.call_args_list))
        self.assertIn('--since',run.call_args.args[0])

    def test_boat_disabled_extra_starts_need_no_enabled_timestamp(self):
        billing=dict(limits=dict(subscriptionCurrentPeriodEnd='2025-12-11T23:16:06Z',
            creditSecondsPerDollar=100000,creditUsedSeconds=100,
            keepGoing=dict(enabledAt=None,usedDollars=0)),
            walletStatus=dict(spending=dict(days=[])))
        run=Mock(side_effect=[SimpleNamespace(returncode=0,stdout=json.dumps(d))
                             for d in [billing,dict(sandboxes=[])]])
        self.assertEqual(c.amount(c.boat({},self.now,run)['amount']),Decimal('.001'))

    def test_vast_date_arguments_are_iso_dates_not_epoch_seconds(self):
        run=Mock(return_value=SimpleNamespace(returncode=0,stdout='[{"amount": "-1.25"}]'))
        self.assertEqual(c.vast({},self.now,run)['amount'],'1.25')
        args=run.call_args.args[0]
        self.assertEqual(args[args.index('--start_date')+1],c.START.isoformat())
        self.assertIn('T',args[args.index('--end_date')+1])

    def test_missing_or_bad_state_not_silently_reset(self):
        (self.root/c.STATE_NAME).write_text('{broken')
        with self.assertRaises(ValueError):self.refresh('10')
        self.sender.assert_not_called()

    def test_plain_cached_total_never_reads_provider(self):
        self.refresh('2')
        self.assertIn('boat $2.00',c.cached(self.host))
        self.assertEqual((self.root/c.STATE_NAME).stat().st_mode & 0o777,0o600)

    def test_send_uses_private_helper_stdin_and_stable_event_key_only(self):
        run=Mock(return_value=SimpleNamespace(returncode=0,stdout='queued\n'))
        self.assertTrue(c.send_notice('For you: cost', 'compute-123',run))
        self.assertEqual(run.call_args.kwargs['input'],'For you: cost')
        self.assertEqual(run.call_args.args[0][-2:],['--event-key','compute-123'])

    def test_compute_only_command_cannot_query_proxies(self):
        from contextlib import redirect_stdout
        from datetime import datetime
        import io
        with patch.object(c,'refresh',return_value='Compute total $1'),patch.object(cu,'fetch') as proxy,redirect_stdout(io.StringIO()):
            self.assertEqual(cu.main(['--compute-check'],clock=lambda:datetime.fromtimestamp(self.now,c.START.tzinfo)),0)
        proxy.assert_not_called()


if __name__=='__main__':unittest.main()


class CredentialsTests(unittest.TestCase):
    def test_empty_native_configs_are_not_logins_and_real_values_are_detected(self):
        with tempfile.TemporaryDirectory() as name:
            home=Path(name)
            for relative,text in [('.runpod/config.toml','apikey = ""\napiurl="https://example.invalid"'),
                                  ('.fly/config.yml','org: example\n'),('.modal.toml','[profile]\nactive=true'),
                                  ('.azure/azureProfile.json','\ufeff{"subscriptions": []}'),
                                  ('.oci/config','[DEFAULT]\nregion=us-ashburn-1')]:
                p=home/relative;p.parent.mkdir(parents=True,exist_ok=True);p.write_text(text)
            self.assertFalse(any(c.credential_status(home,{}).values()))
            (home/'.runpod/config.toml').write_text('apikey="fake-test-key"')
            (home/'.fly/config.yml').write_text('access_token: fake-test-key')
            status=c.credential_status(home,{})
            self.assertTrue(status['RunPod']);self.assertTrue(status['Fly'])
            self.assertFalse(status['Vast'])
            (home/'.oci/config').write_text('[DEFAULT]\ntenancy=fake\nuser=fake\nkey_file=/fake/key')
            self.assertTrue(c.credential_status(home,{})['OCI'])
            self.assertTrue(c.credential_status(home,{'VAST_API_KEY':'fake-test-key'})['Vast'])
