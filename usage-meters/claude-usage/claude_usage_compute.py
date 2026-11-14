"""Non-LLM compute reminders. Provider CLIs only; never changes cloud resources."""
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, time, timezone
from decimal import Decimal, ROUND_HALF_UP
import calendar
import hashlib
import json
import os
import configparser
import re
import tomllib
from pathlib import Path
import subprocess
import sys
from zoneinfo import ZoneInfo

# Tracking start (midnight US Eastern). Set CLAUDE_USAGE_COMPUTE_START=YYYY-MM-DD; changing it
# later makes the saved state refuse to load, so pick it once.
def _start(default='2026-01-01'):
    value = os.environ.get('CLAUDE_USAGE_COMPUTE_START', default)
    try:
        day = date.fromisoformat(value)
    except ValueError:
        print(f'CLAUDE_USAGE_COMPUTE_START={value!r} is not YYYY-MM-DD; using {default}', file=sys.stderr)
        day = date.fromisoformat(default)
    return datetime.combine(day, time(), ZoneInfo('America/New_York'))


START = _start()
HOME = Path.home()
STATE_NAME = 'compute-state.json'



def amount(value):
    result = Decimal(str(value))
    if not result.is_finite() or result < 0:
        raise ValueError('invalid dollar amount')
    return result


def threshold(index):
    """Exact rational recurrence, with no intermediate rounding or float math."""
    if not isinstance(index, int) or not 1 <= index <= 10000:
        raise ValueError('invalid threshold index')
    power = index - 1
    return Decimal(str(55 * 11 ** power - 50 * 10 ** power) + 'e-' + str(power))


def money(value):
    return '$' + str(amount(value).quantize(Decimal('.01'), rounding=ROUND_HALF_UP))


def cli(arguments, run=subprocess.run):
    result = run(arguments, capture_output=True, text=True, timeout=45, check=False)
    if result.returncode:
        raise OSError('provider CLI unavailable')  # Never return raw stderr/credentials.
    return json.loads(result.stdout, parse_float=Decimal)


def epoch(value):
    return datetime.fromisoformat(str(value).replace('Z', '+00:00')).timestamp()


def boat(previous, now, run=subprocess.run):
    data = cli([str(HOME / '.local/bin/boat'), '--no-update', '--json', 'billing'], run)
    limits = data['limits']
    end = datetime.fromisoformat(limits['subscriptionCurrentPeriodEnd'].replace('Z', '+00:00'))
    year, month = (end.year - 1, 12) if end.month == 1 else (end.year, end.month - 1)
    begin = end.replace(year=year, month=month, day=min(end.day, calendar.monthrange(year, month)[1]))
    if begin.timestamp() < START.timestamp():
        raise ValueError('Boat current billing counter begins before tracking start')
    # These are size-weighted seconds, including time paid by the plan. Do not
    # multiply by a machine's billingMultiplier again or add wallet machineDollars.
    denominator = amount(limits['creditSecondsPerDollar'])
    if not denominator:
        raise ValueError('Boat price unavailable')
    periods = dict(previous.get('periods', {}))
    key = limits['subscriptionCurrentPeriodEnd']
    periods[key] = str(max(amount(periods.get(key, 0)), amount(limits['creditUsedSeconds']) / denominator))
    # Validate pricing with the CLI's own per-sandbox meter, not a hardcoded rate.
    listing = cli([str(HOME / '.local/bin/boat'), '--no-update', '--json', 'list', '--all'], run)
    checked_types = set()
    for machine in listing['sandboxes']:
        if machine['type'] in checked_types:
            continue
        checked_types.add(machine['type'])
        usage = cli([str(HOME / '.local/bin/boat'), '--no-update', '--json', 'usage', machine['id'],
                     '--since', START.isoformat()], run)
        if abs(amount(usage['dollars']) - amount(usage['seconds']) / denominator) > Decimal('.0001'):
            raise ValueError('Boat per-machine price disagrees with account counter')
    wallet = data.get('walletStatus')
    if not isinstance(wallet, dict):
        raise ValueError('Boat wallet spending missing')
    days = dict(previous.get('wallet_days', {}))
    for day in wallet['spending']['days']:
        if day['day'] >= START.date().isoformat():
            # First UTC day had zero wallet charges when tracking was enabled;
            # therefore no pre-start (00:00-05:00 UTC) wallet charge is included.
            other = amount(day['startDollars']) + amount(day['otherDollars'])
            days[day['day']] = str(max(amount(days.get(day['day'], 0)), other))
    starts = dict(previous.get('invoice_starts', {}))
    overage = limits['keepGoing']
    if amount(overage['usedDollars']) and epoch(overage['enabledAt']) < START.timestamp():
        raise ValueError('Boat extra-start counter predates tracking start')
    starts[key] = str(max(amount(starts.get(key, 0)), amount(overage['usedDollars'])))
    total = sum((amount(v) for values in (periods, days, starts) for v in values.values()), Decimal(0))
    return dict(amount=str(total), periods=periods, wallet_days=days, invoice_starts=starts)


