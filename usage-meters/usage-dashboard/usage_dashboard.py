#!/usr/bin/python3
"""Tiny local page: % of target rate per category x window, % of total capacity (incl. banked
resets) used per category, % used per CLIProxyAPI account,
and buttons that run `uncap 1|5|15`.

Target rates come from the installed read-only producer usage_target_rates.snapshot() (the same
numbers claude-usage's own reports use); accounts come from the claude-usage reader's management
auth-files read of each proxy (Claude :8318, Codex :8317). Loading the page writes nothing and
sends nothing to Anthropic or OpenAI; management keys stay server-side. Only an uncap button
changes state, through the installed `uncap` command's own code. Serves http://127.0.0.1:8770
by default; the page reloads every 60 s.

It loads claude-usage, claude_usage_boxes and uncap from $CLAUDE_USAGE_BIN (default ~/.local/bin).
"""
import argparse
import copy
from datetime import datetime
import html
import importlib.machinery
import importlib.util
import io
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import math
import os
from pathlib import Path
import secrets
import sys
import threading
from urllib.parse import parse_qs

BIN = Path(os.environ.get('CLAUDE_USAGE_BIN') or Path.home() / '.local/bin')
BASELINE = Path.home() / '.local/state/usage-dashboard/baseline.json'
UNCAP_MINUTES = ('1', '5', '15')
CATEGORY_SHORT = {'fable': 'Fable', 'non-fable': 'Claude', 'codex': 'Codex', 'outside-api': 'APIs'}
STATUS_SHORT = {'stale': 'stale', 'incomplete': 'inc', 'unavailable': 'n/a'}


def load(name, path):
    loader = importlib.machinery.SourceFileLoader(name, str(path))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


def num(value):
    """Finite float or None; proxy signals can be strings or empty."""
    try:
        value = float(value)
    except (TypeError, ValueError):
        return None
    return value if math.isfinite(value) else None


def collect(cu):
    """-> (rows, errors). One proxy failing still shows the other."""
    rows, errors = [], []
    for name, read in (('Claude proxy', lambda: cu.parse(cu.fetch(), excluded_accounts=set())),
                       ('Codex proxy', lambda: cu.parse_codex(cu.fetch(cu.CODEX_URL, cu.CODEX_KEY)))):
        try:
            rows += read()
        except Exception as exc:  # show the failure on the page instead of a 500
            errors.append(f'{name}: unreadable ({type(exc).__name__})')
    return rows, errors


def collect_rates(rates):
    """-> (snapshot or None, errors)."""
    try:
        return rates.snapshot(), []
    except Exception as exc:
        return None, [f'Target rates: unreadable ({type(exc).__name__})']


def totals(policy, rows, boxes, now):
    """Per category: current weekly meter points vs full capacity incl. banked resets.

    Uses the box policy's own units (capacities(), gauges(), account weights). An account
    with no current figure (missing row or gauge, or weekly window already reset) is listed
    in 'missing' and adds nothing, so the % is then a lower bound.
    """
    capacity = boxes.capacities(policy)
    by_key = {(r.get('provider'), r.get('email')): r for r in rows}
    out = {c: dict(category=c, used=0., total=capacity[c], missing=[], parts=[]) for c in boxes.CATEGORIES}
    for source in policy['sources']:
        row = by_key.get((source['provider'], source['email']))
        reset = num(row.get('r7')) if row else None
        gauge = boxes.gauges(row) if row and not (reset and reset <= now) else {}
        name = source['email'].split('@')[0]
        for c in (('codex',) if source['provider'] == 'codex' else ('fable', 'non-fable')):
            points = num(gauge.get(c))
            if points is None:
                out[c]['missing'].append(name)
            else:
                out[c]['used'] += points * source['weight']
                out[c]['parts'].append(f'{name} {points * source["weight"]:.1f}')
    return [out[c] for c in boxes.CATEGORIES]


