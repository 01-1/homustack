#!/usr/bin/env python3
"""Check (and with --fix, repair) a repository's fix ledger and TODO files.

    check.py [ROOT] [--fix] [--state DIR]
Exit 0 when clean, 1 when something needs an owner (UNFIXED lines).
Without --fix nothing is written. See README.md.
"""
import argparse
import fcntl
import sys
from pathlib import Path

# -I excludes the working directory: load only this exact sibling.
sys.path.insert(0, str(Path(__file__).resolve().parent))
from guard import reconcile


def default_state(root):
    """One state directory per repository: ~/.local/state/fix-ledger/<name>-<hash>."""
    import hashlib
    root = Path(root).resolve()
    return Path.home() / '.local/state/fix-ledger' / f"{root.name}-{hashlib.sha256(str(root).encode()).hexdigest()[:8]}"


def check(root, fix=False, state=None):
    state = Path(state or default_state(root))
    if not fix:  # read-only: no directory, lock or inventory writes
        return reconcile(Path(root).resolve(), state, fix)
    state.mkdir(parents=True, exist_ok=True, mode=0o700)
    with (state / 'check.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        return reconcile(Path(root).resolve(), state, fix)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('root', nargs='?', default='.')
    parser.add_argument('--fix', action='store_true')
    parser.add_argument('--state', type=Path)
    args = parser.parse_args()
    errors, repaired = check(args.root, args.fix, args.state)
    for item in repaired:
        print('FIXED', item)
    for item in errors:
        print('UNFIXED', item)
    if not errors and not repaired:
        print('fix-ledger OK')
    sys.exit(1 if errors else 0)
