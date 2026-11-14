# fix-ledger

Keeps requested fixes from silently disappearing from a repository's TODO
files and fix ledger. See `LESSON.md` for why.

- `check.py REPO [--fix]` reads the ledger (`docs/fix-ledger.md`) and TODO files
  (`TODO-hi.md`, `TODO-med.md`, `TODO-lo.md`), and remembers every ledger row
  and unchecked TODO it has seen in a state inventory. With `--fix` it does
  the following:
  - restores deleted ledger rows and silently dropped TODOs (even when both
    files were deleted);
  - adds an unchecked TODO for every OPEN ledger item that lacks one;
  - reopens CLOSED process fixes that lack `proof:` and a valid `receipt:`.
    A valid receipt is JSON with `key`, `automatic: true`, `passed: true`,
    `observed_at`, `trigger` (an enabled, active systemd user `.timer` or
    `.path`), `event`, `expected`, `actual`, `repair` and `agent_fallback`, plus
    `artifacts` (repo files → SHA-256) that still match. `kind: behavior` fixes
    need only `proof:`.

  It keeps priority moves, a TODO that is checked off or listed in an archive
  (`docs/archive/*.md`), and a unique pure retitle (same owner and body). Use
  `<!-- supersedes: todo:old title -->` for retitles that also change details.
  Without `--fix` it writes nothing. It exits 1 if anything is unresolved
  (`UNFIXED`).
- `fix-ledger-run REPO` runs `check.py --fix`. Unresolved cases are
  deduplicated, saved to an outbox before sending, and removed only after the
  notifier exits 0. `--dry-run` checks without `--fix`, prints what it would
  hand off, and writes and sends nothing.
- `install.sh REPO` writes `fix-ledger-<repo>.path`, which runs the check when
  the ledger or a TODO file changes, and a 15-minute `.timer` as a backup, then
  enables both. It refuses to overwrite units it wrote for another repository with
  the same folder name. `--dest DIR` (anything but the default) implies `--no-enable`.

Per-repo settings live in `REPO/.fix-ledger.json` (all optional):

```json
{"ledger": "docs/fix-ledger.md",
 "todos": {"hi": "TODO-hi.md", "med": "TODO-med.md", "lo": "TODO-lo.md"},
 "archives": ["docs/archive/*.md"],
 "key_prefix": "fix",
 "notify": {"argv": ["t3", "thread", "send", "THREAD_ID", "-"]}}
```

`notify.argv` receives the message on stdin, or as `{text}` inside an argument.
The message quotes TODO and ledger lines, so never put `{text}` inside a shell
command (`sh -c "... {text}"`): a line containing `$(...)` would run. With no
notifier, cases are printed to the journal only. `.fix-ledger.json` is read from the
repository on every run, so anyone who can commit to it chooses the notifier command
that runs as you. Install it only for repositories you trust that far. State lives in
`~/.local/state/fix-ledger/<repo>-<hash>/`, one directory per repository.
Don't reset it as a repair: the inventory is the memory. A new install starts with
an empty one. To keep an old inventory, copy its `inventory.json` into the new
directory before the first run.

## Status

**Working, with a new layout.** The repair logic ran live on one busy multi-agent
repository, where controlled live faults (deleted TODO, deleted ledger and TODO,
text-only CLOSED) were repaired automatically. This copy reads its layout from
config instead of that repository's fixed paths, and the handoff goes through a
configurable command.
`python3 -m unittest test_guard` passes 19 offline tests in temporary
directories: deletion recovery, retitles, supersedes, receipts, outbox retry,
dry run, notifier, custom layout, and `install.sh` into a throwaway HOME (quoted
paths, no overwrite of another repo's units). `install.sh --no-enable` with a
throwaway HOME and repo produced the expected units, and `fix-ledger-run
--dry-run` on that repo printed the handoff and wrote nothing. The generated
units have not been enabled anywhere.

## Dependencies

Python 3.9+, standard library only. A systemd user session for the triggers and
for checking receipt triggers (`systemctl --user is-enabled/is-active`).

## Install / uninstall

```bash
./install.sh ~/code/my-project            # or --no-enable, --dest DIR
cp docs/fix-ledger.example.md ~/code/my-project/docs/fix-ledger.md   # if it has none
python3 ~/.local/lib/fix-ledger/fix-ledger-run ~/code/my-project --dry-run

systemctl --user disable --now fix-ledger-my-project.path fix-ledger-my-project.timer
rm ~/.config/systemd/user/fix-ledger-my-project.{service,timer,path}; systemctl --user daemon-reload
rm -r ~/.local/lib/fix-ledger   # state: ~/.local/state/fix-ledger/
```

## Limits

- It only knows requests that were written into a TODO or ledger line at
  least once. It does not read chat.
- Repairs are left in the working tree and are never committed.
- Receipt checks prove the format, the hashes and a live trigger, not that the
  prose is true.
- A failed handoff retries. If delivery succeeded before the outbox was saved,
  the same case can arrive twice.
- Only systemd `.timer` and `.path` triggers can be verified. Others stay OPEN.
