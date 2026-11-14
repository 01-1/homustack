# agent-helper-daemons

A pattern, and a small library implementing it, for standard-library Python
daemons that message T3 Code agent threads reliably. See `RULES.md` for the
rules.

- `t3helper.py`: `lookup_one(name)` (the name's threads.tsv entry, else its
  only matching thread title; raises `AmbiguousName` if several titles match),
  `search_all(name)` (every matching thread: the threads.tsv entry, then titles
  newest first), `send(name, text, dry_run)` (uses `lookup_one`; steers, falls
  back to queue, three tries, fixed log line), `State` (atomic JSON; save only after a successful send),
  `single_copy()` (lock or exit 3) and `forever()` (a loop that survives
  errors, one pass under `--dry-run`). Settings come from environment
  variables: `T3_BIN`, `T3_PROJECT`, `T3_THREADS`, `HELPER_STATE`, `HELPER_TZ`.
- `run-forever.sh LOG -- CMD...`: the restart loop. It stops on exit code 3.
- `examples/inbox_relay.py AGENT FOLDER [--dry-run]`: relays each new `*.txt`
  file in a folder to an agent's thread.

## Status

**Working (tested offline).** The rules come from a multi-agent project, where five
scripts that followed them logged 0 failed sends. `t3helper.py` is new: it
extracts those scripts' shared code without their project-specific logic.
`python3 test_t3helper.py` passes 11 offline tests against a fake `t3`:
lookup order, several matching titles refused unless threads.tsv picks one,
`search_all` order, an ambiguous name fails at once without sending, an empty name never sends, steer-then-queue, three failed tries, dry run with no send and
no state, atomic state, the example daemon (dry run, relays once, a second
copy exits 3, SIGTERM exits 0), and the restart loop stopping on exit 3. It
has not been run against a real T3 thread.

## Dependencies

Python 3.9+ and bash. The T3 Code `t3` CLI, with T3 running.

## Install / uninstall

Nothing to install. Copy `t3helper.py` next to your script, or add this folder
to `PYTHONPATH`. To run a daemon:

```bash
export T3_PROJECT=~/code/my-project
python3 examples/inbox_relay.py Builder ~/inbox --dry-run
setsid nohup ./run-forever.sh ~/.local/state/agent-helpers/inbox_relay.log -- \
    python3 examples/inbox_relay.py Builder ~/inbox >/dev/null 2>&1 &
```

To stop it, kill the `run-forever.sh` loop first, then the script (SIGTERM
exits cleanly). State is in `~/.local/state/agent-helpers/`.

## Limits

- `send` returns once `t3` accepts the message. That proves delivery to the
  thread, not that the agent read or acted on it.
- If `t3` succeeds but the process dies before `State.save()`, the message is
  sent again after a restart. A `t3` call that times out (30 s) counts as a
  failed try and is retried, so a slow send that was in fact accepted can also
  arrive twice.
- `send` refuses an empty agent name. It would match a blank `threads.tsv` field
  or any title starting with `:` or a space.
- When an agent is restarted into a new thread, the old thread keeps its
  title, so `lookup_one` sees two matches and the send fails (the log line
  lists both ids) until the old thread is renamed or threads.tsv names the
  new one.
- Thread lookup by title depends on agents' titles following the naming
  convention.
