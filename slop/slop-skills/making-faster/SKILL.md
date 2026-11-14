---
name: making-faster
description: Help one named agent finish its current task faster, including clearing what blocks it, without taking its work over. Use when the human says "make <agent> faster", "speed up <agent>", "help <agent> finish" or "making faster", or when an owner or authorized automation assigns you to speed up a specific agent. For a stuck agent that needs a fix rather than help, fix the blocker or tell its owner instead.
---

# Making faster

You help one target agent finish the task it already has. The target keeps the task, its files and its decisions.

1. **Start from the target's real state.** Get its current goal, deadline and latest reported state by asking it or its owner (in T3 Code, the ping-owning-thread skill) rather than reading its transcript, and check the clock (`date`). If no target or task is named, ask once which agent is meant.
2. **Find one narrow contribution** that saves the target time without duplicating what it is already doing: a lookup, a check of live state, a test run, a missing fact, a prepared command, or a blocker you can clear. Prefer the one that saves the most time before its deadline.
3. **Stay out of its files.** Work read-only, or on separate paths the target explicitly hands you. Never edit a file the target owns unless it hands over that exact path.
4. **Keep its limits.** Respect the target's approvals, usage limits, project boundaries and runtime constraints. Helping grants no release, spending, provider or cross-project authority.
5. **Hand back a concrete result**: the answer, the shortcut or the exact command, sent to the target in one message, with what you verified and what you did not. Then stop.
6. **No standing job.** Don't start subagents, install a monitor, take on new task intake or keep watching the target after handing back. Run again only when someone assigns you again.
