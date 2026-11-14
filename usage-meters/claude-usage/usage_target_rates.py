"""Read-only MAIN category target-% snapshot; never an admission/enforcement API."""
import sys
sys.dont_write_bytecode = True
import copy
from contextlib import closing
from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3
import time

import claude_usage_boxes as boxes
import outside_api_budget as outside
from usage_helper_admission import number, read_json, signature

WINDOWS = [item[0] for item in boxes.RATE_LADDER]
LABELS = {'fable':'Fable', 'non-fable':'Claude non-Fable', 'codex':'Codex', 'outside-api':'Outside APIs'}
LIMITS = {'ledger_seconds':120, 'sampler_seconds':90, 'source_seconds':1200}


def blank(key, status='unavailable', reason='cached_data_unavailable'):
    return dict(category=key, label=LABELS[key], target_per_hour=None,
                windows={str(m):dict(target_percent=None, status=status, reason=reason) for m in WINDOWS})


def invalidate(row, status, reason):
    for cell in row['windows'].values():
        cell.update(target_percent=None, status=status, reason=reason)


def snapshot(home=None, at=None):
    home = Path.home() if home is None else Path(home)
    at = time.time() if at is None else number(at)
    result = dict(schema=1, scope='main', unit='target_percent', measured_at=datetime.fromtimestamp(at,timezone.utc).isoformat(),
                  windows_minutes=WINDOWS, context=None, scale=None, freshness_limits=LIMITS,
                  freshness=dict(ledger_age_seconds=None, sampler_age_seconds=None),
                  categories=[blank(k) for k in LABELS])
    policy_path = home/'.config/claude-usage/boxes.toml'
    root = home/'.local/state/claude-usage'
    paths=[policy_path,root/'category-ledger.json',root/'passive-rate.json']
    try:
        before=[signature(p) for p in paths]
        if any(s is None for s in before):raise ValueError()
        policy=boxes.load(policy_path)
        if not policy['approved']:raise ValueError()
        ledger,samples=read_json(paths[1]),read_json(paths[2])
        ages=[at-before[i][2]/1e9 for i in (1,2)]
        result['freshness'].update(ledger_age_seconds=ages[0],sampler_age_seconds=ages[1])
        if any(a<0 for a in ages) or before[0][2]>before[1][2]:raise ValueError()
        # Never fabricate a new preparation/run baseline while reading.
        saved=ledger['preparation'][str(policy['preparation']['start'])]
        if not 0<=number(saved['at'])<=at:raise ValueError()
        run=boxes.active_run(policy,at)
        if run and (not run['approved'] or boxes.run_id(run) not in ledger['runs']):raise ValueError()
        # One private copy: the producer never writes the ledger back, and
        # ladder_evaluate runs every window on one ledger too; 13 deepcopies took >2 s.
        ledger=copy.deepcopy(ledger)
        context=boxes.rate_context(policy,ledger,at)
        result.update(context=context['name'],scale=context['scale'])
        for row in result['categories'][:3]:
            category=row['category']
            row['target_per_hour']=number(context['R'][category])
            if row['target_per_hour']<=0:raise ValueError()
            if ages[0]>LIMITS['ledger_seconds'] or ages[1]>LIMITS['sampler_seconds']:
                invalidate(row,'stale','snapshot_stale');continue
            sample_age=at-number(samples['token_last_sampled'])
            if not 0<=sample_age<=LIMITS['sampler_seconds']:
                invalidate(row,'stale','sampler_stale');continue
            relevant=[s for s in policy['sources'] if (s['provider']=='codex')==(category=='codex')]
            source_bad=False
            excluded_boundary=0
            for source in relevant:
                current=ledger['sources'][source['provider']+':'+source['email']]
                meter=current['row']
                field = ('fable_observed_at' if category=='fable' else
                         'nf_observed_at' if category=='non-fable' else 'observed_at')
                observed = meter.get(field)
                # Without a paired NF gauge, the existing gauge is 2A-F and
                # requires both measured snapshots. Never use fresh A for old F.
                try:
                    if category=='non-fable' and meter.get('nf_points') is None:
                        observed=min(number(meter.get('observed_at')),number(meter.get('fable_observed_at')))
                    age=at-number(observed)
                except (ValueError,TypeError):
                    source_bad=True
                    continue
                # An account whose weekly window is exhausted until a future reset takes no
                # requests, so its meter cannot change until then: its reading stays current.
                try:
                    exhausted = number(meter.get('u7'))>=1 and number(meter.get('r7'))>at
                except (ValueError,TypeError):
                    exhausted = False
                # Only windows entirely covered by observed exclusion can use
                # an unchanged old gauge. Current exclusion alone is insufficient.
                excluded=False
                if category=='fable' and meter.get('fable_excluded') is True:
                    try:
                        since=number(meter.get('fable_excluded_since'))
                        if 0<=since<=at:
                            excluded=True
                            excluded_boundary=max(excluded_boundary,since)
                    except (ValueError,TypeError):
                        pass
                if exhausted or excluded:
                    age = 0
                if not 0<=age<=LIMITS['source_seconds'] or not 0<=at-number(current['time'])<=LIMITS['ledger_seconds']:
                    source_bad=True
                if boxes.gauges(meter).get(category) is None or category in current.get('carried',{}):
                    source_bad=True
            for minutes,fixed,_ in boxes.RATE_LADDER:
                cell=row['windows'][str(minutes)]
                window=boxes.target_rate_window(policy,ledger,samples,context,category,minutes,fixed,at)
                lower=window['lower']
                if source_bad:
                    cell.update(status='stale',reason='source_coverage_stale');continue
                if excluded_boundary>lower:
                    cell.update(status='incomplete',reason='exclusion_coverage_incomplete');continue
                if (samples.get('token_complete') is not True or not 0<=number(samples['token_since'])<=lower
                        or any(g[1]==category and lower<number(g[0])<=at for g in ledger['gaps'])):
                    cell.update(status='incomplete',reason='window_coverage_incomplete');continue
                value=number(window['multiple'])*100
                if value<0:raise ValueError()
                cell.update(target_percent=value,status='ok',reason=None)
        if before!=[signature(p) for p in paths]:
            for row in result['categories'][:3]:invalidate(row,'unavailable','snapshot_changed')
    except Exception:
        for row in result['categories'][:3]:invalidate(row,'unavailable','cached_data_unavailable')
    # Independent local dollar ledger: readonly URI, no schema creation/evaluation.
    row=result['categories'][3]
    try:
        before_policy=signature(policy_path)
        db_path=home/'.local/state/outside-api-budget/budget.sqlite'
        with closing(sqlite3.connect(db_path.as_uri()+'?mode=ro',uri=True,timeout=.2)) as db:
            db.execute('PRAGMA query_only=ON')
            db.execute('BEGIN')
            cfg,rate,rules,_,_=outside.readings(db,at,outside.rate_policy(at,policy_path))
            row['target_per_hour']=number(float(rate))
            for rule,used,_,_,denominator in rules[1:]:
                m=rule.split('-')[0]
                value=float(100*max(outside.D(0),used-outside.money(cfg['credit']))/denominator)
                row['windows'][m].update(target_percent=number(value),status='ok',reason=None)
        if signature(policy_path)!=before_policy:raise ValueError()
    except Exception:
        invalidate(row,'unavailable','outside_ledger_unavailable')
    return result


def main():
    if len(sys.argv)!=1:return 2
    print(json.dumps(snapshot(),allow_nan=False,sort_keys=True))
    return 0


if __name__=='__main__':raise SystemExit(main())
