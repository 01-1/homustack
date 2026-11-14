# pace

Starts a "making-faster" helper for a T3 Code thread that has been working
for a long time. Disabled by default and never switched on so far.

Each scan (`pace.py`, or `--apply` to actually launch):

1. Reads `t3 thread activity --project <projectId> --json`. Only the
   configured project and environment count, with a fresh UTC snapshot
   (no older than 15 s).
2. Picks threads with native `eligible: true`, an active run, and more than
   300 s of continuous work. It skips excluded, settled, archived, waiting
   and helper threads.
3. Before every launch it rechecks:
   - at least 8 GiB of available memory;
   - the hold files;
   - no active `t3-load-*` units;
   - usage admission (`usage-helper-admission` from claude-usage): exit 0
     and `allowed=true`.
   Anything unknown denies.
4. Launches at most one helper per scan, with `t3 thread assist <thread>
   --run <run> --origin pace-v1`, on Codex `gpt-6.1-sol` with low effort.
   Receipts in `~/.local/state/code-pace/` keep it from launching twice.

Config `~/.config/code-pace/config.json` (see `config.example.json`):

- `enabled` (required for `--apply`).
- `projectId` and `environmentId` (required).
- `excludedThreadIds`.
- `holdFiles`: files holding an ISO time; nothing launches until it has
  passed. Missing files mean no hold.
- `admissionCommand`.

Status: untested against a real T3. The CLI has `thread activity` and
`thread assist` as of 2026-11-14, but this runner has never been enabled.
`python3 -B -m unittest -v test_pace` runs offline with a fake runner.

The unit files are templates. Nothing is installed or enabled.