def spent_since(ledger, category, marker):
    """Physical usage the ledger appended after `marker` (the last event at the rebase): raw
    events, no guard exemptions, estimates or credits. The meter check only appends events and
    prunes them by age, so position, not timestamp, separates before from after: a check that
    commits late can stamp its events earlier than the rebase."""
    events = ledger['events']
    start = 0
    if marker is not None:
        found = [i for i, e in enumerate(events) if e == marker]
        if not found:
            raise ValueError('the ledger no longer holds the rebase point; run --rebase again')
        start = found[-1] + 1
    return sum(num(e[2]) or 0. for e in events[start:] if e[1] == category)


def make_baseline(policy, ledger, boxes, now):
    """What is left per category, from the ledger alone so the boundary matches its events:
    each account's capacity incl. banked resets minus its last ledger meter reading (an
    account with no current figure counts as nothing left). Usage from then on is every event
    appended after the ledger's last event at this moment (marker)."""
    remaining = dict.fromkeys(boxes.CATEGORIES, 0.)
    missing = {c: [] for c in boxes.CATEGORIES}
    for source in policy['sources']:
        capacity = boxes.capacities(dict(sources=[source]))
        current = ledger['sources'].get(source['provider'] + ':' + source['email'])
        row = (current or {}).get('row')
        reset = num(row.get('r7')) if row else None
        try:
            gauge = boxes.gauges(row) if row and not (reset and reset <= now) else {}
        except (TypeError, ValueError):
            gauge = {}
        for c in (('codex',) if source['provider'] == 'codex' else ('fable', 'non-fable')):
            # The ledger marks a category 'carried' when its reading is from before a reset.
            points = None if c in ((current or {}).get('carried') or {}) else num(gauge.get(c))
            if points is None:
                missing[c].append(source['email'].split('@')[0])
            else:
                remaining[c] += max(0., capacity[c] - points * source['weight'])
    return dict(at=now, remaining=remaining, missing=missing,
                marker=ledger['events'][-1] if ledger['events'] else None)


def since_baseline(baseline, ledger, boxes, now):
    """Usage the ledger recorded since the rebase against what was left then."""
    at, marker = num(baseline.get('at')), baseline.get('marker', 'absent')
    remaining, missing = baseline.get('remaining'), baseline.get('missing') or {}
    if (at is None or not isinstance(remaining, dict) or not isinstance(missing, dict)
            or not (marker is None or isinstance(marker, list) and len(marker) == 3)):
        raise ValueError('baseline')
    items = []
    for c in boxes.CATEGORIES:
        total = num(remaining.get(c))
        if total is None or total < 0 or not isinstance(missing.get(c, []), list):
            raise ValueError('baseline')
        items.append(dict(category=c, used=spent_since(ledger, c, marker), total=total,
                          missing=[str(m) for m in missing.get(c, [])], parts=[]))
    return items


def totals_table(items, since=None):
    body = ''
    for t in items:
        pct = t['used'] / t['total'] * 100 if t['total'] else None
        tip = '; '.join(t['parts'] + [f'no figure{" at rebase" if since else ""}: {", ".join(t["missing"])}'] * bool(t['missing']))
        flag = ' <span class=na>*</span>' if t['missing'] else ''
        cell = ('<td class=na>-</td>' if pct is None else
                f'<td>{bar(pct, "hi" if pct >= 90 else "mid" if pct >= 70 else "lo")}<b>{pct:.0f}%</b>'
                f'<small>{t["used"]:.0f}/{t["total"]:.0f}</small></td>')
        body += f'<tr title="{html.escape(tip)}"><td>{CATEGORY_SHORT[t["category"]]}{flag}</td>{cell}</tr>'
    head = (f'<th title="ledger usage since the rebase vs what was left then, incl. banked resets">Since {since}</th>'
            if since else '<th title="current weekly meters vs total capacity incl. banked resets">Of total</th>')
    return f'<table><tr>{head}<th></th></tr>{body or "<tr><td colspan=2 class=na>none</td></tr>"}</table>'


def short_time(epoch, cu):
    return datetime.fromtimestamp(epoch, cu.ET).strftime('%a %H:%M')


def bar(fill, tone):
    return f'<div class=bar><i class={tone} style="width:{max(0., min(fill, 100.)):.0f}%"></i></div>'


