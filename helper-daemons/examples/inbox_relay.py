#!/usr/bin/env python3
"""Example daemon: relay each new file dropped in a folder to one agent's thread.

    inbox_relay.py AGENT_NAME FOLDER [--dry-run]

Every 5 s, each *.txt file in FOLDER not yet relayed is sent (its text) to the
agent. Relayed names are saved only after a successful send, so a restart neither
repeats nor loses one. Run it under ../run-forever.sh.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import t3helper as h  # noqa: E402


def main(argv):
    dry_run = '--dry-run' in argv
    args = [a for a in argv if a != '--dry-run']
    if len(args) != 2:
        print(__doc__, file=sys.stderr)
        return 2
    agent, folder = args[0], Path(args[1])
    state = h.State('inbox_relay', dry_run)
    done = state.data.setdefault('relayed', [])

    def one_pass():
        for f in sorted(folder.glob('*.txt')):
            if f.name in done:
                continue
            if h.send(agent, f'New file {f.name}:\n\n{f.read_text()}', dry_run) and not dry_run:
                done.append(f.name)
                state.save()

    return h.forever('inbox_relay', one_pass, dry_run=dry_run)


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
