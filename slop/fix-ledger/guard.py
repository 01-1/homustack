"""Durable request inventory and process completion evidence; no network or LLM calls.

Repository layout comes from LAYOUT, overridable per repo with a .fix-ledger.json
file at the repository root (see configure()).
"""
import hashlib
import json
import re
import subprocess
from pathlib import Path

ITEM = re.compile(r'- (OPEN|CLOSED) \[([^\]\s]+)\](.*)')
TODO = re.compile(r'^- \[([ xX])\] (.+)$')
DEFAULT_LAYOUT = {
    'ledger': 'docs/fix-ledger.md',
    'todos': {'hi': 'TODO-hi.md', 'med': 'TODO-med.md', 'lo': 'TODO-lo.md'},
    'archives': ['docs/archive/*.md'],   # checked-off TODO lines here count as completed
    'key_prefix': 'fix',                 # explicit keys look like [fix:name]
}
LAYOUT = dict(DEFAULT_LAYOUT)
EXPLICIT = re.compile(r'\[(fix:[^\]\s]+)\]')


def configure(root):
    """Load <root>/.fix-ledger.json over the defaults."""
    global EXPLICIT
    LAYOUT.clear()
    LAYOUT.update(json.loads(json.dumps(DEFAULT_LAYOUT)))
    cfg = Path(root) / '.fix-ledger.json'
    if cfg.exists():
        LAYOUT.update(json.loads(cfg.read_text()))
    EXPLICIT = re.compile(r'\[(' + re.escape(LAYOUT['key_prefix']) + r':[^\]\s]+)\]')
    return LAYOUT


def explicit_key(key):
    return key.startswith(LAYOUT['key_prefix'] + ':')


def fields(rest):
    return dict((k.strip(), v.strip()) for part in rest.split('|')
                if ':' in part for k, v in [part.split(':', 1)])


def identity(line):
    explicit = EXPLICIT.search(line)
    if explicit:
        return explicit[1]
    title = re.search(r'\*\*(.+?)\*\*', line)
    title = title[1] if title else line[6:]
    return 'todo:' + re.split(r'\s*\(', title, maxsplit=1)[0].strip().rstrip('.').casefold()


def retitle_signature(line):
    """Only infer a retitle when owner/qualifiers AND the full body are unchanged."""
    heading = re.search(r'\*\*(.+?)\*\*', line)
    if not heading or explicit_key(identity(line)):
        return None
    owner = heading[1].find('(')
    body = re.sub(r'<!--\s*supersedes:.*?-->', '', line[heading.end():]).strip()
    if owner < 0 or not body:
        return None
    return heading[1][owner:], body


def write_checked(path, before, after):
    if before == after:
        return
    if path.exists() and path.read_text() != before:
        raise RuntimeError(f'{path.name} changed during repair; retry next tick')
    path.write_text(after)


def completion(root, key, rest):
    f = fields(rest)
    if not f.get('proof'):
        return 'no proof'
    if f.get('kind') == 'behavior':
        return None
    try:
        p = (root / f['receipt']).resolve()
        if not p.is_relative_to(root.resolve()):
            return 'receipt must be a repository file'
        receipt = json.loads(p.read_text())
        if receipt['key'] != key or receipt['passed'] is not True:
            return 'receipt key/result mismatch'
        for name in ('observed_at', 'trigger', 'event', 'expected', 'actual', 'repair', 'agent_fallback'):
            if not isinstance(receipt[name], str) or not receipt[name].strip():
                return f'receipt missing {name}'
        if receipt['automatic'] is not True:
            return 'end-to-end observation was not automatic'
        artifacts = receipt['artifacts']
        if not isinstance(artifacts, dict) or not artifacts:
            return 'no deployed artifact hashes'
        for name, digest in artifacts.items():
            artifact = (root / name).resolve()
            if not artifact.is_relative_to(root.resolve()):
                return 'artifact outside repository'
            if hashlib.sha256(artifact.read_bytes()).hexdigest() != digest:
                return f'changed artifact {name}'
        unit = receipt['trigger']
        if not re.fullmatch(r'[a-zA-Z0-9_.@-]+\.(timer|path)', unit):
            return 'unsupported automatic trigger; agent must add a concrete verifier'
        for action in ('is-enabled', 'is-active'):
            result = subprocess.run(['systemctl', '--user', action, unit],
                                    capture_output=True, text=True, timeout=10)
            if result.returncode or result.stdout.strip() not in ('enabled', 'active'):
                return f'trigger {unit} is not {action}'
        return None
    except (KeyError, ValueError, TypeError, OSError, subprocess.SubprocessError) as exc:
        return f'missing/invalid activation and end-to-end receipt ({type(exc).__name__})'


