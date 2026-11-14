"""Plain human usage messages. Thread/log text stays one line."""
from datetime import datetime
import re
from zoneinfo import ZoneInfo

ET = ZoneInfo('America/New_York')
LABELS = ('Claude non-Fable', 'Fable', 'Codex')
PRIORITY = {'STOPPED': 0, 'RELEASED': 0, 'OUT': 1, 'WARNING': 2, 'NOTE': 3}


def end_text(end, now):
    dt = datetime.fromtimestamp(end, ET)
    whole = max(0, int(round((end - now) / 60)))
    return f'{dt:%a %H:%M ET} (in {whole // 60}h {whole % 60}m)'


def pack(level, label, headline, used, ends, action):
    return f'{level}  {label}: {headline} | Used: {used} | Ends: {ends} | Do: {action}'


def release_action(minutes=None):
    if minutes is not None:
        return f'at a terminal run  uncap {minutes:g}  (at least {minutes:g} more minutes of credit for every category)'
    return 'at a terminal run  uncap  (release everything) or  uncap 30  (30 minutes of credit)'


def rate_detail(used, planned, minutes, credit=0):
    """Compare with the scale-adjusted base target rate, never the stop multiple."""
    multiple = max(0., used - credit) / planned if planned > 0 else 0.
    ratio_text = f'{multiple * 100:.0f} target %'
    credit_text = f', after {credit:.4f} points soft credit' if credit else ''
    def points(value):
        return f'{value:.2f}' if abs(value) >= .01 or value == 0 else f'{value:.5g}'
    return (f'{points(used)} points vs {points(planned)} at target rate (last {minutes:g} min), '
            f'{ratio_text}{credit_text}')


def rate_annotation(name, used, planned):
    return f' Target rate: {planned:.12g} points; Spend: {used:.12g} points; Box: {name}.'


def box_notice(reading, now, blocked=False, pace=None, level='WARNING', forecast=None):
    """Presentation only: callers retain all existing trigger decisions."""
    name = reading['name']
    prep_total = reading.get('prep_total') or name.startswith('Preparation total')
    if prep_total:
        pace = forecast = None  # No wall-clock/active-hours projection on P/H.
    rate_window = re.search(r'(\d+)-minute rate$', name)
    used = f"{reading['used']:.2f} of {reading['allowed']:.2f} points ({name})"
    if prep_total:
        fraction = reading['used'] / reading['allowed'] if reading['allowed'] else 1.
        used = f"total {reading['used']:.2f} of {reading['allowed']:.2f} points, {fraction * 100:.0f}% of the budget"
    if rate_window and reading.get('rate_points', 0) > 0:
        used = rate_detail(reading['used'], reading['rate_points'], int(rate_window.group(1)),
                           reading.get('soft_credit', 0)) + f", stop at {reading['allowed']:.2f} points"
    if pace:
        points, planned, minutes = pace
        used = rate_detail(points, planned, minutes) + f", total {used}"
    elif reading.get('soft_credit', 0) and not rate_window:
        used += f", includes {reading['soft_credit']:.4f} points soft credit"
    if reading.get('estimated'):
        used += ', includes estimated non-Fable split'
    if forecast is not None:
        used += ', projected to exhaust ' + datetime.fromtimestamp(forecast, ET).strftime('%a %H:%M ET') + ' before close'
    headline = name + (': cap reached' if blocked else ': pace warning' if pace else ': usage warning')
    if prep_total and not blocked and fraction >= .5:
        threshold = 90 if fraction >= .9 else 75 if fraction >= .75 else 50
        headline = f'{name}: {threshold}% budget level'
    if rate_window:
        timing = 'Rolling rate window'
    elif prep_total:
        timing = 'Budget period closes: ' + end_text(reading['end'], now)
    elif reading.get('run'):
        timing = 'Run box closes: ' + end_text(reading['end'], now)
    else:
        timing = 'Rolling usage window'
    action = (release_action()
              if blocked else 'nothing needed' if level == 'NOTE' else 'slow down this category')
    return pack('STOPPED' if blocked else level,
                {'fable': 'Fable', 'non-fable': 'Claude non-Fable', 'codex': 'Codex'}[reading['category']],
                headline, used, timing, action)


