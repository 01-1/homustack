# homustack

Rules, skills and small tools for running many coding agents at once, mostly Claude Code and Codex inside [T3 Code](https://github.com/pingdotgg/t3code). Built while running dozens of agents on one project. Each folder stands alone; its own README has the details. Everything needs Python 3 (standard library only) and runs on Linux; the T3 helpers need the `t3` CLI with T3 Code running.

`slop/` holds rougher, less-tested extras.

## Folders

**`agent-skills/`**: six Claude Code skills. review-loop and review-roll have a fresh reviewer check a change in bounded rounds (before or after it ships); retire-role splits a role across several owners; ai-debate runs a debate between models; stop-all-threads and settle-all-threads stop or settle every thread in T3 projects. Status: used in practice; review-loop most. No tests (they are instructions). Install by copying the folders into `~/.claude/skills/` (see `agent-skills/README.md`). review-loop and review-roll derive from MIT-licensed tstack skills; keep `LICENSE-tstack` with them.

**`helper-daemons/`**: `RULES.md`, the rules for small long-running scripts that message agent threads (one lock, atomic state saved after a send, retries, logging, a restart loop, `--dry-run`), and `t3helper.py`, a library that implements them, with an example inbox relay. Status: working, tested offline only against a fake `t3`. Tests: `cd helper-daemons && python3 test_t3helper.py`.

**`killall/`**: `agents-killall` and `ka`, an emergency stop for AI coding agents on a Linux desktop: finds agent processes, the apps that launch them and the systemd user units that would restart them, and stops them in a safe order. Dry run by default; targets come only from your config. Status: tested with fake process tables only, never run for real. Tests: `cd killall && python3 test_agents_killall.py`. Install: write `~/.config/agents-killall/config.toml` from `config.example.toml`, then `./install.sh`.

**`spawn-cap/`**: a PreToolUse hook for Claude Code and Codex that blocks a spawn or wake when more than a set number of agents (10 by default) would be running at once under one thread, plus `spawn-cap tree`, the parent-child tree of who started whom (read from T3's database). Fails open. Status: a close variant is in daily use; this copy's tests pass. Tests: `cd spawn-cap && python3 test_spawn_cap.py`. Install: see `spawn-cap/README.md` for the hook entries.

**`usage-meters/`**: meters and alerts for Claude and Codex subscription usage served through [CLIProxyAPI](https://github.com/router-for-me/CLIProxyAPI) proxies. `usage-alert` pops up when every account has hit a cap; `claude-usage` is the meter with usage boxes and rate ladders per category; `usage-dashboard` is a local page; `pace` starts a helper for a long-running T3 thread when usage allows. Status: usage-alert and the dashboard worked in use; `claude-usage` works but is tied to one setup (see its Limits section); `pace` was never switched on. Tests: in each subfolder, for example `cd usage-meters/claude-usage && HOME=$(mktemp -d) python3 -B -m unittest test_boxes` (one file at a time, or `discover -p 'test_*.py'` for all 557). Install: see `usage-meters/README.md`.

**`slop/agent-rules/`**: `AGENTS.md`, a general rule file for coding agents (reading the human, doing the work, asking first, accuracy, subagents and models, messaging agents in T3 Code, helper scripts, replies); `LESSONS.md`, 19 lessons from the multi-agent project; and three example Claude Code memories. Status: reference; the rules were used, the file as a whole was not. No tests. Install: copy `AGENTS.md` into a project and add `@AGENTS.md` to its `CLAUDE.md` (see its README).

**`slop/slop-skills/`**: twelve more skills: intent-reviewer, record-standing-rule, coordinator-handoff, draft-for-review, now-list, live-system-change, research-digest, dual-agent, babysit, making-faster, ping-owning-thread and encrypted-backup. Several skills in `agent-skills/` point at four of these (record-standing-rule, coordinator-handoff, live-system-change, dual-agent). Status: rewritten from project-specific versions, lightly used. No tests. Install as for `agent-skills/`. dual-agent derives from a tstack skill; keep its `LICENSE-tstack`.

**`slop/fix-ledger/`**: keeps requested fixes from going missing. It remembers every request in a repository's TODO and fix ledger, restores ones that disappear, and reopens "closed" process fixes that have no enabled trigger and end-to-end receipt. Runs from systemd user `.path` and `.timer` units. Status: the repair logic ran live on one repository; this configurable version is tested offline only and has not been enabled anywhere. Tests: `cd slop/fix-ledger && python3 -m unittest test_guard`. Install: `./install.sh <repo>` (see its README).

## Install

Clone the repo anywhere, then follow the install line of each folder you want. Nothing installs itself. Alternatively, ask your LLM to install the parts you want.

## License

No license. tstack's MIT notice stays with the skills derived from it (`LICENSE-tstack` in `agent-skills/review-loop`, `agent-skills/review-roll` and `slop/slop-skills/dual-agent`).
