# homustack

Also see: https://git.meowc.at/vi/config (Contains global AGENTS.md/CLAUDE.md files, among other things.)

Rules, skills and small tools for running many coding agents at once, mostly Claude Code and Codex inside [T3 Code](https://github.com/pingdotgg/t3code). The scripts run on Linux with Python 3 and use its standard library. The T3 helpers need the `t3` CLI and a running T3 Code instance.

`slop/` holds rougher, less-tested extras that I do not recommend using.

## Folders

**`agent-skills/`** contains skills for reviewing changes, retiring roles, running model debates, and stopping or settling T3 threads. It also includes `babysit-self`, which schedules the owning T3 thread to carry out follow-ups. See the [skill list and install steps](agent-skills/README.md).

**`helper-daemons/`** contains rules for scripts that message agent threads, a Python library that implements them, and an example inbox relay.

**`killall/`** contains `agents-killall` and `ka`, an emergency stop for configured agents, launchers, and systemd user services. It defaults to a dry run. Follow the [setup instructions](killall/README.md#install).

**`spawn-cap/`** contains a Claude Code and Codex hook that caps running agents under one thread (10 by default) and `spawn-cap tree` to see who started whom. See the [hook entries](spawn-cap/README.md) to install it.

**`usage-meters/`** contains meters and alerts for Claude and Codex subscriptions served through [CLIProxyAPI](https://github.com/router-for-me/CLIProxyAPI). See [the tools and their requirements](usage-meters/README.md).

**`effort-and-prompt-cache.md`** explains how changing a running thread's model or effort affects the prompt cache.

**`slop/LESSONS.md`** and **`slop/memories/`** contain guidance for coordinating agents and example Claude Code memories.

**`slop/slop-skills/`** contains [additional skills](slop/slop-skills/README.md), including dependencies for some of the main skills.

**`slop/fix-ledger/`** keeps requested fixes from going missing. It restores missing entries and reopens process fixes that lack a working trigger and receipt. See [setup](slop/fix-ledger/README.md#install--uninstall).

## Install

Clone the repo and follow the README for the tool or skill you want.

## License

No license. tstack's MIT notice stays with the skills derived from it (`LICENSE-tstack` in `agent-skills/review-loop`, `agent-skills/review-roll`, `agent-skills/og-sol-loop` and `slop/slop-skills/dual-agent`).
