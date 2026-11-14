"""Read-only cached category admission (Codex, Fable, Claude non-Fable); never enforces or sends."""
import sys
sys.dont_write_bytecode = True

import copy
from datetime import datetime, timezone
import json
import math
from pathlib import Path
import time

import claude_usage_boxes as boxes

SCHEMA = 1
SAMPLER_MAX_AGE = 90  # Existing sampler's own discontinuity cutoff (30s cadence).
LEDGER_MAX_AGE = 120  # Two existing 60s cached-meter cycles.
SOURCE_MAX_AGE = 1200  # Existing category coverage bound; not a provider probe.
DEFAULT_CATEGORY = 'codex'  # Schema-1 callers without --category keep the Codex decision.


class Unknown(ValueError):
    pass


def number(value):
    if type(value) not in (int, float) or not math.isfinite(value):
        raise Unknown('invalid numeric evidence')
    return value


def signature(path):
    try:
        stat = path.stat()
        return (stat.st_ino, stat.st_size, stat.st_mtime_ns)
    except FileNotFoundError:
        return None


def read_json(path):
    def invalid(_):
        raise Unknown('nonfinite JSON evidence')
    value = json.loads(path.read_text(), parse_constant=invalid,
                       parse_float=lambda value: number(float(value)))
    if not isinstance(value, dict):
        raise Unknown('object evidence required')
    return value


def gauge(row, name):
    """A row meter as a finite number. The Claude meter stores header text such as '0.42'
    (the arithmetic reads it with float()); Codex rows stay strictly numeric as before."""
    value = row[name]
    if isinstance(value, str) and row.get('provider') == 'claude':
        try:
            value = float(value)
        except ValueError:
            raise Unknown('invalid numeric evidence')
    return number(value)


def exhausted(row, category):
    """Provider meter full for this category on this account (5-hour, weekly; Fable also its own)."""
    gauges = ('u5', 'u7') if category in ('codex', 'non-fable') else ('u5', 'u7', 'uf')
    return any(row.get(name) is not None and gauge(row, name) >= 1 for name in gauges)


