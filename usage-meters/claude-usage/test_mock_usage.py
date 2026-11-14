"""Read-only subordinate mock boxes. Token estimates, never proxy enforcement.

Only operational T3 identities and usage counters are selected/retained. The
overall boxes already count this traffic: never add these subtotals to them.
"""
import sys
sys.dont_write_bytecode = True
from contextlib import closing
from datetime import datetime, timezone
import json
import math
import hashlib
from pathlib import Path
import re
import sqlite3
import time

import claude_usage_boxes as boxes

PREFIX = 'orchestration_v2_projection_'
MOCK_RUN_GLOB = str(Path.home() / 'mock') + '/run-*'  # test-run workspaces: ~/mock/run-*
SESSION = re.compile(r'[a-zA-Z0-9_-]{1,128}\Z')
MAX_BYTES = 64 * 1024 * 1024


def readonly(path):
    db = sqlite3.connect(Path(path).resolve().as_uri() + '?mode=ro', uri=True, timeout=.5)
    db.execute('PRAGMA query_only=ON')
    db.execute('BEGIN')
    return db


def bindings(db):
    """Project membership plus explicit child edges; no messages or briefs."""
    projects = db.execute("SELECT project_id FROM projection_projects WHERE title GLOB 'Mock *' AND workspace_root GLOB ?", (MOCK_RUN_GLOB,)).fetchall()
    project_ids = {p[0] for p in projects}
    threads = db.execute(f"SELECT thread_id,project_id,json_extract(payload_json,'$.lineage.parentThreadId') FROM {PREFIX}threads").fetchall()
    selected = {tid for tid, pid, parent in threads if pid in project_ids}
    edges = [(parent, tid) for tid, pid, parent in threads if parent]
    edges += db.execute(f'SELECT thread_id,child_thread_id FROM {PREFIX}subagents WHERE child_thread_id IS NOT NULL').fetchall()
    while True:
        more = {child for parent, child in edges if parent in selected}
        if more <= selected:
            break
        selected |= more
    rows = db.execute(f"""SELECT thread_id,driver,
        json_extract(payload_json,'$.nativeThreadRef.nativeId')
        FROM {PREFIX}provider_threads""").fetchall()
    grouped = {}
    missing = set()
    for tid, driver, native in rows:
        provider = 'claude' if driver == 'claudeAgent' else 'codex' if driver == 'codex' else None
        if tid in selected and (provider is None or not isinstance(native, str) or not SESSION.fullmatch(native)):
            missing.add(provider or 'all')
        if provider and isinstance(native, str) and SESSION.fullmatch(native):
            grouped.setdefault((provider, native), set()).add(tid)
    sessions = set()
    for identity, owners in grouped.items():
        if owners & selected:
            if not owners <= selected:
                missing.add(identity[0])  # shared organizer/mock session is ambiguous
            else:
                sessions.add(identity)
    return sessions, missing, len(project_ids), len(selected)


def session_files(home, sessions, start=None):
    found = {identity: [] for identity in sessions}
    extra = []
    for provider, root in [('claude', home/'.claude/projects'), ('codex', home/'.codex/sessions')]:
        identities = {native for p, native in sessions if p == provider}
        if not root.is_dir() and identities:
            continue
        for path in root.rglob('*.jsonl'):
            # Exact native UUID suffix/path, not workspace/content inference.
            for native in identities:
                match = path.stem == native if provider == 'claude' else path.stem.endswith('-' + native)
                if match:
                    found[(provider, native)].append(path)
                elif provider == 'claude' and native in path.parts and path.parent.name == 'subagents':
                    extra.append((provider, path))
    files = {(provider, path) for (provider, native), paths in found.items() for path in paths}
    files.update(extra)
    missing = {provider for (provider, native), paths in found.items() if len(paths) != 1}
    # Native Codex spawn_agent children need not have a T3 thread. Their first
    # session_meta record has an explicit parent native UUID; never infer from
    # model, name, workspace, prompt or time proximity.
    known = {native for provider,native in sessions if provider == 'codex'}
    children = []
    root = home/'.codex/sessions'
    for path in root.rglob('*.jsonl'):
        if start is not None and path.stat().st_mtime < start:
            continue
        try:
            with path.open('rb') as stream:
                raw = stream.readline(1048576)
            record = json.loads(raw)
            if record.get('type') != 'session_meta':
                continue
            meta = record.get('payload') or {}
            source = meta.get('source')
            if not isinstance(source,dict):
                continue
            subagent = source.get('subagent')
            spawn = subagent.get('thread_spawn') if isinstance(subagent,dict) else None
            parent = spawn.get('parent_thread_id') if isinstance(spawn,dict) else None
            native = meta.get('id') or meta.get('session_id')
            if isinstance(parent,str) and isinstance(native,str) and SESSION.fullmatch(native):
                children.append((parent,native,path))
        except (OSError,ValueError,TypeError):
            missing.add('codex')
    while True:
        more = {native for parent,native,path in children if parent in known}
        if more <= known:
            break
        known |= more
    files.update(('codex',path) for parent,native,path in children if parent in known)
    return sorted(files, key=lambda item: str(item[1])), missing