def meter(value, reset, cu, now):
    value, reset = num(value), num(reset)
    if value is None:
        return '<td class=na>-</td>'
    if reset and reset <= now.timestamp():
        return f'<td class=na title="window reset {html.escape(cu.when(reset))}; no newer figure">reset</td>'
    used = value * 100
    tone = 'hi' if used >= 90 else 'mid' if used >= 70 else 'lo'
    tip = f' title="resets {html.escape(cu.when(reset))}"' if reset else ''
    when = f'<small>{short_time(reset, cu)}</small>' if reset else ''
    return f'<td{tip}>{bar(used, tone)}<b>{used:.0f}%</b>{when}</td>'


def estimate_rates(boxes, policy, ledger, samples, now):
    """{category: {minutes: (estimated % of target, soft credit in the window)}} from the same
    window math the producer uses, but without its coverage checks. Works on a ledger copy."""
    context = boxes.rate_context(policy, copy.deepcopy(ledger), now)
    out = {}
    for c in boxes.CATEGORIES:
        for minutes, fixed, _ in boxes.RATE_LADDER:
            w = boxes.target_rate_window(policy, ledger, samples, context, c, minutes, fixed, now)
            multiple = num(w['multiple'])
            out.setdefault(c, {})[minutes] = (None if multiple is None else multiple * 100, num(w['credit']) or 0.)
    return out


def rate_cell(cell, warn, stop, estimate=None):
    """warn/stop are the ladder's first notification and default stop, as % of target.
    estimate: (pct, credit) for this cell, shown dimmed as ~pct when the producer has no figure."""
    pct = num((cell or {}).get('target_percent'))
    status = (cell or {}).get('status')
    credit = f'; soft credit {estimate[1]:.2f} pts in window' if estimate and estimate[1] else ''
    if status != 'ok' or pct is None:
        reason = html.escape(str((cell or {}).get('reason') or status))
        if estimate and estimate[0] is not None:
            return (f'<td class=na title="estimate; producer: {reason}{credit}">'
                    f'{bar(estimate[0] / stop * 100, "est")}<b>~{estimate[0]:.0f}%</b></td>')
        return f'<td class=na title="{reason}">{STATUS_SHORT.get(status, "-")}</td>'
    tone = 'hi' if pct >= stop else 'mid' if pct >= warn else 'lo'
    return (f'<td title="warn {warn:g}%, default stop {stop:g}%{credit}">'
            f'{bar(pct / stop * 100, tone)}<b>{pct:.0f}%</b></td>')


def rates_table(snap, ladder, estimates=None):
    """ladder: {minutes: (first warning multiple, default stop multiple)}."""
    windows = [str(m) for m in (snap or {}).get('windows_minutes') or sorted(ladder)]
    head = ''.join(f'<th>{w}m</th>' for w in windows)
    body = ''
    for row in (snap or {}).get('categories') or []:
        cells = row.get('windows') or {}
        tph = num(row.get('target_per_hour'))
        tip = f' title="{html.escape(str(row.get("label")))}; target {tph:.2f}/h"' if tph else ''
        body += (f'<tr><td{tip}>{html.escape(CATEGORY_SHORT.get(row.get("category"), str(row.get("category"))))}</td>'
                 + ''.join(rate_cell(cells.get(w), *[x * 100 for x in ladder.get(int(w), (1, 1))],
                                   (estimates or {}).get(row.get('category'), {}).get(int(w))) for w in windows)
                 + '</tr>')
    body = body or f'<tr><td colspan={len(windows) + 1} class=na>none</td></tr>'
    context = html.escape(str((snap or {}).get('context') or ''))
    return f'<table><tr><th title="{context}">% target</th>{head}</tr>{body}</table>'


def account_row(r, cu, now):
    email = str(r.get('email'))
    status = '' if r.get('status') == 'active' else f' <span class=err>{html.escape(str(r.get("status")))}</span>'
    fable = meter(r.get('uf'), r.get('rf'), cu, now) if r.get('provider') == 'claude' else '<td class=na></td>'
    return (f'<tr><td title="{html.escape(email)}">{html.escape(str(r.get("provider"))[:2])} '
            f'{html.escape(email.split("@")[0])}{status}</td>'
            f'{meter(r.get("u5"), r.get("r5"), cu, now)}{meter(r.get("u7"), r.get("r7"), cu, now)}{fable}</tr>')