def reconcile(root, state, fix=False):
    root, state = Path(root), Path(state)
    configure(root)
    if fix:
        state.mkdir(parents=True, exist_ok=True, mode=0o700)
    history = state / 'inventory.json'
    saved = json.loads(history.read_text()) if history.exists() else {'ledger': {}, 'todos': {}}
    errors, repaired = [], []
    lp = root / LAYOUT['ledger']
    before = lp.read_text() if lp.exists() else ''
    lines = before.splitlines()
    items = {}
    for i, line in enumerate(lines):
        m = ITEM.fullmatch(line)
        if m:
            if m[2] in items:
                errors.append(f'{m[2]}: duplicate ledger key')
            items[m[2]] = (i, m[1], m[3])
        elif re.match(r'- (OPEN|CLOSED)\b', line):
            errors.append(f'malformed item: {line[:100]}')
    for key, line in saved['ledger'].items():
        if key not in items:
            if fix:
                m = ITEM.fullmatch(line)
                lines.append(line)
                items[key] = (len(lines)-1, m[1], m[3])
                repaired.append(f'{key}: restored deleted ledger row')
            else:
                errors.append(f'{key}: deleted ledger row')
    todo_files = {p: root / name for p, name in LAYOUT['todos'].items()}
    first = next(iter(todo_files))
    texts = {p: f.read_text() if f.exists() else '' for p, f in todo_files.items()}
    updated = dict(texts)
    active, completed_lines = {}, []
    rows = [(p, line) for p, text in texts.items() for line in text.splitlines() if TODO.fullmatch(line)]
    for priority, text in texts.items():
        for line in text.splitlines():
            m = TODO.fullmatch(line)
            if m:
                key = identity(line)
                if m[1] == ' ':
                    if not explicit_key(key):
                        candidates = [(k, r) for k, r in saved['todos'].items()
                                      if identity(r['line']) == key and k not in active
                                      and k not in saved.get('superseded', {})]
                        exact = [k for k, r in candidates if r['line'] == line]
                        same_priority = [k for k, r in candidates if r['priority'] == priority]
                        count = sum(identity(other) == key for _, other in rows)
                        if exact:
                            key = exact[0]
                        elif len(candidates) == 1 and count == 1:
                            key = candidates[0][0]
                        elif len(same_priority) == 1:
                            key = same_priority[0]
                        elif count > 1 or key in saved['todos']:
                            key += '#' + hashlib.sha256(line.encode()).hexdigest()[:16]
                    active[key] = {'line': line, 'priority': priority}
                else:
                    completed_lines.append(line)
    for pattern in LAYOUT['archives']:
        for archive in sorted(root.glob(pattern)):
            completed_lines += [line for line in archive.read_text().splitlines() if TODO.fullmatch(line)]
    def completed(record):
        return any(line[6:].startswith(record['line'][6:]) for line in completed_lines)

    superseded = saved.setdefault('superseded', {})
    missing = {k: r for k, r in saved['todos'].items()
               if k not in active and k not in superseded and not completed(r)}
    new = {k: r for k, r in active.items() if k not in saved['todos']}
    for old_key, record in missing.items():
        signature = retitle_signature(record['line'])
        matches = [k for k, r in new.items() if signature and retitle_signature(r['line']) == signature]
        old_matches = [k for k, r in missing.items() if signature and retitle_signature(r['line']) == signature]
        if len(matches) == len(old_matches) == 1:
            # Retain the remembered identity, replace its wording; a later drop restores NEW wording.
            active[old_key] = active.pop(matches[0])
            new.pop(matches[0])
    for successor, record in list(active.items()):
        for predecessor in re.findall(r'<!--\s*supersedes:\s*(.*?)\s*-->', record['line']):
            candidates = [k for k, r in saved['todos'].items()
                          if k == predecessor or identity(r['line']) == predecessor]
            if predecessor in saved['todos']:
                candidates = [predecessor]
            if len(candidates) != 1 or not predecessor.startswith('todo:'):
                errors.append(f'{successor}: unknown/ambiguous supersedes marker {predecessor}')
                continue
            predecessor = candidates[0]
            if predecessor == successor:
                continue
            if predecessor in active:
                errors.append(f'{successor}: superseded predecessor still present: {predecessor}')
                continue
            # Explicitly renaming back to a former title must not leave a cycle in the lineage.
            superseded.pop(successor, None)
            superseded[predecessor] = successor
    for key, (i, status, rest) in items.items():
        if not fields(rest).get('owner'):
            errors.append(f'{key}: no owner; one must be assigned')
        if status == 'CLOSED':
            reason = completion(root, key, rest)
            if reason:
                if fix:
                    lines[i] = f'- OPEN [{key}]{rest}'
                    status = 'OPEN'
                    repaired.append(f'{key}: reopened ({reason})')
                    errors.append(f'{key}: status repaired, but owning agent must establish completion ({reason})')
                else:
                    errors.append(f'{key}: CLOSED: {reason}')
        if status == 'OPEN' and key not in active:
            if fix:
                line = f'- [ ] **[{key}]**{rest} (automatically restored; see {LAYOUT["ledger"]})'
                updated[first] += '\n' + line + '\n'
                active[key] = {'line': line, 'priority': first}
                repaired.append(f'{key}: restored OPEN TODO')
            else:
                errors.append(f'{key}: OPEN without unchecked TODO')
    # Protect all observed open TODOs, even requests not entered in the fix ledger.
    for key, record in saved['todos'].items():
        if key in active or completed(record) or key in superseded:
            continue
        if key in items and items[key][1] == 'CLOSED' and not completion(root, key, items[key][2]):
            continue
        if fix:
            updated[record['priority'] if record['priority'] in updated else first] += '\n' + record['line'] + '\n'
            active[key] = record
            repaired.append(f'{key}: restored silently dropped TODO')
        else:
            errors.append(f'{key}: silently dropped TODO')
    if fix:
        write_checked(lp, before, '\n'.join(lines) + '\n')
        for priority in texts:
            write_checked(todo_files[priority], texts[priority], updated[priority])
        saved['ledger'].update({key: lines[i] for key, (i, _, _) in items.items()})
        saved['todos'].update(active)
        temp = history.with_suffix('.tmp')
        temp.write_text(json.dumps(saved, indent=2) + '\n')
        temp.replace(history)
    return errors, repaired