def usage_events(files, at, budget=MAX_BYTES, state=None):
    events, bad = [], set()
    state = {} if state is None else state
    counters = state.setdefault('counters', {})
    cursors = state.setdefault('files', {})
    for provider, path in files:
        try:
            stat = path.stat()
            size = stat.st_size
            identity = hashlib.sha256(str(path).encode()).hexdigest()
            cursor = cursors.setdefault(identity,dict(offset=0,inode=stat.st_ino,previous={}))
            if cursor['inode'] != stat.st_ino or size < cursor['offset']:
                bad.add(provider)  # never reread a rewritten session as new spend
                state['permanent_bad']=sorted(set(state.get('permanent_bad',[])) | {provider})
                continue
            if size-cursor['offset'] > budget:
                bad.add(provider)
                continue
            budget -= size-cursor['offset']
            previous = counters if provider == 'claude' else cursor['previous']
            with path.open('rb') as stream:
                stream.seek(cursor['offset'])
                remaining = size-cursor['offset']
                while remaining:
                    raw = stream.readline(min(remaining, 1048576))
                    remaining -= len(raw)
                    if not raw or not raw.endswith(b'\n'):
                        bad.add(provider)
                        break
                    cursor['offset']=stream.tell()
                    if b'"usage"' not in raw and b'"token_count"' not in raw:
                        continue
                    record = json.loads(raw)
                    # Discard all text/tool fields before the existing extractor.
                    if provider == 'claude':
                        msg = record.get('message') or {}
                        safe = dict(timestamp=record.get('timestamp'), type=record.get('type'),
                                    message={k:msg[k] for k in ('id','usage','model') if k in msg})
                    else:
                        payload = record.get('payload') or {}
                        safe = dict(timestamp=record.get('timestamp'), type=record.get('type'),
                                    payload={k:payload[k] for k in ('type','info') if k in payload})
                    point = boxes.usage_record(safe, provider, previous)
                    if point:
                        if point[0] > at + 5:
                            bad.add(provider)
                            state['permanent_bad']=sorted(set(state.get('permanent_bad',[])) | {provider})
                        elif all(math.isfinite(x) and x >= 0 for x in (point[0], point[2])):
                            events.append(point)
                        else:
                            bad.add(provider)
        except (OSError, ValueError, TypeError, KeyError, OverflowError):
            bad.add(provider)
            state['permanent_bad']=sorted(set(state.get('permanent_bad',[])) | {provider})
    return events, bad


def summarize(config, policy, events, bad, at):
    start, end = boxes.stamp(config['start']), boxes.stamp(config['end'])
    if end <= start or config['schema'] != 1 or config['share'] != .25:
        raise ValueError('invalid mock allocation')
    elapsed = max(0, min(at, end)-start) / 3600
    hours = (end-start)/3600
    categories = []
    for category in boxes.CATEGORIES:
        amount = boxes.finite(config['budgets'][category], True)
        rate = amount / hours
        factor = boxes.finite(config.get('points_per_million',policy['rate_points_per_million'])[category], True)/1e6
        unknown = 'all' in bad or ('codex' if category == 'codex' else 'claude') in bad
        points = [(t, n*factor) for t, c, n in events if c == category and start <= t <= min(at,end)]
        total = sum(n for t,n in points)
        windows = {}
        pause, slow = total >= amount, False
        for minutes, fixed, default_stop in boxes.RATE_LADDER:
            used = sum(n for t,n in points if max(start,at-minutes*60) < t <= min(at,end))
            multiple = used/(rate*minutes/60)
            stop = policy['preparation'].get('stops', {}).get(str(minutes),default_stop)
            levels = boxes.notify_levels(minutes,stop)
            pause |= multiple >= stop
            slow |= multiple >= levels[0]
            windows[str(minutes)] = dict(target_percent=None if unknown else multiple*100,
                status='unknown' if unknown else 'estimated',
                warning_target_percents=[n*100 for n in levels], stop_target_percent=stop*100)
        categories.append(dict(category=category, budget=amount, unit='points',
            used_estimate=None if unknown else total, partial_usage_estimate=total,
            budget_percent=None if unknown else total/amount*100,
            average_target_percent=None if unknown or not elapsed else total/(rate*elapsed)*100,
            target_per_hour=rate, status='unknown' if unknown else 'estimated', windows=windows,
            recommendation='hold_unknown' if unknown else 'pause_mocks' if pause else 'slow_mocks' if slow else 'within_target_ladder'))
    return categories