def timing_line(item):
    """Name the actual boundary; rolling rate windows have no closing instant."""
    text = item['ends']
    if text.startswith(('Budget period closes:', 'Run box closes:', 'Limit resets:', '5-hour limit resets:', 'Released:', 'Recovered:',
                        'Re-checked every ', 'Status:')):
        return text
    if re.search(r'\d+-minute (?:rate|stop)', item['headline']) or text.startswith('Rolling '):
        return ''
    if 'stays blocked' in text:
        return 'Status: ' + text
    evidence = (item['headline'] + ' ' + item['used']).lower()
    if 'meter' in evidence or 'limit used up' in evidence or 'all accounts out' in evidence:
        return 'Limit resets: ' + text
    if 'preparation total' in evidence:
        return 'Budget period closes: ' + text
    return 'Status: ' + text


def category(line):
    if 'non-fable' in line.lower():
        return 'Claude non-Fable'
    if 'fable' in line.lower():
        return 'Fable'
    if 'codex' in line.lower():
        return 'Codex'
    if line.startswith('Claude '):
        # A shared all-model/5h account meter constrains both Claude categories.
        # Production expands it to both; retain a visible category fallback.
        return 'Claude non-Fable'
    return None


def event(line, now, key='', level=2, ended=False):
    packed = re.fullmatch(r'(STOPPED|RELEASED|OUT|WARNING|NOTE)  (Fable|Claude non-Fable|Codex|Usage tool): (.*?) \| Used: (.*?) \| Ends: (.*?) \| Do: (.*)', line)
    if packed:
        status, label, headline, used, ends, action = packed.groups()
        used = used.replace('run rate', 'target rate')
        used = re.sub(r'([0-9]+(?:\.[0-9]+)?)x target rate',
                      lambda m: f'{float(m[1]) * 100:.0f} target %', used)
        if not re.search(r'\d', used) or not ends:
            return None
        if status in ('STOPPED', 'RELEASED') and re.search(r'--(?:soft-)?uncap\b', action):
            shortfall = re.search(r'at least ([0-9.]+) more minutes', action)
            action = release_action(float(shortfall.group(1)) if shortfall else None)
        return dict(level=status, category=label, headline=headline, used=used, ends=ends, action=action, key=key, multiple=0)
    label = category(line)
    if label is None:
        return None
    pool = {'Fable': 'fable', 'Claude non-Fable': 'non-fable', 'Codex': 'codex'}[label]
    upper = line.upper()
    status = ('NOTE' if ended or 'BACK BELOW' in upper or 'RECOVERED' in upper else
              'STOPPED' if 'BLOCKED' in upper or line.startswith('STOP ') else
              'OUT' if 'OUT OF USAGE' in upper else
              'WARNING' if level >= 2 or 'WOULD STOP' in upper else 'NOTE')
    used = allowed = None
    match = re.search(r'([0-9.]+) of ([0-9.]+) (?:meter )?points', line)
    if match:
        used, allowed = match.groups()
    else:
        match = re.search(r'([0-9.]+) points / ([0-9.]+) (?:planned|limit)', line)
        if match:
            used, allowed = match.groups()
    window = 'category box'
    headline = 'Usage changed'
    minutes = re.search(r'(\d+)(?:m\b|[- ]minute)', line) if ('rate' in line.lower() or 'crossed' in line.lower() or line.startswith(('STOP ', 'WOULD STOP '))) else None
    rate_used_line = None
    if minutes:
        count = int(minutes.group(1))
        window = f'last {count} min'
        multiple = re.search(r'(?:≥ |rate over )([0-9.]+)[×x]', line)
        crossed = re.search(r'\d+m ([0-9.]+)× crossed', line)
        if crossed:
            multiple_text = crossed.group(1)
        elif multiple:
            multiple_text = multiple.group(1)
        else:
            multiple_text = ''
        headline = f'{count}-minute rate' + (f' over {multiple_text}x' if multiple_text else ' changed')
        if 'WOULD STOP' in upper:
            headline = f'{count}-minute rate would stop'
        if ended or 'BACK BELOW' in upper:
            headline = f'{count}-minute rate back below warning'
        basis = re.search(r'(?:Target|Run) rate: ([0-9.eE+-]+) points; (?:Spend: ([0-9.eE+-]+) points; )?Box: (.*?)\.(?: Soft credit:|$)', line)
        if basis:
            headline = basis.group(3) + ': ' + headline
        if used is not None:
            credit = re.search(r'Soft credit: ([0-9.]+) points', line)
            credit_points = float(credit.group(1)) if credit else 0.
            if basis:
                planned = float(basis.group(1))
            elif 'planned' in line:
                planned = float(allowed)
            else:
                threshold = re.search(r'≥ ([0-9.]+)[×x]', line)
                planned = ((float(allowed) - credit_points) / float(threshold.group(1))
                           if threshold and float(threshold.group(1)) else 0.)
            if planned > 0:
                amount = float(basis.group(2)) if basis and basis.group(2) else float(used)
                rate_used_line = rate_detail(amount, planned, count, credit_points)
                if ' limit)' in line:
                    rate_used_line += f', stop at {float(allowed):.2f} points'
    else:
        box = re.search(r'^(?:Fable|Claude non-Fable|Codex), (.*?):', line)
        if box:
            window = box.group(1)
        if status == 'STOPPED':
            headline = window + ': cap reached'
        elif status == 'OUT':
            headline = 'All accounts out of usage' if 'every account' in line else 'Account out of usage'
        elif 'readings incomplete' in line.lower():
            headline = 'Usage readings need checking'
        elif 'meter points' in line:
            limit = re.search(r'\b(5-hour|weekly|Fable)\s*:', line)
            window = (limit.group(1) + ' ' if limit else '') + 'account meter'
            headline = window + ' near cap'
            account = re.search(r'\b[^\s,:]+@[^\s,:]+', line)
            if account:
                window = account.group(0) + ' ' + window
        elif match:
            headline = window + ': usage warning'
        elif ended or 'RECOVERED' in upper:
            headline = 'Previous warning ended'
    if status == 'STOPPED':
        ends = 'stays blocked until released'
        action = release_action()
    else:
        hours = re.search(r'([0-9.]+)h left', line)
        if hours:
            seconds = float(hours.group(1)) * 3600
        elif minutes:
            seconds = int(minutes.group(1)) * 60
        else:
            # A weekly reset line includes a concrete reset when one is known.
            seconds = None
            duration = re.search(r'(?:(\d+)d\s*)?(?:(\d+)h\s*)?(?:(\d+)m\s*)?until reset', line)
            if duration and any(duration.groups()):
                days, hour, minute = (int(value or 0) for value in duration.groups())
                seconds = days * 86400 + hour * 3600 + minute * 60
        if seconds is None:
            ends = 'see the current box in claude-usage --boxes'
        else:
            end = datetime.fromtimestamp(now + seconds, ET)
            whole = int(round(seconds / 60))
            ends = f'{end:%a %H:%M ET} (in {whole // 60}h {whole % 60}m)'
        action = ('nothing needed' if status == 'NOTE' or 'WOULD STOP' in upper else
                  'redeem a reset for this category' if status == 'OUT' and 'every account' in line else
                  'use another account in this category' if status == 'OUT' else 'slow down this category')
    if used is None or ('see the current box' in ends and not minutes):
        return None
    credit = re.search(r'Soft credit: ([0-9.]+) points', line)
    if credit:
        window += f', {credit.group(1)} soft credit'
    used_line = f'{used} of {allowed} points ({window})' if used is not None else f'not available ({window})'
    return dict(level=status, category=label, headline=headline,
                used=rate_used_line or used_line, ends=ends, action=action, key=key,
                multiple=float(crossed.group(1)) if minutes and crossed else 0)