def modal(previous, now, run=subprocess.run):
    rows = cli([str(HOME / '.local/bin/modal'), 'billing', 'report', '--start', START.date().isoformat(),
                '--resolution', 'h', '--tz', 'America/New_York', '--show-resources', '--json'], run)
    if not isinstance(rows, list):
        raise ValueError('Modal billing rows missing')
    total = Decimal(0)
    for row in rows:
        resource = row['resource'].lower()
        if not any(word in resource for word in ('llm', 'token')) and START.timestamp() <= epoch(row['interval_start']) <= now:
            total += amount(row['cost'])
    return dict(amount=str(total), note='hourly report, before credits')


def runpod(previous, now, run=subprocess.run):
    total = Decimal(0)
    for kind in ('pods', 'serverless', 'network-volume'):
        rows = cli([str(HOME / '.local/bin/runpodctl'), 'billing', kind, '--start-time', START.isoformat(),
                    '--end-time', datetime.fromtimestamp(now, timezone.utc).isoformat(), '--bucket-size', 'hour'], run)
        if not isinstance(rows, list):
            raise ValueError('RunPod billing schema not recognized')
        for row in rows:
            if 'totalCost' in row:
                total += amount(row['totalCost'])
            elif all(key in row for key in ('gpuCost', 'cpuCost', 'diskCost')):
                total += sum((amount(row[key]) for key in ('gpuCost', 'cpuCost', 'diskCost')), Decimal(0))
            else:
                raise ValueError('RunPod billing cost missing')
    return dict(amount=str(total))


def vast(previous, now, run=subprocess.run):
    data = cli([str(HOME / '.local/bin/vastai'), 'show', 'invoices', '--start_date',
                START.isoformat(), '--end_date', datetime.fromtimestamp(now, timezone.utc).isoformat(), '--only_charges', '--raw', '--retry', '0'], run)
    rows = data['invoices'] if isinstance(data, dict) else data
    if not isinstance(rows, list):
        raise ValueError('Vast invoice schema not recognized')
    return dict(amount=str(sum((abs(Decimal(str(row['amount']))) for row in rows), Decimal(0))))


READERS = {'boat': boat, 'Modal': modal, 'RunPod': runpod, 'Vast': vast}