def collect(host, policy, at):
    """Existing passive job only; append usage deltas, no delivery/enforcement."""
    root = host.HOME/'.local/state/claude-usage'
    config_path = host.HOME/'.config/claude-usage/test-mocks.json'
    if not config_path.exists():
        return
    path = root/'test-mock-ledger.json'
    with host.guard_lock(path):
        try:
            config = json.loads(config_path.read_text())
            signature = hashlib.sha256(config_path.read_bytes()).hexdigest()
            state = json.loads(path.read_text()) if path.exists() else dict(schema=1,events=[],config_sha256=signature)
            if state.get('config_sha256') != signature or state.get('schema') != 1:
                raise ValueError('allocation changed; preserve historical ledger')
            with closing(readonly(host.HOME/'.t3/userdata/statev2.sqlite')) as db:
                sessions, bad, projects, threads = bindings(db)
            if not sessions or not projects:
                bad.add('all')
            files, missing = session_files(host.HOME, sessions,boxes.stamp(config['start']))
            events, invalid = usage_events(files, at, state=state)
            state['events'] += [e for e in events if boxes.stamp(config['start']) <= e[0] <= boxes.stamp(config['end'])]
            # Parsing/rewrite errors can skip historical counters and must not
            # disappear on the next flat sample. Missing files can recover.
            state['invalid_providers'] = state.get('permanent_bad',[])
            state.update(at=at,projects=projects,threads=threads,sessions=len(sessions),file_count=len(files),
                         bad=sorted(bad | missing | invalid | set(state['invalid_providers'])))
            host.atomic_write(path,json.dumps(state,sort_keys=True)+'\n')
        except (OSError,ValueError,TypeError,KeyError,sqlite3.Error):
            # Keep the last good ledger; read-only consumers deny stale data.
            return


def snapshot(home=None, at=None):
    home = Path.home() if home is None else Path(home)
    at = time.time() if at is None else at
    result = dict(schema=1, scope='test-mocks', measured_at=datetime.fromtimestamp(at,timezone.utc).isoformat(),
                  unit='target_percent', accounting='subtotals_already_in_overall_boxes',
                  enforcement='organizer_paces_mocks; overall_proxy_guards_unchanged', categories=[])
    try:
        config = json.loads((home/'.config/claude-usage/test-mocks.json').read_text())
        policy = boxes.load(home/'.config/claude-usage/boxes.toml')
        if not policy['approved']:
            raise ValueError()
        result.update(start=config['start'],end=config['end'],share=config['share'],budget_basis=config['budget_basis'])
        state = json.loads((home/'.local/state/claude-usage/test-mock-ledger.json').read_text())
        if (state.get('schema') != 1 or state['config_sha256'] != hashlib.sha256((home/'.config/claude-usage/test-mocks.json').read_bytes()).hexdigest()
                or not 0 <= at-state['at'] <= 90):
            raise ValueError('scoped sample stale or incoherent')
        bad=set(state['bad']);events=state['events']
        result['coverage'] = dict(projects=state['projects'],threads=state['threads'],sessions=state['sessions'],files=state['file_count'],
                                  sampled_at=datetime.fromtimestamp(state['at'],timezone.utc).isoformat(),age_seconds=at-state['at'],max_age_seconds=90,
                                  unknown_providers=sorted(bad),source='local_native_session_usage',
                                  method='existing_passive_token_point_estimate; not_provider_meter_attribution')
        result['categories'] = summarize(config,policy,events,bad,at)
        # No caller has a mock provenance field yet. All-global-zero proves
        # scoped zero; any positive untagged call remains unknown, never guessed.
        with closing(readonly(home/'.local/state/outside-api-budget/budget.sqlite')) as db:
            cap = float(dict(db.execute('SELECT key,value FROM settings'))['cap'])
            calls = db.execute('SELECT upper_usd,actual_usd FROM calls').fetchall()
        known_zero = all(float(upper if actual is None else actual) == 0 for upper,actual in calls)
        amount = boxes.finite(config['budgets']['outside-api'],True)
        if not math.isfinite(cap) or cap <= 0:
            raise ValueError()
        result['categories'].append(dict(category='outside-api',unit='USD',budget=amount,
            used_estimate=0 if known_zero else None,status='exact' if known_zero else 'unknown',
            reason=None if known_zero else 'outside_call_scope_unrecorded',target_per_hour=amount/((boxes.stamp(config['end'])-boxes.stamp(config['start']))/3600),
            recommendation='within_target_ladder' if known_zero else 'hold_unknown',
            windows={str(m):dict(target_percent=0 if known_zero else None,status='exact' if known_zero else 'unknown') for m,_,_ in boxes.RATE_LADDER}))
    except (OSError, ValueError, TypeError, KeyError, sqlite3.Error):
        result['status']='unknown'
        result['recommendation']='hold_unknown'
        return result
    result['status']='unknown' if any(c['status']=='unknown' for c in result['categories']) else 'estimated'
    return result


def main():
    if len(sys.argv) != 1:
        return 2
    print(json.dumps(snapshot(),allow_nan=False,sort_keys=True))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
