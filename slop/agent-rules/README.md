# Agent rules

General rules for working with coding agents (Claude in T3 Code and Claude Code), the lessons from running dozens of agents on one project, and example Claude Code memories.

| File | What it is |
|---|---|
| `AGENTS.md` | The rule file: short, grouped, no project history. Drop it into any project. |
| `LESSONS.md` | 19 lessons from the multi-agent project, each with what to do instead. Reference only; agents don't need it. |
| `memories/` | Three example Claude Code memories and their `MEMORY.md` index. |
| `memories/INDEX.md` | Where each memory's rule lives in `AGENTS.md`. |

`AGENTS.md` names seven skills: intent-reviewer, record-standing-rule, draft-for-review, live-system-change, encrypted-backup and ping-owning-thread from `../slop-skills`, and stop-all-threads from `../../agent-skills`. Install those too (see each folder's README), or the pointers lead nowhere.

## What AGENTS.md covers

- **Reading the human:** the `My reading:` first line, reading short messages in context, two readings plus a live change means wait, intent-reviewer before risky or ambiguous work.
- **Doing the work:** be agentic, finish contained steps, one command when one will do, git after 3 changes, time estimates, "done" means triggered automatically and seen working.
- **Ask first:** destructive, outward-facing, live-system, money and sign-in actions.
- **Accuracy and corrections:** verify before agreeing, fix artifacts without a correction note, no claim without evidence, never run a real command to test its guard.
- **Rules and memory:** rules live in the repo, replace instead of stacking, running agents need a message.
- **Subagents and models:** dele/root/loop words, worktrees for parallel editors, Claude models only (Fable for intelligence, Opus for talking to the human), a fresh Claude Opus agent reviews by default (a different model only as an extra), rough time per task by effort, pick model and effort at thread start (prompt cache), stable request ids for thread starts, Claude subscription only through Claude Code.
- **Messaging other agents (T3 Code):** ask the owner, steer in auto mode, `completed` is idle, stopping one agent or a whole project, the false "user declined" bug, routers forward exact words, scripts do relaying.
- **Helper scripts:** the rules for small long-running daemons (one lock, atomic state saved after a send, retries, logging, restart loop, `--dry-run`). `../../helper-daemons` has a library that implements them.
- **Reaching the human:** one channel kept for the whole project, only important items, one item per matter, never block.
- **Replies:** code blocks only for code; paths as clickable links.

Check before use: the rule "in T3 Code, start subagents with `delegate_task`, not the harness's native subagent tool" is a preference from the project. T3 Code's own current guidance prefers the native tool when the child uses the same provider and settings, so decide which one should win before installing.

## Install

Run these from this folder (`slop/agent-rules/`).

### In one project

Claude Code reads `CLAUDE.md`, not `AGENTS.md`, so add a one-line `CLAUDE.md` that imports it. Replace `<project>` with the project's root.

```bash
cp -n AGENTS.md <project>/AGENTS.md
[ -e <project>/CLAUDE.md ] || printf '@AGENTS.md\n' > <project>/CLAUDE.md
```

If the project already has a `CLAUDE.md`, add the line `@AGENTS.md` to it by hand instead.

### At user level (every project)

`-n` means an existing `~/.claude/CLAUDE.md` is never overwritten; if you have one, merge by hand.

```bash
cp -n AGENTS.md ~/.claude/CLAUDE.md
```

### Memories (optional)

Claude Code keeps memory per project in `~/.claude/projects/<slug>/memory/`, where the slug is the project's absolute path with every `/` replaced by `-` (for `/home/you/code/newproj`, `-home-you-code-newproj`):

```bash
M=~/.claude/projects/-home-you-code-newproj/memory
mkdir -p "$M"
cp -n memories/code-blocks-only-for-code.md memories/never-test-real-commands.md memories/ambiguous-short-message-no-action.md "$M"/
cat memories/MEMORY.md >> "$M"/MEMORY.md
```

### Skills

The skills are in `../../agent-skills` and `../slop-skills`; each README has the copy commands.