def credential_status(home=None, environ=None):
    """Check native local sign-in stores; never print secrets or call a provider."""
    home = HOME if home is None else home
    env = os.environ if environ is None else environ
    def file(relative):
        try:
            return bool((home / relative).read_text().strip())
        except FileNotFoundError:
            return False
        except OSError:
            return (home / relative).exists()  # Unreadable credentials are an outage.
    def fields(relative, names, kind='json'):
        try:
            content=(home / relative).read_text(encoding='utf-8-sig')
            data=tomllib.loads(content) if kind=='toml' else json.loads(content)
            return any(bool(data.get(k)) for k in names)
        except FileNotFoundError:
            return False
        except (OSError,ValueError,TypeError,AttributeError):
            return file(relative)
    def aws():
        if env.get('AWS_ACCESS_KEY_ID') and env.get('AWS_SECRET_ACCESS_KEY'):
            return True
        try:
            config=configparser.ConfigParser()
            config.read(home / '.aws/credentials')
            return any(config.get(section,'aws_access_key_id',fallback='')
                       for section in config.sections() if 'r2' not in section.lower())
        except (OSError,ValueError,configparser.Error):
            return file('.aws/credentials')
    def oci_login():
        try:
            config=configparser.ConfigParser()
            with (home / '.oci/config').open(encoding='utf-8-sig') as source:
                config.read_file(source)
            sections=[config.defaults()]+[dict(config[section]) for section in config.sections()]
            return any(d.get('tenancy') and (d.get('security_token_file') or
                       (d.get('user') and d.get('key_file'))) for d in sections)
        except FileNotFoundError:
            return False
        except (OSError,ValueError,configparser.Error):
            return file('.oci/config')
    def azure_login():
        return (fields('.azure/msal_token_cache.json',['AccessToken','RefreshToken']) or
                fields('.azure/service_principal_entries.json',['service_principal_entries']) or
                any((home / '.azure' / name).exists() and (home / '.azure' / name).stat().st_size
                    for name in ('msal_token_cache.bin','service_principal_entries.bin')))
    def modal_login():
        try:
            data=tomllib.loads((home / '.modal.toml').read_text())
            return any(isinstance(v,dict) and v.get('token_id') and v.get('token_secret') for v in data.values())
        except FileNotFoundError:
            return False
        except (OSError,ValueError):
            return file('.modal.toml')
    def fly_login():
        try:
            text=(home / '.fly/config.yml').read_text()
            return any(re.match(r'^\s*(?:access_token|access-token|token)\s*:\s*[^\s#]',line,re.I)
                       and line.partition(':')[2].strip() not in ('null','~',"''",'""') for line in text.splitlines())
        except FileNotFoundError:
            return False
        except OSError:
            return (home / '.fly/config.yml').exists()
    return {
        'boat':bool(env.get('BOAT_API_KEY') or fields('.config/ascii/boat/config.json',['token'])),
        'Modal':bool((env.get('MODAL_TOKEN_ID') and env.get('MODAL_TOKEN_SECRET')) or modal_login()),
        'RunPod':bool(env.get('RUNPOD_API_KEY') or fields('.runpod/config.toml',['apikey','api_key','apiKey'],'toml')),
        'Vast':bool(env.get('VAST_API_KEY') or file('.config/vastai/vast_api_key') or file('.vast_api_key')),
        'AWS':bool(aws()),
        'Azure':bool((env.get('AZURE_CLIENT_ID') and env.get('AZURE_CLIENT_SECRET'))
                     or azure_login()),
        'OCI':bool(oci_login()),
        'Rackspace':bool(env.get('SPOTINST_TOKEN') or fields('.spot_config',['token','refresh_token','access_token'])),
        'Fly':bool(env.get('FLY_API_TOKEN') or fly_login()),
    }


def unavailable(previous, now):
    raise OSError('local CLI billing reader not available')


READERS.update({name:unavailable for name in ('AWS','Azure','OCI','Rackspace','Fly')})


def monitored(p):
    return not p.get('not_setup', False)


def since():
    return f'{START:%b} {START.day} 00:00 ET'


def provider_split(state):
    return ', '.join(f'{name} {money(p["amount"])}' if 'amount' in p else f'{name} unread'
                     for name,p in state['providers'].items() if monitored(p)) or 'none configured'


def notice(state, index, failures, recoveries):
    crossed=f'; crossed {money(threshold(index))}' if index>state['fired'] else ''
    text=(f'For you: Compute since {since()}: {money(total(state))} reported ({provider_split(state)})'
          f'{crossed}; next {money(threshold(index+1))}.')
    details=[]
    missing=[name for name,p in state['providers'].items() if monitored(p) and p.get('failures')]
    if missing:
        details.append('Unread: '+', '.join(missing)+'; last known amounts retained.')
    if failures:
        details.append('Three consecutive checks failed: '+', '.join(failures)+'.')
    if recoveries:
        details.append('Recovered: '+', '.join(recoveries)+'; billing reads work again.')
    return text+('\n'+' '.join(details) if details else '')


def initial():
    return dict(version=1, start=START.isoformat(), fired=0, providers={}, pending=None)


def read(path):
    try:
        state = json.loads(path.read_text())
    except FileNotFoundError:
        return initial()
    if state.get('version') != 1 or state.get('start') != START.isoformat():
        raise ValueError('compute state has another tracking start')
    if not isinstance(state.get('fired'), int) or state['fired'] < 0 or not isinstance(state.get('providers'), dict):
        raise ValueError('invalid compute state')
    return state


def total(state):
    return sum((amount(p.get('amount', 0)) for p in state['providers'].values()), Decimal(0))


def summary(state):
    providers = state['providers']
    if not providers:
        return f'Compute since {since()}: not checked yet; next reminder $5.00.'
    stale=', '.join(name for name,p in providers.items() if monitored(p) and p.get('failures'))
    result=f'Compute since {since()}: {money(total(state))} reported ({provider_split(state)}); next {money(threshold(state["fired"]+1))}.'
    if stale:
        result+=' Unread now: '+stale+'; last known amounts retained.'
    not_setup=', '.join(name for name,p in providers.items() if not monitored(p))
    if not_setup:
        result+=' not set up: '+not_setup+'.'
    return result


