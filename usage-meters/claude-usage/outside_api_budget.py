#!/usr/bin/python3
"""Local atomic outside-API spend guard. Never contacts a provider or reads a key."""
import argparse
from contextlib import closing
from decimal import Decimal, InvalidOperation
import json
import os
from pathlib import Path
import re
import sqlite3
import stat
import subprocess
import sys
import time
import tomllib

STATE = Path.home() / '.local/state/outside-api-budget'
POLICY = Path.home() / '.config/claude-usage/boxes.toml'
CALL_ID = re.compile(r'[A-Za-z0-9][A-Za-z0-9_.:%-]{0,199}')
D = Decimal


def money(value, positive=False):
    if isinstance(value, bool) or not isinstance(value, (str, int, float, Decimal)):
        raise ValueError('invalid money')
    number = D(str(value))
    if (not number.is_finite() or number < 0 or number > D('1000000')
            or number.as_tuple().exponent < -36 or (positive and number == 0)):
        raise ValueError('invalid money')
    return number


def db_open(directory=None):
    directory = STATE if directory is None else directory
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    meta = directory.lstat()
    if not stat.S_ISDIR(meta.st_mode) or meta.st_uid != os.getuid() or meta.st_mode & 0o077:
        raise ValueError('private guard directory required')
    fd = os.open(directory / 'budget.sqlite', os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    meta = os.fstat(fd); os.close(fd)
    if not stat.S_ISREG(meta.st_mode) or meta.st_uid != os.getuid() or meta.st_mode & 0o077:
        raise ValueError('private guard database required')
    db = sqlite3.connect(directory / 'budget.sqlite', timeout=2)
    db.executescript('''PRAGMA synchronous=FULL;
        CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY,value TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS calls(id TEXT PRIMARY KEY,provider TEXT,upper_usd TEXT,actual_usd TEXT,at REAL,settled_at REAL);
        CREATE TABLE IF NOT EXISTS latches(rule TEXT PRIMARY KEY,used TEXT,allowed TEXT);
        CREATE TABLE IF NOT EXISTS warnings(rule TEXT PRIMARY KEY,active INTEGER);
        CREATE TABLE IF NOT EXISTS notices(id INTEGER PRIMARY KEY AUTOINCREMENT,body TEXT,sent INTEGER DEFAULT 0);
    ''')
    return db


def settings(db):
    return dict(db.execute('SELECT key,value FROM settings').fetchall())


def put(db, key, value):
    db.execute('INSERT OR REPLACE INTO settings VALUES (?,?)', (key, str(value)))


def initialize(db, cap='40'):
    """Installation only; never modifies existing cap/spend/latches."""
    with db:
        db.execute('INSERT OR IGNORE INTO settings VALUES (?,?)', ('cap', str(money(cap, True))))
        for key, value in [('credit', '0'), ('hard_extra', '0'), ('rate_epoch', '0'), ('providers', 'cerebras')]:
            db.execute('INSERT OR IGNORE INTO settings VALUES (?,?)', (key, value))


def rate_policy(now=None, policy=POLICY):
    from claude_usage_boxes import RATE_LADDER, notify_levels
    data = tomllib.loads(Path(policy).read_text())
    if data.get('approved') is not True:
        raise ValueError('usage policy not approved')
    hours = money(data['preparation']['active_hours'], True)
    scale = D(1)
    now = time.time() if now is None else now
    from datetime import datetime
    for run in data.get('runs', []):
        if datetime.fromisoformat(run['start']).timestamp() <= now < datetime.fromisoformat(run['end']).timestamp():
            if run.get('approved') is False:
                raise ValueError('run policy not approved')
            scale = money(run.get('scale', 1), True)
            break
    stops = data['preparation'].get('stops', {})
    ladder = [(minutes, tuple(D(str(x)) for x in notify_levels(minutes, stops.get(str(minutes), stop))),
               D(str(stops.get(str(minutes), stop)))) for minutes, _, stop in RATE_LADDER]
    return hours, scale, ladder


def readings(db, now, policy):
    cfg = settings(db)
    if 'cap' not in cfg:
        raise ValueError('cap unset')
    cap, credit, extra = money(cfg['cap'], True), money(cfg['credit']), money(cfg['hard_extra'])
    hours, scale, ladder = policy
    rate = cap / hours * scale
    rows = db.execute('SELECT provider,upper_usd,actual_usd,at,settled_at FROM calls').fetchall()
    totals = {'cerebras': D(0), 'spare': D(0)}
    pending = D(0)
    for provider, upper, actual, at, settled in rows:
        amount = money(upper if actual is None else actual)
        totals[provider] += amount
        if actual is None:
            pending += amount
    used = sum(totals.values(), D(0))
    rules = [('Total cap', used, cap + extra + credit,
              tuple((D(x), level) for x, level in [('.5', 'NOTE'), ('.75', 'WARNING'), ('.9', 'WARNING')]), None)]
    epoch = float(cfg['rate_epoch'])
    for minutes, levels, stop in ladder:
        amount = sum((money(upper if actual is None else actual)
            for provider, upper, actual, at, settled in rows
            if at >= epoch and (actual is None or (settled or at) >= now - minutes * 60)), D(0))
        denominator = rate * D(minutes) / 60
        rules.append((f'{minutes}-minute rate', amount, denominator * stop + credit,
                      tuple((x, 'WARNING') for x in levels), denominator))
    return cfg, rate, rules, totals, pending


def notice(db, body):
    db.execute('INSERT INTO notices(body) VALUES (?)', ('For you: ' + body,))


def confirmed_spend(db, rule, now, epoch):
    if rule == 'Total cap':
        rows = db.execute('SELECT actual_usd FROM calls WHERE actual_usd IS NOT NULL')
    else:
        minutes = int(rule.split('-')[0])
        rows = db.execute('SELECT actual_usd FROM calls WHERE actual_usd IS NOT NULL AND at>=? AND settled_at>=?',
                          (epoch, now - minutes * 60))
    return sum((money(row[0]) for row in rows), D(0))


def evaluate(db, now, policy):
    cfg, rate, rules, totals, pending = readings(db, now, policy)
    for rule, used, allowed, levels, denominator in rules:
        lowest_warning = min((multiple for multiple, level in levels if level == 'WARNING'), default=None)
        confirmed = confirmed_spend(db, rule, now, float(cfg['rate_epoch']))
        # Upper bounds protect admission, but unused reservations are not actual
        # spending. Only measured settled charges may create a permanent latch.
        if confirmed >= allowed:
            old = db.execute('SELECT used,allowed FROM latches WHERE rule=?', (rule,)).fetchone()
            db.execute('INSERT OR REPLACE INTO latches VALUES (?,?,?)',
                       (rule, str(max(confirmed, money(old[0])) if old else confirmed), str(allowed)))
            if not old:
                detail = (f'${confirmed:.4f} of ${allowed:.4f}' if denominator is None else
                          f'${confirmed:.4f} vs ${denominator:.4f} at target rate, {confirmed / denominator * 100:.0f} target %')
                notice(db, f'STOPPED Outside API: {rule}. {detail}. '
                           'Do: at a terminal run uncap (release everything) or uncap 30 (30 minutes of credit).')
        for multiple, level in levels:
            threshold = allowed * multiple if denominator is None else denominator * multiple + money(cfg['credit'])
            key = rule + ':' + str(multiple)
            old = db.execute('SELECT active FROM warnings WHERE rule=?', (key,)).fetchone()
            active = int(used >= threshold)
            if active and not (old and old[0]) and used < allowed:
                detail = (f'${used:.4f} of ${allowed:.4f}, {used / allowed:.0%} of budget' if denominator is None else
                          f'${used:.4f} vs ${denominator:.4f} at target rate (last {rule.split("-")[0]} min), '
                          f'{used / denominator * 100:.0f} target %')
                notice(db, f'{level} Outside API: {rule}. {detail}. '
                           f'Cerebras ${totals["cerebras"]:.4f}; Spare ${totals["spare"]:.4f}; '
                           f'${pending:.4f} pending reservations included.')
            if multiple == lowest_warning and not active and old and old[0]:
                notice(db, f'NOTE Outside API: {rule} back below warning. Recovered; no action needed.')
            db.execute('INSERT OR REPLACE INTO warnings VALUES (?,?)', (key, active))
    return readings(db, now, policy)


def snapshot(db, now, policy):
    cfg, rate, rules, totals, pending = readings(db, now, policy)
    return {'cap_usd': cfg['cap'], 'allowed_total_usd': str(rules[0][2]),
            'committed_usd': str(rules[0][1]), 'pending_usd': str(pending),
            'providers': {k: str(v) for k, v in totals.items()}, 'target_rate_usd_per_hour': str(rate), 'run_rate_usd_per_hour': str(rate),
            'active_hours': str(policy[0]), 'blocked': bool(db.execute('SELECT 1 FROM latches LIMIT 1').fetchone()),
            'enabled_providers': cfg['providers'].split(',')}


def reserve(db, value, now, policy):
    if not isinstance(value, dict) or set(value) != {'id', 'provider', 'upper_usd'}:
        raise ValueError('reserve schema')
    identifier, provider, upper = value['id'], value['provider'], money(value['upper_usd'], True)
    if not isinstance(identifier, str) or not CALL_ID.fullmatch(identifier):
        raise ValueError('invalid call id')
    db.execute('BEGIN IMMEDIATE')
    try:
        cfg = settings(db)
        if 'cap' not in cfg:
            return {'allowed': False, 'reason': 'cap-unset'}
        if provider not in ('cerebras', 'spare') or provider not in cfg['providers'].split(','):
            return {'allowed': False, 'reason': 'provider-not-approved'}
        if db.execute('SELECT 1 FROM calls WHERE id=?', (identifier,)).fetchone():
            return {'allowed': False, 'reason': 'duplicate-id'}
        _, _, rules, _, _ = evaluate(db, now, policy)
        if db.execute('SELECT 1 FROM latches LIMIT 1').fetchone():
            return {'allowed': False, 'reason': 'human-release-required'}
        if any(used + upper > allowed for rule, used, allowed, levels, denominator in rules):
            return {'allowed': False, 'reason': 'insufficient-budget-or-rate-credit'}
        db.execute('INSERT INTO calls VALUES (?,?,?,?,?,NULL)', (identifier, provider, str(upper), None, now))
        evaluate(db, now, policy)
        return {'allowed': True, 'reservation_id': identifier}
    finally:
        db.commit()


def settle(db, value, now, policy):
    if not isinstance(value, dict) or set(value) != {'id', 'actual_usd'}:
        raise ValueError('settle schema')
    actual = money(value['actual_usd'])
    db.execute('BEGIN IMMEDIATE')
    try:
        row = db.execute('SELECT actual_usd FROM calls WHERE id=?', (value['id'],)).fetchone()
        if not row:
            raise ValueError('no reservation')
        if row[0] is not None and money(row[0]) != actual:
            raise ValueError('conflicting settlement')
        db.execute('UPDATE calls SET actual_usd=?,settled_at=COALESCE(settled_at,?) WHERE id=?',
                   (str(actual), now, value['id']))
        evaluate(db, now, policy)
        return {'settled': True, 'reservation_id': value['id'], **snapshot(db, now, policy)}
    except Exception:
        db.rollback()
        raise
    finally:
        if db.in_transaction:
            db.commit()


def human_release(db, minutes, now, policy, only_if_blocked=False):
    """Caller must enforce real-terminal guard. No spending record is removed.

    only_if_blocked: timed credit is added only while a stop is latched.
    """
    db.execute('BEGIN IMMEDIATE')
    try:
        cfg, rate, rules, totals, pending = readings(db, now, policy)
        was_blocked = bool(db.execute('SELECT 1 FROM latches LIMIT 1').fetchone())
        added = D(0)
        if only_if_blocked and minutes is not None and not was_blocked:
            return {**snapshot(db, now, policy), 'credit_added_usd': '0', 'shortfall_usd': '0', 'skipped': True}
        if minutes is None:
            put(db, 'hard_extra', rules[0][1])
            put(db, 'rate_epoch', now)
            db.execute('DELETE FROM latches'); db.execute('DELETE FROM warnings')
        else:
            added = rate * money(minutes, True) / 60
            put(db, 'credit', money(cfg['credit']) + added)
            for rule, used, allowed in db.execute('SELECT * FROM latches').fetchall():
                if money(used) < money(allowed) + added:
                    db.execute('DELETE FROM latches WHERE rule=?', (rule,))
                else:
                    db.execute('UPDATE latches SET allowed=? WHERE rule=?', (str(money(allowed) + added), rule))
            evaluate(db, now, policy)
        result = snapshot(db, now, policy)
        short = max([D(0)] + [money(u) - money(a) for u, a in db.execute('SELECT used,allowed FROM latches')])
        if was_blocked:
            notice(db, ('STOPPED' if result['blocked'] else 'RELEASED') + ' Outside API: '
                   + (f'${short:.4f} credit short; run uncap or uncap <minutes>.' if result['blocked'] else
                      'human release applied; all recorded spending retained.'))
        return {**result, 'credit_added_usd': str(added), 'shortfall_usd': str(short)}
    finally:
        db.commit()


def flush(db, runner=subprocess.run):
    script = Path.home() / '.local/lib/matrix-relay/notify_mirror.py'
    for identifier, body in db.execute('SELECT id,body FROM notices WHERE sent=0 ORDER BY id LIMIT 16').fetchall():
        try:
            result = runner(['/usr/bin/python3', str(script), '--producer', 'usage', '--event-key', f'outside-api:{identifier}'],
                            input=body, text=True, capture_output=True, timeout=2, check=False)
            if result.returncode == 0 and result.stdout.strip() == 'queued':
                with db:
                    db.execute('UPDATE notices SET sent=1 WHERE id=?', (identifier,))
            else:
                break
        except (OSError, subprocess.SubprocessError):
            break


def cached_line():
    if not (STATE / 'budget.sqlite').exists():
        return 'Outside API: cap unset; live calls refused.'
    try:
        with closing(db_open()) as db:
            row = snapshot(db, time.time(), rate_policy())
        return (f'Outside API: ${money(row["committed_usd"]):.4f} of ${money(row["allowed_total_usd"]):.2f} total '
                f'(Cerebras ${money(row["providers"]["cerebras"]):.4f}; Spare ${money(row["providers"]["spare"]):.4f}); '
                f'${money(row["target_rate_usd_per_hour"]):.4f}/h; ' + ('STOPPED' if row['blocked'] else 'open'))
    except Exception:
        return 'Outside API: guard unreadable; live calls refused.'


def check_and_notify():
    if not (STATE / 'budget.sqlite').exists():
        return
    with closing(db_open()) as db:
        with db:
            evaluate(db, time.time(), rate_policy())
        flush(db)


def release_all(minutes=None, stdin=None, stdout=None, only_if_blocked=False):
    stdin, stdout = stdin or sys.stdin, stdout or sys.stdout
    if not stdin.isatty() or not stdout.isatty():
        print('Outside API: refused; use an interactive terminal.', file=stdout)
        return 2
    if not (STATE / 'budget.sqlite').exists():
        return 0  # Not installed/enabled; never silently creates a cap.
    with closing(db_open()) as db:
        value = human_release(db, minutes, time.time(), rate_policy(), only_if_blocked)
        flush(db)
    if value.get('skipped'):
        print('Outside API: not capped; skipped, no credit added.', file=stdout)
        return 0
    print('Outside API: ' + ('still blocked' if value['blocked'] else 'released')
          + f'; credit added ${money(value["credit_added_usd"]):.4f}'
          + (f'; ${money(value["shortfall_usd"]):.4f} short' if value['blocked'] else '')
          + '; recorded spend retained.', file=stdout)
    return int(value['blocked'])


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('action', choices=('reserve', 'settle', 'status', 'check', 'set-cap', 'uncap'))
    parser.add_argument('value', nargs='?')
    args = parser.parse_args()
    if args.action in ('uncap', 'set-cap') and not (sys.stdin.isatty() and sys.stdout.isatty()):
        print('{"error":"human interactive terminal required"}')
        return 2
    if args.action == 'uncap':
        return release_all(args.value)
    with closing(db_open()) as db:
        now, policy = time.time(), rate_policy()
        if args.action == 'reserve':
            value = reserve(db, json.loads(sys.stdin.read(8193)), now, policy)
        elif args.action == 'settle':
            value = settle(db, json.loads(sys.stdin.read(8193)), now, policy)
        elif args.action == 'set-cap':
            cap = money(args.value, True)
            initialize(db, cap)
            with db:
                put(db, 'cap', cap); evaluate(db, now, policy)
            value = snapshot(db, now, policy)
        else:
            with db:
                evaluate(db, now, policy)
            value = snapshot(db, now, policy)
        print(json.dumps(value, sort_keys=True))
        # Reserve must stay within caller's5s deadline; the usage cadence retries
        # durable notices. No subprocess/notification work on the admission path.
        if args.action in ('check', 'set-cap'):
            flush(db)
    return 0


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except (OSError, ValueError, KeyError, TypeError, InvalidOperation, sqlite3.Error):
        print('{"allowed":false,"error":"outside-API guard unavailable; no live call allowed"}')
        raise SystemExit(1)
