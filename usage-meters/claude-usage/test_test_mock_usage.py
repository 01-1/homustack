"""Scoped offline fixtures; no model traffic, live state or credential reads."""
import copy
from contextlib import nullcontext
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from types import SimpleNamespace

import test_mock_usage as mock

MOCK = str(Path.home() / 'mock')


class MockBoxes(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.home=Path(self.tmp.name)
        self.start=mock.boxes.stamp('2025-11-12T05:44:00-05:00');self.at=self.start+3600
        self.config=dict(schema=1,start='2025-11-12T05:44:00-05:00',end='2025-11-13T15:00:00-05:00',share=.25,
                         budget_basis='25% of frozen preparation total allocation P',
                         budgets=dict(fable=25.,**{'non-fable':25.,'codex':25.,'outside-api':10.}))
        self.policy=dict(rate_points_per_million=dict.fromkeys(mock.boxes.CATEGORIES,1),preparation={})

    def sql(self):
        db=sqlite3.connect(':memory:')
        db.executescript('''CREATE TABLE projection_projects(project_id,title,workspace_root);
        CREATE TABLE orchestration_v2_projection_threads(thread_id,project_id,payload_json);
        CREATE TABLE orchestration_v2_projection_subagents(thread_id,child_thread_id);
        CREATE TABLE orchestration_v2_projection_provider_threads(thread_id,driver,payload_json);''')
        db.execute('INSERT INTO projection_projects VALUES (?,?,?)',('mock','Mock stand-in: seat',MOCK+'/run-date-standin-a'))
        db.execute('INSERT INTO projection_projects VALUES (?,?,?)',('main','main project',str(Path.home()/'code/main')))
        return db

    def thread(self,db,tid,pid,native,driver='codex',parent=None):
        db.execute('INSERT INTO orchestration_v2_projection_threads VALUES (?,?,?)',(tid,pid,json.dumps({'lineage':{'parentThreadId':parent}})))
        db.execute('INSERT INTO orchestration_v2_projection_provider_threads VALUES (?,?,?)',(tid,driver,json.dumps({'nativeThreadRef':{'nativeId':native}})))

    def test_project_identity_children_and_shared_session_refusal(self):
        db=self.sql();self.addCleanup(db.close)
        self.thread(db,'root','mock','native1')
        self.thread(db,'child','main','native2',parent='root')
        self.thread(db,'delegate','main','native3')
        db.execute('INSERT INTO orchestration_v2_projection_subagents VALUES (?,?)',('child','delegate'))
        self.thread(db,'organizer','main','native4')
        sessions,bad,projects,threads=mock.bindings(db)
        self.assertEqual(sessions,{('codex','native1'),('codex','native2'),('codex','native3')})
        self.assertEqual((bad,projects,threads),(set(),1,3))
        self.thread(db,'foreign','main','native1')
        sessions,bad,_,_=mock.bindings(db)
        self.assertNotIn(('codex','native1'),sessions);self.assertEqual(bad,{'codex'})

    def test_live_and_future_mock_projects_require_title_and_run_root(self):
        db=self.sql();self.addCleanup(db.close)
        projects=[
            ('live','Mock live: Seat A',MOCK+'/run-2026-01-01-live-seat-a'),
            ('seatb','Mock live: Seat B',MOCK+'/run-2026-01-01-live-seat-b'),
            ('future','Mock next: seat',MOCK+'/run-next-seat'),
            ('stray','Mock Live: Seat B',str(Path.home()/'.t3/projects/mock-live-seat-b')),
            ('other','Other',MOCK+'/run-next-other'),
        ]
        db.executemany('INSERT INTO projection_projects VALUES (?,?,?)',projects)
        for pid,_,_ in projects:
            self.thread(db,pid,pid,'native-'+pid)
        sessions,bad,projects,threads=mock.bindings(db)
        self.assertEqual(sessions,{('codex','native-'+pid) for pid in ('live','seatb','future')})
        self.assertEqual((bad,projects,threads),(set(),4,3))

    def test_exact_file_binding_includes_claude_native_children(self):
        root=self.home/'.claude/projects/work';root.mkdir(parents=True)
        main=root/'session1.jsonl';main.write_text('')
        child=root/'session1/subagents/agent-child.jsonl';child.parent.mkdir(parents=True);child.write_text('')
        (root/'session1-other.jsonl').write_text('')
        files,bad=mock.session_files(self.home,{('claude','session1')})
        self.assertEqual({p for provider,p in files},{main,child});self.assertFalse(bad)

    def test_claude_message_dedup_codex_cached_and_reasoning_not_added(self):
        timestamp='2025-11-12T11:40:00Z'
        claude=dict(type='assistant',timestamp=timestamp,message=dict(id='message1',model='claude-fable-5-2',
            content='PRIVATE FIXTURE MUST NEVER LEAVE',usage=dict(input_tokens=100,output_tokens=10,cache_read_input_tokens=100)))
        c=self.home/'c.jsonl';c.write_text(json.dumps(claude)+'\n'+json.dumps(claude)+'\n')
        codex=dict(type='event_msg',timestamp=timestamp,payload=dict(type='token_count',info=dict(
            total_token_usage=dict(input_tokens=1000,cached_input_tokens=500,output_tokens=20,reasoning_output_tokens=10),
            last_token_usage=dict(input_tokens=1000,cached_input_tokens=500,output_tokens=20,reasoning_output_tokens=10))))
        x=self.home/'x.jsonl';x.write_text(json.dumps(codex)+'\n'+json.dumps(codex)+'\n')
        events,bad=mock.usage_events([('claude',c),('codex',x)],self.at)
        self.assertFalse(bad);self.assertEqual([e[2] for e in events],[160.,650.])
        self.assertNotIn('PRIVATE',json.dumps(events))

    def test_partial_missing_oversize_evidence_never_zero(self):
        p=self.home/'partial.jsonl';p.write_bytes(b'{"usage":')
        events,bad=mock.usage_events([('claude',p)],self.at)
        rows=mock.summarize(self.config,self.policy,events,bad,self.at)
        self.assertIsNone(rows[0]['used_estimate']);self.assertEqual(rows[0]['recommendation'],'hold_unknown')
        self.assertEqual(mock.usage_events([('codex',p)],self.at,budget=1)[1],{'codex'})
        _,bad=mock.session_files(self.home,{('codex','missing')});self.assertEqual(bad,{'codex'})

    def test_budget_average_window_and_existing_ladder(self):
        events=[(self.at-1,'codex',1_000_000)]
        rows=mock.summarize(self.config,self.policy,events,set(),self.at)
        row=rows[2];hours=(mock.boxes.stamp(self.config['end'])-self.start)/3600
        self.assertEqual(row['used_estimate'],1.)
        self.assertAlmostEqual(row['target_per_hour'],25/hours)
        self.assertAlmostEqual(row['average_target_percent'],100/(25/hours))
        self.assertEqual(row['windows']['5']['warning_target_percents'],[200.,250.,275.])
        self.assertEqual(row['windows']['5']['stop_target_percent'],300.)
        self.assertEqual(row['recommendation'],'pause_mocks')

    def test_before_start_and_after_end_do_not_add_usage(self):
        end=mock.boxes.stamp(self.config['end'])
        events=[(self.start-1,'fable',1e6),(end+1,'fable',1e6),(self.start+1,'fable',2e6)]
        before=copy.deepcopy(events)
        rows=mock.summarize(self.config,self.policy,events,set(),end+3600)
        self.assertEqual(rows[0]['used_estimate'],2);self.assertEqual(events,before)

    def test_readonly_connection_does_not_create_missing_db_or_allow_writes(self):
        p=self.home/'state.sqlite'
        with self.assertRaises(sqlite3.Error):mock.readonly(p)
        self.assertFalse(p.exists())
        with sqlite3.connect(p) as db:db.execute('create table fixture(n)')
        before=p.read_bytes()
        db=mock.readonly(p);self.addCleanup(db.close)
        with self.assertRaises(sqlite3.Error):db.execute('insert into fixture values (1)')
        self.assertEqual(before,p.read_bytes())

    def test_incremental_collect_readonly_snapshot_stale_and_partial_recovery(self):
        config_path=self.home/'.config/claude-usage';config_path.mkdir(parents=True)
        (config_path/'test-mocks.json').write_text(json.dumps(self.config))
        text=Path(__file__).with_name('fixtures').joinpath('boxes-preparation-v2.toml').read_text()
        (config_path/'boxes.toml').write_text(text.replace('approved = false','approved = true'))
        policy=mock.boxes.load(config_path/'boxes.toml')
        state_path=self.home/'.local/state/claude-usage';state_path.mkdir(parents=True)
        db_path=self.home/'.t3/userdata/statev2.sqlite';db_path.parent.mkdir(parents=True)
        db=self.sql();self.thread(db,'root','mock','session1','claudeAgent');db.commit()
        with sqlite3.connect(db_path) as dest:db.backup(dest)
        db.close()
        root=self.home/'.claude/projects/scope';root.mkdir(parents=True)
        log=root/'session1.jsonl'
        record=dict(type='assistant',timestamp='2025-11-12T11:40:00Z',message=dict(id='1',model='claude-fable-5-2',usage=dict(input_tokens=100)))
        log.write_text(json.dumps(record)+'\n')
        api_path=self.home/'.local/state/outside-api-budget/budget.sqlite';api_path.parent.mkdir(parents=True)
        with sqlite3.connect(api_path) as db:
            db.executescript("CREATE TABLE settings(key,value);INSERT INTO settings VALUES ('cap','40');CREATE TABLE calls(upper_usd,actual_usd);")
        host=SimpleNamespace(HOME=self.home,guard_lock=lambda p:nullcontext(),atomic_write=lambda p,t:p.write_text(t))
        mock.collect(host,policy,self.at);first=mock.snapshot(self.home,self.at)
        self.assertEqual(first['status'],'estimated');self.assertEqual(first['categories'][3]['used_estimate'],0)
        total=first['categories'][0]['used_estimate'];self.assertGreater(total,0)
        mock.collect(host,policy,self.at+1)
        before={str(p):p.read_bytes() for p in self.home.rglob('*') if p.is_file()}
        self.assertEqual(mock.snapshot(self.home,self.at+1)['categories'][0]['used_estimate'],total)
        self.assertEqual(before,{str(p):p.read_bytes() for p in self.home.rglob('*') if p.is_file()})
        self.assertEqual(mock.snapshot(self.home,self.at+92)['status'],'unknown')
        with log.open('a') as f:f.write('{"usage":')
        mock.collect(host,policy,self.at+2)
        self.assertEqual(mock.snapshot(self.home,self.at+2)['categories'][0]['status'],'unknown')
        with log.open('a') as f:f.write('null}\n')
        mock.collect(host,policy,self.at+3)
        self.assertEqual(mock.snapshot(self.home,self.at+3)['categories'][0]['used_estimate'],total)

    def test_native_codex_child_uses_explicit_parent_metadata(self):
        root=self.home/'.codex/sessions';root.mkdir(parents=True)
        parent=root/'rollout-parent.jsonl';parent.write_text('{}\n')
        child=root/'rollout-child.jsonl'
        child.write_text(json.dumps(dict(type='session_meta',payload=dict(id='child',source=dict(subagent=dict(thread_spawn=dict(parent_thread_id='parent'))))))+'\n')
        files,bad=mock.session_files(self.home,{('codex','parent')})
        self.assertFalse(bad);self.assertEqual({p for _,p in files},{parent,child})

    def test_legacy_subagent_variant_cannot_crash_parent_collection(self):
        root=self.home/'.codex/sessions';root.mkdir(parents=True)
        parent=root/'rollout-parent.jsonl';parent.write_text('{}\n')
        (root/'review.jsonl').write_text(json.dumps(dict(type='session_meta',payload=dict(id='review',source={'subagent':'review'})))+'\n')
        files,bad=mock.session_files(self.home,{('codex','parent')})
        self.assertFalse(bad);self.assertEqual(files,[('codex',parent)])

    def test_future_event_remains_unknown_after_flat_resample(self):
        p=self.home/'future.jsonl'
        p.write_text(json.dumps(dict(type='assistant',timestamp='2025-11-13T11:40:00Z',message=dict(id='future',model='claude-fable-5-2',usage=dict(input_tokens=100))))+'\n')
        state={};events,bad=mock.usage_events([('claude',p)],self.at,state=state)
        self.assertEqual((events,bad),([],{'claude'}))
        mock.usage_events([('claude',p)],self.at+1,state=state)
        self.assertEqual(state['permanent_bad'],['claude'])


if __name__=='__main__':unittest.main()