def send_notice(text, key, run=subprocess.run):
    result = run([sys.executable, str(HOME / '.local/lib/matrix-relay/notify_mirror.py'), '--producer', 'usage', '--event-key', key],
                 input=text, text=True, capture_output=True, timeout=10, check=False)
    return result.returncode == 0 and result.stdout.strip() == 'queued'


def refresh(host, now, readers=None, sender=send_notice, configured=None):
    """At most one newly queued reminder per check; durable pending event + key."""
    readers = READERS if readers is None else readers
    configured = credential_status() if configured is None else configured
    path = host.LOG.parent / STATE_NAME
    with host.guard_lock(path):
        state = read(path)
        def save():
            host.atomic_write(path, json.dumps(state, sort_keys=True) + '\n')
        def flush():
            event = state['pending']
            try:
                sent = sender(event['text'], event['key'])
            except (OSError, subprocess.SubprocessError):
                sent = False
            if sent:
                state['fired'] = max(state['fired'], event['index'])
                for name, epoch_id in event.get('recoveries', {}).items():
                    p=state['providers'].get(name,{})
                    if p.get('recovery_pending')==epoch_id:
                        p.pop('recovery_pending',None)
                for name, epoch_id in event['failures'].items():
                    p = state['providers'].get(name, {})
                    if p.get('failure_since') == epoch_id:
                        p['failure_notified'] = True
                state['pending'] = None
                save()
            return sent
        # Prior successful billing remains monitored even after credentials vanish.
        for name in readers:
            p=state['providers'].setdefault(name,{})
            p['not_setup']=not (p.get('last_ok') is not None or configured.get(name,False))
            if p['not_setup']:
                p.update(failures=0,failure_notified=False)
                p.pop('failure_since',None)
                p.pop('recovery_pending',None)
        active={name:reader for name,reader in readers.items() if monitored(state['providers'][name])}
        with ThreadPoolExecutor(max_workers=max(1,len(active))) as pool:
            futures={name:pool.submit(reader,dict(state['providers'][name]),now) for name,reader in active.items()}
            for name,future in futures.items():
                p=state['providers'][name]
                try:
                    result=future.result()
                    result['amount']=str(max(amount(p.get('amount',0)),amount(result['amount'])))
                    if p.get('failure_notified'):
                        p['recovery_pending']=p['failure_since']
                    p.update(result)
                    p.update(failures=0,last_ok=now,failure_notified=False,not_setup=False)
                    p.pop('failure_since',None)
                except (OSError,ValueError,KeyError,TypeError,ArithmeticError,subprocess.SubprocessError):
                    p['failures']=p.get('failures',0)+1
                    p.setdefault('failure_since',now)
        # Migrate old false-alarm state/pending notices quietly, without recovery spam.
        had_pending=bool(state['pending'])
        if state['pending']:
            event=state['pending']
            event['failures']={name:stamp for name,stamp in event['failures'].items()
                               if monitored(state['providers'].get(name,{'not_setup':True}))
                               and state['providers'][name].get('failures',0)>=3
                               and state['providers'][name].get('failure_since')==stamp}
            event['recoveries']={name:stamp for name,stamp in event.get('recoveries',{}).items()
                                 if not state['providers'].get(name,{}).get('failures')
                                 and state['providers'].get(name,{}).get('recovery_pending')==stamp}
            if event['index']<=state['fired'] and not event['failures'] and not event['recoveries']:
                state['pending']=None
                had_pending=False
            else:
                event['text']=notice(state,event['index'],event['failures'],event['recoveries'])
                flush()
        state['checked_at'] = now
        index = state['fired']
        while total(state) >= threshold(index + 1):
            index += 1
        failures={name:p['failure_since'] for name,p in state['providers'].items()
                  if monitored(p) and p.get('failures',0)>=3 and not p.get('failure_notified')}
        recoveries={name:p['recovery_pending'] for name,p in state['providers'].items()
                    if monitored(p) and not p.get('failures') and 'recovery_pending' in p}
        if not state['pending'] and (index>state['fired'] or failures or recoveries):
            key=hashlib.sha256(json.dumps([state['start'],index,failures,recoveries],sort_keys=True).encode()).hexdigest()
            state['pending']=dict(key='compute-'+key,text=notice(state,index,failures,recoveries),
                                  index=index,failures=failures,recoveries=recoveries)
        save()
        if state['pending'] and not had_pending:
            flush()
        return summary(state)


def cached(host):
    try:
        state = read(host.LOG.parent / STATE_NAME)
        return summary(state)
    except (OSError, ValueError, KeyError, TypeError, ArithmeticError):
        return 'Compute total cannot be read; last reminders and usage stops are unchanged.'