def page(rows, errors, cu, now=None, snap=None, ladder=None, token='', last=None, total_items=(), since=None, estimates=None):
    now = now or datetime.now(cu.ET)
    notes = ''.join(f'<p class=err>{html.escape(e)}</p>' for e in errors)
    acct_body = ''.join(account_row(r, cu, now) for r in rows) or '<tr><td colspan=4 class=na>none</td></tr>'
    buttons = ''.join(
        f'<button name=minutes value={m} onclick="return confirm(\'Run uncap {m}: add {m} min of credit to capped categories?\')">+{m}m</button>'
        for m in UNCAP_MINUTES)
    result = (f'<pre title="{html.escape(last[0])}">{html.escape(last[1])}</pre>' if last else '')
    return f'''<!doctype html><html><head><meta charset=utf-8><meta http-equiv=refresh content=60>
<meta name=viewport content="width=device-width,initial-scale=1"><title>Usage</title><style>
:root{{color-scheme:light dark;--bg:#fff;--fg:#111;--mute:#777;--line:#ddd;--track:#e6e6e6}}
@media (prefers-color-scheme:dark){{:root{{--bg:#151515;--fg:#eee;--mute:#999;--line:#2c2c2c;--track:#2a2a2a}}}}
body{{background:var(--bg);color:var(--fg);font:13px/1.25 system-ui,sans-serif;margin:8px}}
.wrap{{display:flex;flex-wrap:wrap;gap:4px 16px;align-items:flex-start}}table{{border-collapse:collapse}}
th{{font-weight:600;color:var(--mute);text-align:left;padding:1px 6px}}td{{padding:2px 6px;border-top:1px solid var(--line);white-space:nowrap}}
.bar{{display:inline-block;width:32px;background:var(--track);height:6px;border-radius:3px;overflow:hidden;margin-right:4px}}
.bar i{{display:block;height:100%}}.lo{{background:#2e9d5b}}.est{{background:var(--mute)}}.mid{{background:#d29a1e}}.hi{{background:#d0453a}}
b{{display:inline-block;min-width:30px}}small{{color:var(--mute);margin-left:4px}}.na{{color:var(--mute)}}.err{{color:#d0453a}}
p{{margin:2px 0}}p.meta{{color:var(--mute);font-size:11px}}form{{display:flex;gap:4px;align-items:center}}
button{{font:inherit;padding:1px 8px}}pre{{margin:2px 0;font-size:11px;white-space:pre-wrap;color:var(--mute)}}
</style></head><body>{notes}<div class=wrap>{rates_table(snap, ladder or {}, estimates)}{totals_table(total_items, since)}
<table><tr><th>Account</th><th>5h</th><th>Week</th><th>Fable</th></tr>{acct_body}</table>
<form method=post action=/uncap><span class=na>uncap</span><input type=hidden name=token value="{html.escape(token)}">{buttons}</form></div>
{result}<p class=meta>{now.strftime('%H:%M:%S')} · % target = use in the window vs the planned rate (100% = on pace; bar full = default stop) · ~ = estimate where the producer has no trustworthy figure (inc/stale) · Since = usage since the rebase / what was left then incl. banked resets, * = an account had no figure · hover for details</p>
</body></html>'''


class TtyBuffer(io.StringIO):
    """uncap refuses non-terminals; a click on this page is the human's confirmation."""
    def isatty(self):
        return True