def render(lines, now):
    """Inputs are (key, severity, raw line); no markdown or log/thread mutation."""
    events = []
    for key, severity, line in lines:
        item = event(line, now, key, severity)
        if item:
            events.append(item)
    events.sort(key=lambda e: (PRIORITY[e['level']], -e['multiple'], e['category']))
    if not events:
        # Non-pool operational/billing errors still need a readable notification.
        body = '\n\n'.join(' '.join(text.splitlines()) for _, _, text in lines if category(text) is None)
        if not body:
            return ''
        return f'Usage, {datetime.fromtimestamp(now, ET):%H:%M ET}\n\n' + body
    blocks = ['\n'.join(filter(None, [f"{e['level']}  {e['category']}: {e['headline']}",
                                    f"Used: {e['used']}", timing_line(e), f"Do: {e['action']}"]))
              for e in events]
    prefix = f'Usage, {datetime.fromtimestamp(now, ET):%H:%M ET}\n\n' if len(events) > 1 else ''
    return prefix + '\n\n'.join(blocks)


def matrix_batches(text):
    """Urgent transitions each get their own preview, ahead of other events."""
    blocks = text.split('\n\n')
    headers, urgent, regular = [], [], []
    for block in blocks:
        if block.startswith('Usage, '):
            headers.append(block)
        elif block.startswith(('STOPPED ', 'RELEASED ')):
            urgent.append(block)
        elif block:
            regular.append(block)
    urgent.sort(key=lambda block: 0 if block.startswith('STOPPED ') else 1)
    return urgent + (['\n\n'.join(headers + regular)] if regular else [])
