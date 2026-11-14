# agents-killall

An emergency stop for AI coding agents on a Linux desktop. It finds the agent
processes, the apps that launch them, and the systemd user units that would
restart or wake them, then stops all of it in a safe order. What counts as a
target is set in a config file. Nothing is hard-coded.

```sh
agents-killall                       # dry run (the default): show what would stop
agents-killall --verify              # exit 1 if anything selected is still running
ka                                   # REAL stop, no confirmation (terminal only)
ka --keep app                        # stop agents but leave the "app" group running
ka --include proxies                 # also stop an optional group
ka open                              # the recovery agent's stop, keeping [ka_open] keep groups
agents-killall --yes                 # the same real stop, without the terminal check
agents-killall --check-config        # validate the config only
```

## Status

The general version of a stop tool that was used on one laptop. The process
selection, pidfd signalling, unit ordering and guards come from that tool. That
tool's site-specific parts are left out: notification rerouting, scheduler-database
edits, cloud job stops and a model-based post-check.

This version has **not been run for real**. Its tests (`test_agents_killall.py`)
use fake process tables, a fake `/proc` and mocked `systemctl`. Before relying
on it, write your config, read the dry-run output closely, and test it on a
machine where an unwanted stop is harmless.

Needs Linux with cgroup v2, systemd user sessions, and Python 3.11+ (`tomllib`,
`os.pidfd_open`). No third-party packages.

## What it stops, in order

1. **Units.** First the `first_units` (for example a watchdog that would restart
   agents): disabled and stopped (a scope is only stopped). Then the units matching an active group's
   patterns: timers, paths and sockets, then services, then scopes. Scopes are
   only stopped. Other units are `disable --now`ed, or with `unit_action = "stop"`
   only stopped and left enabled. A unit that fails, times out (25 s) or no
   longer exists is reported and the stop carries on. One bad unit never prevents
   step 2. If `systemctl` can't list units at all, `--yes` still tries the
   `first_units`, goes on to step 2 and exits 1; a dry run fails instead.
2. **Processes.** SIGTERM to every selected process, then SIGKILL after
   `grace_seconds` to whatever is left. New targets that appear during the wait
   are added. Children orphaned when their parent exits are remembered by PID and
   start time and killed too. Each signal goes through a pidfd after rechecking
   the start time, so a reused PID is never signalled.
3. **Verify.** Re-scan. The run exits 1 if any selected process is left, any
   planned unit is still active (or still enabled, for `disable` units), or any
   step failed.

Exit codes: 0 when the dry run finished or every check passed; 1 when the stop or
verify failed; 2 on a refusal, a bad config or a bad invocation.

## Selecting targets

The config lives at `~/.config/agents-killall/config.toml`, where `~` is the
home directory from the password database, never `$HOME` or `$XDG_CONFIG_HOME`
(`--config PATH` overrides it, except for `--open`). `config.example.toml` documents every key. A
`[[group]]` selects processes and units:

| key | meaning |
| --- | --- |
| `names` | exact basenames: executable, `comm`, `argv[0]`, or the script in `argv[1]` for node/bash/sh/python. Other arguments and prompt text never match. |
| `only_in_cgroups` | regexes. When set, `names` match only in a matching cgroup, e.g. only agents an app launched, not your own terminal sessions. |
| `cgroups` | regexes. Every process in a matching cgroup is selected, whatever its name. |
| `child_names` + `parents` | select a process with one of these names whose parent belongs to one of the listed groups, even if that group is kept. |
| `units` | shell globs over `systemctl --user` unit names. |
| `unit_action` | `disable` (default) or `stop`. |
| `optional` | when true, the group is stopped only with `--include NAME`. |

`names` and `child_names` must be at least 3 characters. A unit pattern needs a
literal prefix of at least 3 characters and must end in a literal unit type or
`.*` (`*.service`, `*service` and `app-*` are refused). A string where a list
belongs is an error, never a list of one-letter names.