def check(home=None, at=None, category=DEFAULT_CATEGORY):
    """Return schema-1 decision for one category. All failures deny; no locks, writes or network.

    `home` and `at` are import-only fixture seams. The CLI always uses real home/time.
    Reasons are stable codes, never exception messages or account identifiers.
    """
    home = Path.home() if home is None else Path(home)
    at = time.time() if at is None else at
    result = dict(schema=SCHEMA, category=category, allowed=False, status='unknown', reasons=[],
                  measured_at=None, freshness={}, coverage={}, override=False)
    try:
        if category not in boxes.CATEGORIES:
            raise Unknown('unsupported category')
        provider = 'codex' if category == 'codex' else 'claude'
        number(at)
        result['measured_at'] = datetime.fromtimestamp(at, timezone.utc).isoformat()
        root = home / '.local/state/claude-usage'
        paths = dict(policy=home / '.config/claude-usage/boxes.toml',
                     ledger=root / 'category-ledger.json', samples=root / 'passive-rate.json',
                     stops=root / 'hard-stops.json', override=root / 'hard-continue.json')
        before = {key: signature(path) for key, path in paths.items()}
        if any(before[key] is None for key in ('policy', 'ledger', 'samples', 'stops')):
            raise Unknown('missing evidence')
        policy = boxes.load(paths['policy'])
        if policy['approved'] is not True:
            raise Unknown('unapproved policy')
        ledger, samples, stops = (read_json(paths[key]) for key in ('ledger', 'samples', 'stops'))
        override = read_json(paths['override']) if before['override'] is not None else {'disabled': {}}
        for key, limit in (('ledger', LEDGER_MAX_AGE), ('samples', SAMPLER_MAX_AGE)):
            age = at - before[key][2] / 1e9
            result['freshness'][key] = dict(age_seconds=age, max_age_seconds=limit)
            if not 0 <= age <= limit or before['policy'][2] > before[key][2]:
                raise Unknown('stale or incoherent evidence')
        for key in ('sources', 'starts', 'forgiven', 'runs', 'preparation'):
            if not isinstance(ledger[key], dict):
                raise Unknown('invalid ledger')
        for key in ('events', 'gaps', 'meter_intervals'):
            if not isinstance(ledger[key], list):
                raise Unknown('invalid ledger')
        for mapping in ('forgiven', 'cap_restart'):
            for key, stamp in ledger.get(mapping, {}).items():
                if key not in boxes.CATEGORIES or not 0 <= number(stamp) <= at:
                    raise Unknown('invalid ledger epoch')
        for event in ledger['events']:
            if len(event) != 3 or not 0 <= number(event[0]) <= at or event[1] not in boxes.CATEGORIES or number(event[2]) < 0:
                raise Unknown('invalid ledger event')
        for interval in ledger['meter_intervals']:
            if (len(interval) != 5 or not 0 <= number(interval[0]) <= number(interval[1]) <= at
                    or interval[2] not in boxes.CATEGORIES or number(interval[3]) < 0):
                raise Unknown('invalid meter interval')
        if not isinstance(stops['latches'], dict) or not isinstance(stops['released'], list) or not isinstance(stops['files'], dict):
            raise Unknown('invalid stops')
        disabled = override['disabled']
        if not isinstance(disabled, dict) or any(c not in boxes.CATEGORIES or not 0 < number(t) <= at for c, t in disabled.items()):
            raise Unknown('invalid override')
        result['override'] = category in disabled
        sources = [s for s in policy['sources'] if s['provider'] == provider]
        if not sources:
            raise Unknown('no Codex coverage' if category == 'codex' else 'no category coverage')
        coverage = result['coverage']
        coverage.update(configured_sources=len(sources), fresh_sources=0,
                        source_max_age_seconds=SOURCE_MAX_AGE,
                        account_routing='proxy-owned; not inferred')
        for source in sources:
            current = ledger['sources'][provider + ':' + source['email']]
            row = current['row']
            age, observed_age = at - number(current['time']), at - number(row['observed_at'])
            if row.get('provider') != provider:
                raise Unknown('invalid source coverage')
            points = boxes.gauges(row).get(category)
            if (not 0 <= age <= SOURCE_MAX_AGE or not 0 <= observed_age <= SOURCE_MAX_AGE
                    or row['provider'] != provider or row['email'] != source['email']
                    or gauge(row, 'u7') < 0 or points is None or number(points) < 0
                    or not isinstance(current.get('carried', {}), dict)
                    or category in current.get('carried', {})):
                raise Unknown('invalid source coverage')
            for name in ('u5', 'uf'):
                if row.get(name) is not None and gauge(row, name) < 0:
                    raise Unknown('invalid source gauge')
            if category in ledger.get('pending_rebase', {}).get(provider + ':' + source['email'], []):
                raise Unknown('source rebase pending')
            coverage['fresh_sources'] += 1
        # Never let readings()/rate_context() synthesize a missing opening baseline.
        run = boxes.active_run(policy, at)
        if run and (run['approved'] is not True or boxes.run_id(run) not in ledger['runs']):
            raise Unknown('missing approved opening')
        if run and not run['start'] <= number(ledger['runs'][boxes.run_id(run)]['baseline']) <= at:
            raise Unknown('invalid run opening')
        if not boxes.preparation_active(policy, at):
            raise Unknown('rate regime not covered')
        if str(policy['preparation']['start']) not in ledger['preparation']:
            raise Unknown('missing frozen preparation')
        if not 0 <= number(ledger['preparation'][str(policy['preparation']['start'])]['at']) <= at:
            raise Unknown('invalid preparation opening')
        context = boxes.rate_context(policy, copy.deepcopy(ledger), at)
        lower = max(context['start'], ledger['forgiven'].get(category, -math.inf), at - 3600)
        coverage['rate_window_start'] = lower
        coverage['sample_since'] = number(samples['token_since'])
        coverage['sample_complete'] = samples['token_complete'] is True
        sample_age = at - number(samples['token_last_sampled'])
        if not 0 <= sample_age <= SAMPLER_MAX_AGE or not coverage['sample_complete'] or not 0 <= coverage['sample_since'] <= lower:
            raise Unknown('rate coverage incomplete')
        for event in samples['events']:
            if len(event) != 3 or not 0 <= number(event[0]) <= at or event[1] not in boxes.CATEGORIES or number(event[2]) < 0:
                raise Unknown('invalid sample event')
        if any(g[1] == category and lower < number(g[0]) <= at for g in ledger['gaps']):
            raise Unknown('rate meter gap')
        # The same token snapshot used by ladder_tick(), but only in this local copy.
        local = copy.deepcopy(ledger)
        local['token_sample'] = dict(since=samples['token_since'], at=samples['token_last_sampled'],
                                     complete=samples['token_complete'], records=samples['token_records'])
        items = [r for r in boxes.readings(policy, local, at) if r['category'] == category]
        if not items or any(r['incomplete'] or not r['approved'] or number(r['allowed']) <= 0 or number(r['used']) < 0 for r in items):
            raise Unknown('fixed box coverage incomplete')
        reasons = []
        for latch in stops['latches'].values():
            if not isinstance(latch, dict) or not isinstance(latch.get('pool'), str):
                raise Unknown('invalid latch')
            categories = boxes.latch_categories(latch)
            pool_provider = latch['pool'].split(':')[0]
            if not categories or pool_provider not in ('codex', 'claude') or ((pool_provider == 'codex') != ('codex' in categories)):
                raise Unknown('unclassified latch')
            if category in categories and not result['override']:
                reasons.append(category + '_stop_latch')
        if any(r['used'] >= r['allowed'] - 1e-8 for r in items) and not result['override']:
            reasons.append(category + '_fixed_budget_stop')
        if any(e[1] >= 2 for e in boxes.warnings(items, local, at, policy)):
            reasons.append(category + '_fixed_budget_warning')
        # A NEW crossing state makes fired=True mean CURRENT math, never history.
        state = {}
        _, trips = boxes.ladder_evaluate(policy, local, samples, state, at, all_trips=True)
        if any(key.startswith(category + ':') and value['fired'] for key, value in state.get('levels', {}).items()):
            reasons.append(category + '_rate_warning')
        if any(t['category'] == category for t in trips) and not result['override']:
            reasons.append(category + '_rate_stop')
        if all(exhausted(ledger['sources'][provider + ':' + s['email']]['row'], category) for s in sources):
            reasons.append(category + '_all_accounts_exhausted')
        if before != {key: signature(path) for key, path in paths.items()}:
            raise Unknown('evidence changed during check')
        result.update(status='hold' if reasons else 'clear', allowed=not reasons, reasons=sorted(set(reasons)))
    except Unknown as exc:
        result.update(allowed=False, status='unknown', reasons=[str(exc).replace(' ', '_')])
    except Exception:
        # Never print exceptions: cached inputs can contain identifiers/metadata.
        result.update(allowed=False, status='unknown', reasons=['cached_evidence_unavailable_or_incoherent'])
    return result


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if argv == []:
        category = DEFAULT_CATEGORY
    elif len(argv) == 2 and argv[0] == '--category' and argv[1] in boxes.CATEGORIES:
        category = argv[1]
    else:
        print(json.dumps(dict(schema=SCHEMA, allowed=False, status='unknown', reasons=['unsupported_arguments'])))
        return 2
    result = check(category=category)
    print(json.dumps(result, allow_nan=False, sort_keys=True))
    return 0 if result['allowed'] else 1 if result['status'] == 'hold' else 2


if __name__ == '__main__':
    raise SystemExit(main())
