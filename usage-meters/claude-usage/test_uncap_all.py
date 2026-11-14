"""Human terminal shortcut; no live release, proxy requests or subprocesses."""
import copy
import importlib.machinery
import importlib.util
import io
import json
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import claude_usage_boxes as b
import test_categories as fixture

loader=importlib.machinery.SourceFileLoader('uncap_shortcut',str(Path(__file__).with_name('uncap')))
spec=importlib.util.spec_from_loader(loader.name,loader)
shortcut=importlib.util.module_from_spec(spec);loader.exec_module(shortcut)


class Terminal(io.StringIO):
    def __init__(self, interactive=True):
        super().__init__();self.interactive=interactive
    def isatty(self):
        return self.interactive


class Shortcut(unittest.TestCase):
    ledger=fixture.Categories.ledger
    record=fixture.Categories.record
    assertAuthUntouched=fixture.Categories.assertAuthUntouched

    def setUp(self):
        fixture.Categories.setUp(self)
        self.host.desktop_notify=lambda line: True
        self.host.MATRIX_COPY_ALL=True
        self.data=self.ledger();self.record(self.data)
        self.path=self.root/'category-ledger.json'
        self.path.write_text(json.dumps(self.data))
        self.inp,self.out=Terminal(),Terminal()
        patch.object(b,'append_alert',return_value=True).start()
        patch.object(b,'thread_notice',return_value='steered').start()

    def trip(self,category):
        reading=next(r for r in b.readings(self.policy,self.data,self.now) if r['category']==category)
        reading['used']=reading['allowed']+1
        b.enforce(self.host,self.policy,self.data,[reading],self.now)

    def test_refusal_before_loading_host_for_either_noninteractive_stream(self):
        for inp,out in ((Terminal(False),Terminal()),(Terminal(),Terminal(False))):
            with self.subTest(),patch.object(shortcut.importlib.machinery,'SourceFileLoader') as load:
                self.assertEqual(shortcut.main([],inp,out),2)
                load.assert_not_called()
                self.assertEqual(out.getvalue(),'uncap: refused; run it directly in an interactive terminal.\n')
        with patch.object(self.host,'guard_lock') as lock:
            self.assertEqual(b.hard_uncap_all(self.host,Terminal(False),Terminal()),2)
            lock.assert_not_called()

    def test_interactive_wrapper_takes_no_confirmation_and_dispatches(self):
        with patch.object(b,'hard_uncap_all',return_value=0) as release:
            self.assertEqual(shortcut.main([],self.inp,self.out),0)
        self.assertEqual(release.call_count,1)
        self.assertIs(release.call_args.args[1],self.inp)
        self.assertIs(release.call_args.args[2],self.out)
        # An empty interactive input was enough: no readline/typed word.
        self.assertEqual(self.inp.tell(),0)

    def test_invalid_minutes_refused_without_import_or_any_release(self):
        for arguments in (['codex'],['0'],['-1'],['nan'],['inf'],['1','2']):
            out=Terminal()
            with self.subTest(arguments=arguments),patch.object(shortcut.importlib.machinery,'SourceFileLoader') as load:
                self.assertEqual(shortcut.main(arguments,self.inp,out),2)
                load.assert_not_called()
            self.assertEqual(out.getvalue(),'usage: uncap [positive number of minutes]\n')

    def test_positive_minutes_grant_soft_credit_to_every_category_without_prompt(self):
        for category in b.CATEGORIES:self.trip(category)
        before=copy.deepcopy(self.data)
        with patch.object(b,'load',return_value=self.policy),patch.object(b,'soft_uncap',return_value=0) as soft:
            self.assertEqual(shortcut.main(['2.5'],self.inp,self.out),0)
        self.assertEqual([c.args[2] for c in soft.call_args_list],list(b.CATEGORIES))
        self.assertTrue(all(c.args[3]==2.5 for c in soft.call_args_list))
        self.assertTrue(all(c.kwargs['confirmer']() for c in soft.call_args_list))
        self.assertTrue(all(c.kwargs['only_if_capped'] is True for c in soft.call_args_list))
        self.assertEqual(len({c.kwargs['now'] for c in soft.call_args_list}),1)
        self.assertEqual(self.inp.tell(),0)
        self.assertEqual(self.data,before)

    def test_soft_shortfall_or_one_error_does_not_skip_other_categories(self):
        with patch.object(b,'load',return_value=self.policy),patch.object(b,'soft_uncap',side_effect=[1,OSError('missing'),0]) as soft:
            self.assertEqual(shortcut.main(['10'],self.inp,self.out),1)
        self.assertEqual([c.args[2] for c in soft.call_args_list],list(b.CATEGORIES))
        self.assertIn('Claude non-Fable: credit could not be applied',self.out.getvalue())

    def test_all_categories_and_accounts_restored_no_meter_reads(self):
        for category in b.CATEGORIES:self.trip(category)
        before=json.loads(self.path.read_text())
        # No hard stops: the trips are read out, and no auth file is edited.
        self.assertEqual([part.split(', ')[-1] for part in self.spoken[-1].split('. ')],['Fable','Opus','Codex.'])
        self.assertRegex(self.spoken[-1],r'^Measured mock, 1\d\d percent, Fable\. Measured mock, 1\d\d percent, Opus\. Measured mock, 1\d\d percent, Codex\.$')
        self.assertAuthUntouched()
        with patch.object(self.host,'fetch',side_effect=AssertionError('no management fetch')) as fetch, \
             patch.object(b,'matrix_notice',return_value=True) as matrix:
            self.assertEqual(b.hard_uncap_all(self.host,self.inp,self.out,now=self.now+1),0)
        fetch.assert_not_called()
        guard=self.host.guard_state(self.host.GUARD_STATE)
        self.assertEqual(guard['latches'],{})
        self.assertEqual(guard['files'],{})
        for provider,root in self.host.AUTH_DIRS.items():
            for path in root.glob('*.json'):
                auth=json.loads(path.read_text())
                self.assertFalse(auth.get('disabled',False))
                self.assertNotIn('*fable*',auth.get('excluded_models',[]))
                self.assertNotIn('*',auth.get('excluded_models',[]))
        after=json.loads(self.path.read_text())
        self.assertEqual(after['events'],before['events'])
        self.assertEqual(after['preparation'] if 'preparation' in after else {},before.get('preparation',{}))
        self.assertTrue(all(after['forgiven'][cat]==self.now+1 for cat in b.CATEGORIES))
        self.assertEqual(after['pending_rebase']['claude:account-b@example.com'],['fable','non-fable'])
        self.assertEqual(after['pending_rebase']['codex:account-c@example.com'],['codex'])
        self.assertEqual(matrix.call_count,3)
        self.assertTrue(all(call.args[0].startswith('RELEASED') for call in matrix.call_args_list))
        self.assertEqual(len(self.out.getvalue().splitlines()),3)
        self.assertFalse((self.root/'hard-continue.json').exists())
        self.assertIsNone(self.spoken[-1])  # every latch released: the voice stops
        self.assertAuthUntouched()

    def test_unknown_legacy_pool_also_removed_with_tombstone(self):
        guard=dict(latches={'legacy-other':dict(pool='other:legacy:week',box='old')},files={},released=[])
        self.host.atomic_write(self.host.GUARD_STATE,json.dumps(guard))
        self.assertEqual(b.hard_uncap_all(self.host,self.inp,self.out,now=self.now+1),0)
        after=self.host.guard_state(self.host.GUARD_STATE)
        self.assertEqual(after['latches'],{})
        self.assertIn('legacy-other',after['released'])
        self.assertIn('Other usage guards: 1 released.',self.out.getvalue())

    def test_legacy_only_state_without_category_ledger_can_release(self):
        self.path.unlink()
        guard=dict(latches={'old':dict(pool='codex:account-c@example.com:week',box='old')},files={},released=[])
        self.host.atomic_write(self.host.GUARD_STATE,json.dumps(guard))
        self.assertEqual(b.hard_uncap_all(self.host,self.inp,self.out,now=self.now+1),0)
        self.assertFalse(self.path.exists())
        self.assertFalse(self.host.guard_state(self.host.GUARD_STATE)['latches'])

    def test_failed_matrix_release_retains_outbox_for_periodic_retry(self):
        self.trip('fable')
        with patch.object(b,'matrix_notice',return_value=False):
            self.assertEqual(b.hard_uncap_all(self.host,self.inp,self.out,now=self.now+1),0)
        path=self.root/'hard-release-notices.json'
        with patch.object(b,'matrix_notice',return_value=True) as matrix:
            b.deliver_current(self.host,None,self.now+61,path)
        self.assertEqual(matrix.call_count,1)
        self.assertTrue(matrix.call_args.args[0].startswith('RELEASED  Fable:'))
        with patch.object(b,'matrix_notice') as matrix:
            b.deliver_current(self.host,None,self.now+121,path)
        matrix.assert_not_called()

    def test_full_account_key_releases_actual_legacy_latches(self):
        for pool,category in (('claude:account-b@example.com:fable','fable'),
                              ('codex:account-c@example.com:week','codex')):
            with self.subTest(pool=pool):
                guard=dict(latches={'legacy':dict(pool=pool,box='old')},files={},released=[])
                self.host.atomic_write(self.host.GUARD_STATE,json.dumps(guard))
                self.assertTrue(b.uncap(self.host,b.uncap_category(pool),self.path,self.policy,
                    confirmer=lambda _:True,reader=lambda:[]))
                saved=self.host.guard_state(self.host.GUARD_STATE)
                self.assertEqual(saved['latches'],{})
                self.assertIn('legacy',saved['released'])

    def test_pending_release_is_cancelled_if_category_has_stopped_again(self):
        self.trip('fable')
        with patch.object(b,'matrix_notice',return_value=False):
            self.assertEqual(b.hard_uncap_all(self.host,self.inp,self.out,now=self.now+1),0)
        self.trip('fable')
        with patch.object(b,'matrix_notice',return_value=True) as matrix:
            b.deliver_current(self.host,None,self.now+61,self.root/'hard-release-notices.json')
        matrix.assert_not_called()
        self.assertEqual(json.loads((self.root/'hard-release-notices.json').read_text())['records'],{})

    def test_real_soft_wrapper_grants_fixed_credit_only_to_capped_categories(self):
        # Credit goes only to categories that are capped right now.
        import outside_api_budget
        policy=b.load(Path(__file__).parent/'fixtures/boxes-preparation-v2.toml')
        policy['approved']=True
        for run in policy['runs']:run['approved']=True
        rates=b.rate_context(policy,self.data,self.now)['R']
        self.trip('codex')
        self.path.write_text(json.dumps(self.data))
        before=copy.deepcopy(self.data)
        def fake_host(module):module.__dict__.update(vars(self.host))
        with patch.object(shortcut.importlib.machinery.SourceFileLoader,'exec_module',side_effect=fake_host), \
             patch.object(outside_api_budget,'release_all',return_value=0), \
             patch.object(shortcut,'datetime') as clock,patch.object(b,'load',return_value=policy):
            clock.now.return_value.timestamp.return_value=self.now+1
            shortcut.main(['2.5'],self.inp,self.out)
        after=json.loads(self.path.read_text())
        self.assertAlmostEqual(b.soft_credit(after,'codex'),rates['codex']*2.5/60)
        for category in ('fable','non-fable'):
            self.assertEqual(b.soft_credit(after,category),0)
            self.assertIn(b.LABELS[category]+': not capped; skipped, no credit added.',self.out.getvalue())
        self.assertEqual(after['events'],before['events'])
        self.assertEqual(after['forgiven'],before['forgiven'])
        self.assertEqual(self.inp.tell(),0)
        self.assertEqual(len(self.out.getvalue().splitlines()),3)

    def test_legacy_full_key_without_ledger_keeps_pool_release_path(self):
        self.path.unlink()
        args=SimpleNamespace(boxes=self.root/'unused.toml',uncap='codex:account-c@example.com:week',
            accept_defaults=False,warn=False,log=False,passive=False,meter_check=False,test_box=None)
        with patch.object(b,'load',return_value=self.policy),patch.object(b,'uncap') as category, \
             patch.object(self.host,'uncap_pool',return_value=True) as legacy,patch('sys.stdout',new=io.StringIO()):
            self.assertEqual(b.cli(self.host,args,lambda:datetime.fromtimestamp(self.now,self.host.ET)),0)
        category.assert_not_called()
        legacy.assert_called_once_with(args.uncap)

    def test_account_key_alias_and_legacy_fallback_keep_old_cli_working(self):
        self.assertEqual(b.uncap_category('claude:account-b@example.com:fable'),'fable')
        self.assertEqual(b.uncap_category('claude:account-a@example.com:fable'),'fable')
        self.assertEqual(b.uncap_category('codex:account-c@example.com:week'),'codex')
        self.assertEqual(b.uncap_category('non-fable'),'non-fable')
        self.assertIsNone(b.uncap_category('other:legacy:week'))
        args=SimpleNamespace(boxes=self.root/'unused.toml',uncap='claude:account-b@example.com:fable',
            accept_defaults=False,warn=False,log=False,passive=False,meter_check=False,test_box=None)
        with patch.object(b,'load',return_value=self.policy),patch.object(b,'uncap',return_value=True) as release, \
             patch('sys.stdout',new=io.StringIO()):
            self.assertEqual(b.cli(self.host,args,lambda:datetime.fromtimestamp(self.now,self.host.ET)),0)
        self.assertEqual(release.call_args.args[1],'fable')