The first group in file order that matches a process owns it. All descendants of
selected processes are stopped too, except processes that match `[preserve]
names` (and their descendants) and processes that belong to a kept or
not-included group. `--keep NAME` leaves a group's processes and units alone, and
a unit that matches a kept group's pattern is never touched by group patterns, even
if another group's pattern also matches. `first_units` are the exception: they are
always disabled and stopped, whatever `--keep` says.

Only the current user's processes are considered: those whose real, effective
and saved UIDs all match. Setuid helpers and other users' processes are never
signalled. Same-user processes that made themselves non-dumpable are still found.

## Safety guards

- **Dry run by default.** Nothing is signalled, stopped or disabled without
  `--yes`. `--dry-run`, `--yes` and `--verify` are mutually exclusive.
- **Never as root.** Running as UID 0 is refused before anything is inspected.
- **Not from inside what it stops.** `--yes` is refused when:
  - the tool runs beneath a process it would stop (an agent's shell, say);
  - its own cgroup matches an active group's `cgroups`, which catches an agent
    that detached the call from its process tree;
  - its own cgroup sits inside a unit it would stop.

  Each of these would kill the run halfway through. Run it from a separate desktop
  terminal.
- **`ka` needs a terminal.** It refuses unless stdin and stdout are both TTYs,
  then runs `agents-killall --yes` with your options. The TTY check only stops
  accidental scripted use. The guards above protect against an agent with a PTY.
- **`ka open` is for one recovery agent.** It needs a `[ka_open]` table and runs
  `agents-killall --open`: the same stop with the table's `keep` groups left
  running, no terminal needed. It is allowed only beneath a process started with
  `--name <recovery_name>`, or, when `gate` is set, only when the gate exits 0;
  the gate then decides instead. The recovery agent and its descendants are
  never stopped by `--open`, even if a group selects them, but it must not run
  inside a unit a group would stop. Every other real stop (`ka`,
  `agents-killall --yes`) is refused beneath the recovery agent. `--open` refuses
  `--config`. The gate must be an absolute path (or `~/...`) and runs with a fixed
  `PATH=/usr/bin:/bin`, `HOME` and `LANG` only. `ka` runs `/usr/bin/python3 -I`.
  So the caller's environment and options never choose the recovery name, the
  gate or the interpreter. These are accident guards, not a security boundary:
  any process can start itself with `--name <recovery_name>`, and any caller can
  run `agents-killall --yes` with its own `--config`.
- **No secrets in output.** Process rows show PID, PPID, group and state only,
  never command lines or environment. `systemctl` stderr is not relayed.
- **Bad config fails closed.** Unknown keys, invalid regexes, unknown group
  names in `--keep`/`--include`, or a group that selects nothing make it exit 2
  before it inspects anything.

Not covered: other users, other machines, cron jobs, remote or cloud agents, and
processes that match no rule and have no selected ancestor (for example a
daemonized job outside any configured cgroup).

## Install

```sh
mkdir -p ~/.config/agents-killall
cp config.example.toml ~/.config/agents-killall/config.toml   # then edit it
./install.sh              # links agents-killall and ka into ~/.local/bin
agents-killall            # check the dry run before trusting ka
```

`install.sh` refuses (exit 2) when the config is missing or invalid (it runs
`agents-killall --check-config`), since there are no built-in targets. It moves an existing `agents-killall` or `ka` aside to
`NAME.bak-STAMP` before linking. `ka` runs the `agents-killall` next to its own
resolved path, so the symlinks keep the pair together.

## Tests

```sh
python3 -m unittest -v test_agents_killall
```

The tests can't signal or stop anything real. `main --yes` runs only with every
command and signal path mocked, and the `ka` tests run a copy of `ka` next to an
inert stub. `install.sh` is never run; its test checks only its text. The gate
test runs `true` and `false`. The PTY helper closes its own slave descriptor and reads with a
deadline, so a `ka` that refuses without printing can't hang the suite.
