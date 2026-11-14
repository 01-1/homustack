"""Optional CATEGORY publisher. Never sends arbitrary text to cloud agent backends."""
import json
import math
from pathlib import Path
import re
import subprocess

CONFIG=Path('/etc/agent-cloud/category-routes.json')
DISPATCH='/usr/local/lib/agent-cloud/category_dispatch.py'
LABELS={'Fable':'fable','Claude non-Fable':'non-fable','Codex':'codex'}


def enabled():
    return CONFIG.exists()


def publish(value,runner=subprocess.run):
    if not CONFIG.exists():return False
    try:
        reply=runner(['/usr/bin/sudo','-n','/usr/bin/python3','-E','-s',DISPATCH,'enqueue'],
                     input=json.dumps(value),text=True,capture_output=True,timeout=3,check=False)
        return reply.returncode==0 and reply.stdout.strip()=='queued'
    except (OSError,subprocess.SubprocessError):
        return False


def guard(state,send=publish):
    """Only numeric existing latch evidence can request native process stops."""
    for latch in state.get('latches',{}).values():
        category=latch.get('category')
        if category not in LABELS.values():continue
        try:
            used,allowed,at=(float(latch[key]) for key in ('used','allowed','at'))
        except (KeyError,TypeError,ValueError):continue
        if not all(math.isfinite(n) and n>=0 for n in (used,allowed,at)) or used<allowed:continue
        send(dict(category=category,level='STOPPED',used_points=used,allowed_points=allowed,
                  stop=True,observed_at=at))


def notices(rows,now,send=publish):
    """Presentation-derived amounts may notify only; never authorize a stop."""
    import claude_usage_messages as human
    results=[]
    for key,severity,line in rows:
        parsed=human.event(line,now,key,severity)
        if not parsed and 'OUT OF USAGE' in line.upper():
            parsed=dict(category=human.category(line),level='OUT',used='')
        if not parsed:
            # A category notice that cannot be represented is NOT delivered.
            if human.category(line) in LABELS:results.append(False)
            continue
        if parsed['category'] not in LABELS or parsed['level']=='STOPPED':continue
        pair=re.search(r'([0-9]+(?:\.[0-9]+)?) of ([0-9]+(?:\.[0-9]+)?) (?:meter )?points\b',parsed['used'])
        if not pair:
            pair=re.search(r'([0-9]+(?:\.[0-9]+)?) points vs ([0-9]+(?:\.[0-9]+)?) at (?:target|run) rate\b',parsed['used'])
        if pair:
            used,allowed=float(pair[1]),float(pair[2])
        elif parsed['level'] in ('OUT','RELEASED'):
            # 0/0 denotes no point measurement for these state-only notices;
            # receivers must omit points, never interpret counts as spending.
            used=allowed=0.
        else:
            results.append(False)
            continue
        results.append(send(dict(category=LABELS[parsed['category']],level=parsed['level'],
                  used_points=used,allowed_points=allowed,stop=False,observed_at=now)))
    return all(results)