def run_uncap(form, origin, expected_origin, token, uncap_main, lock):
    """-> (http status, message). Only a same-page POST with this server's token runs uncap."""
    minutes = (form.get('minutes') or [''])[0]
    if origin != expected_origin or not secrets.compare_digest((form.get('token') or [''])[0], token):
        return 403, 'refused: not from this page'
    if minutes not in UNCAP_MINUTES:
        return 400, 'refused: minutes must be 1, 5 or 15'
    if not lock.acquire(blocking=False):
        return 409, 'refused: another uncap is running'
    try:
        out = TtyBuffer()
        try:
            code = uncap_main([minutes], stdin=TtyBuffer(), stdout=out)
        except Exception as exc:
            return 500, f'uncap {minutes} failed ({type(exc).__name__}); check the local usage guard.'
        return 200, f'uncap {minutes} (exit {code}):\n' + out.getvalue().strip()
    finally:
        lock.release()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--port', type=int, default=8770)
    parser.add_argument('--rebase', action='store_true', help='set the totals view to 0 as of now, then exit')
    args = parser.parse_args(argv)
    sys.path.insert(0, str(BIN))  # the installed modules import each other by name
    cu = load('claude_usage_reader', BIN / 'claude-usage')
    import claude_usage_boxes as boxes
    import usage_target_rates as rates
    uncap = load('uncap_command', BIN / 'uncap')
    if args.rebase:
        ledger_path = cu.LOG.parent / 'category-ledger.json'
        with cu.guard_lock(ledger_path):  # the meter check writes the ledger under this lock
            ledger = json.loads(ledger_path.read_text())
            now = datetime.now().timestamp()
        baseline = make_baseline(boxes.load(cu.BOXES), ledger, boxes, now)
        cu.atomic_write(BASELINE, json.dumps(baseline, sort_keys=True) + '\n')
        print(json.dumps(baseline, sort_keys=True))
        return
    ladder = {m: (fixed[0], stop) for m, fixed, stop in boxes.RATE_LADDER}
    token, lock, state = secrets.token_urlsafe(24), threading.Lock(), {}
    origin = f'http://127.0.0.1:{args.port}'

    class Handler(BaseHTTPRequestHandler):
        def send(self, status, data, location=None):
            self.send_response(status)
            if location:
                self.send_header('Location', location)
            self.send_header('Content-Type', 'text/html; charset=utf-8')
            self.send_header('Cache-Control', 'no-store')
            self.send_header('Content-Length', str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self):
            if self.path not in ('/', '/index.html'):
                self.send_error(404)
                return
            now = datetime.now(cu.ET)
            rows, errors = collect(cu)
            snap, rate_errors = collect_rates(rates)
            estimates = None
            try:
                policy, since = boxes.load(cu.BOXES), None
                ledger = json.loads((cu.LOG.parent / 'category-ledger.json').read_text())
                try:
                    samples = json.loads((cu.LOG.parent / 'passive-rate.json').read_text())
                    estimates = estimate_rates(boxes, policy, ledger, samples, now.timestamp())
                except Exception as exc:
                    rate_errors = rate_errors + [f'Estimates: unreadable ({type(exc).__name__})']
                try:
                    baseline = json.loads(BASELINE.read_text())
                except FileNotFoundError:
                    baseline = None
                if baseline:
                    total_items = since_baseline(baseline, ledger, boxes, now.timestamp())
                    since = short_time(num(baseline['at']), cu)
                else:
                    total_items = totals(policy, rows, boxes, now.timestamp())
            except Exception as exc:
                total_items, since, rate_errors = (), None, rate_errors + [f'Totals: unreadable ({type(exc).__name__})']
            self.send(200, page(rows, rate_errors + errors, cu, now, snap, ladder, token, state.get('last'), total_items, since, estimates).encode())

        def do_POST(self):
            if self.path != '/uncap':
                self.send_error(404)
                return
            length = min(int(self.headers.get('Content-Length') or 0), 4096)
            form = parse_qs(self.rfile.read(length).decode('utf-8', 'replace'))
            status, message = run_uncap(form, self.headers.get('Origin'), origin, token, uncap.main, lock)
            state['last'] = (datetime.now(cu.ET).strftime('%H:%M:%S'), message)
            if status == 403:
                self.send(403, b'refused')
                return
            self.send(303, b'', '/')

        def log_message(self, *args):
            pass

    print(origin, flush=True)
    ThreadingHTTPServer(('127.0.0.1', args.port), Handler).serve_forever()


if __name__ == '__main__':
    main()
