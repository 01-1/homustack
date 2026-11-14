#!/usr/bin/env python3
"""Small helpers for standard-library daemons that message T3 Code agent threads.

They implement RULES.md: thread lookup by agent name, sends that steer and fall
back to queue with three tries, state saved atomically only after a successful
send, a one-copy lock, a fixed log line, and --dry-run.

Settings (environment variables, with defaults):
  T3_BIN        ~/.t3/bin/t3
  T3_PROJECT    the current directory: the T3 project whose threads you message
  T3_THREADS    <T3_PROJECT>/threads.tsv   ("name<TAB>threadId" per line; last line wins)
  HELPER_STATE  ~/.local/state/agent-helpers
  HELPER_TZ     the system's local time zone (e.g. America/New_York)
"""
import datetime
import fcntl
import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path
from zoneinfo import ZoneInfo

T3 = os.path.expanduser(os.environ.get('T3_BIN', '~/.t3/bin/t3'))
PROJECT = os.path.expanduser(os.environ.get('T3_PROJECT', os.getcwd()))
THREADS = os.path.expanduser(os.environ.get('T3_THREADS', os.path.join(PROJECT, 'threads.tsv')))
STATE_DIR = Path(os.path.expanduser(os.environ.get('HELPER_STATE', '~/.local/state/agent-helpers')))
TZ = ZoneInfo(os.environ['HELPER_TZ']) if os.environ.get('HELPER_TZ') else None
TIMEOUT = 30


def now_hms():
    return (datetime.datetime.now(TZ) if TZ else datetime.datetime.now()).strftime('%H:%M:%S')


def log(status, name, tid, message, error=''):
    """One line per send or failure; under WOULD SEND the full message follows."""
    print(f"{now_hms()} {status} {name} {tid or 'NOT FOUND'} {' '.join(message.split())[:80]}"
          + (f' {error}' if error else ''), flush=True)
    if status == 'WOULD SEND':
        print(message, flush=True)


def t3(args, stdin=None):
    return subprocess.run([T3, *args], cwd=PROJECT, input=stdin, capture_output=True,
                          text=True, timeout=TIMEOUT)


class AmbiguousName(LookupError):
    """More than one thread title matches the name and threads.tsv doesn't pick one."""


def _check_name(name):
    if not isinstance(name, str) or not name.strip():
        # An empty name would match a blank threads.tsv field or any title starting with ':' or ' '.
        raise ValueError('agent name is empty')


def _tsv_entry(name):
    """The thread id on the last threads.tsv line whose first field is `name`, or None."""
    found = None
    try:
        with open(THREADS, encoding='utf-8') as f:
            for line in f:
                fields = line.rstrip('\n').split('\t')
                if len(fields) >= 2 and fields[0].strip() == name and fields[1].strip():
                    found = fields[1].strip()
    except OSError:
        pass
    return found


def _title_matches(name):
    """Thread ids whose title is `name`, starts with `name:`, `name ` or `You are name.`; newest first."""
    p = t3(['thread', 'list', '--project', PROJECT, '--json'])
    if p.returncode != 0:
        raise RuntimeError('t3 thread list failed: ' + (p.stderr.strip().splitlines() or ['?'])[-1])
    matches = [x for x in json.loads(p.stdout) if (x.get('title') or '') == name or
               (x.get('title') or '').startswith((name + ':', name + ' ', f'You are {name}.'))]
    return [x['threadId'] for x in sorted(matches, key=lambda x: x.get('createdAt') or '', reverse=True)]


def lookup_one(name):
    """The one thread for `name`: its threads.tsv entry, else its only matching thread title.
    None if nothing matches; AmbiguousName if several titles match. Never cached."""
    _check_name(name)
    found = _tsv_entry(name)
    if found:
        return found
    ids = _title_matches(name)
    if len(ids) > 1:
        raise AmbiguousName(f'{len(ids)} threads match {name!r} ({", ".join(ids)}); '
                            'add the right one to threads.tsv or rename the others')
    return ids[0] if ids else None


def search_all(name):
    """Every thread for `name`: its threads.tsv entry first (if any), then matching titles newest first."""
    _check_name(name)
    found = _tsv_entry(name)
    ids = _title_matches(name)
    return ([found] if found else []) + [i for i in ids if i != found]


def send(name, message, dry_run=False, tries=3, pause=2):
    """Send to the agent called `name`. Returns True on success. Steers (auto), then queue."""
    error = ''
    tid = None
    for attempt in range(tries):
        if attempt:
            time.sleep(pause)
        try:
            tid = lookup_one(name)
            if tid is None:
                error = 'not found'
                continue
            if dry_run:
                log('WOULD SEND', name, tid, message)
                return True
            for mode in ('auto', 'queue'):
                p = t3(['thread', 'send', '--mode', mode, tid, '-'], stdin=message)
                if p.returncode == 0:
                    log('SENT', name, tid, message)
                    return True
                error = (p.stderr.strip().splitlines() or [f'exit {p.returncode}'])[-1]
        except AmbiguousName as e:
            error = f'{type(e).__name__}: {e}'
            break  # retrying can't fix it
        except Exception as e:  # noqa: BLE001 - every failure counts as a failed try
            error = f'{type(e).__name__}: {e}'
    if dry_run:
        log('WOULD SEND', name, tid, message, '(lookup failed: ' + error + ')')
        return False
    log('FAILED', name, tid, message, error)
    return False


class State:
    """JSON state saved atomically (tmp + os.replace). Call save() only after a successful send."""

    def __init__(self, script, dry_run=False):
        self.path = STATE_DIR / f'{script}.json'
        self.dry_run = dry_run
        try:
            self.data = json.loads(self.path.read_text())
        except FileNotFoundError:
            self.data = {}
        except (OSError, ValueError) as e:
            print(f'{now_hms()} state file unreadable, treating as missing: {e}', flush=True)
            self.data = {}

    def save(self):
        if self.dry_run:
            return
        STATE_DIR.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix('.json.tmp')
        with open(tmp, 'w', encoding='utf-8') as f:
            f.write(json.dumps(self.data, indent=1, sort_keys=True))
            f.flush()
            os.fsync(f.fileno())  # so a crash cannot leave an empty file in place of the old state
        os.replace(tmp, self.path)


def single_copy(script):
    """Take the one-copy lock or exit 3 ("already running"). Keep the returned file open."""
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    lock = open(STATE_DIR / f'{script}.lock', 'w')
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        print('already running', flush=True)
        sys.exit(3)
    return lock


def forever(script, one_pass, poll=5, dry_run=False):
    """Run one_pass() forever, catching every exception; one pass and exit 0 under --dry-run."""
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))
    if dry_run:
        one_pass()
        return 0
    lock = single_copy(script)  # noqa: F841 - held until exit
    try:
        while True:
            try:
                one_pass()
            except Exception as e:  # noqa: BLE001
                print(f'{now_hms()} ERROR {type(e).__name__}: {e}', flush=True)
            time.sleep(poll)
    except KeyboardInterrupt:
        return 0
