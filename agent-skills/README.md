# Agent skills

Eight working-practice skills for running coding agents, mostly in T3 Code with Claude. They name no roles, people, project paths or dates; each reads the project's own `AGENTS.md` and role files for anything project-specific. Rougher, less-tested skills are in `../slop/slop-skills`.

Where a skill uses a reviewer or a debater, the default is a fresh agent that didn't write the work. review-loop and review-roll default to a fresh Sol reviewer, and any model the human names replaces it. ai-debate uses Claude models unless the human names another provider. Model IDs are never hardcoded: in T3 Code the skills resolve the latest versions with `orchestrator_capabilities`.

| Skill | What it does |
|---|---|
| babysit-self | Sets a recurring T3 wake in the owning thread to execute authorized follow-ups and disable itself when the goal is complete. |
| review-loop | A fresh reviewer (Sol by default; any model the user names, as in "sol loop", "opus loop") checks a change in bounded rounds until agreement. Works on PRs, local commits, uncommitted work and files outside git. |
| review-roll | Publishes first, then a fresh background reviewer (Sol by default) checks a snapshot, and its fixes go out as follow-up releases. |
| retire-role | Splits a role across at least two owners, none taking more than 50% of its load, then retires it. |
| ai-debate | Debaters answer alone, rebut each other once, and the result lists agreement and open disputes. |
| stop-all-threads | Stops every running thread and the project's background scripts in the named T3 projects. |
| settle-all-threads | Settles every thread in the named T3 projects without interrupting anything. |
| og-sol-loop | tstack's `sol-loop` as published, triggered by "og sol loop", for comparing against review-loop. |

review-loop is tstack's `sol-loop` (`skills/sol-loop/SKILL.md` in t3dotgg/tstack) in its own wording, generalised from pull requests to any change and with Sol as the default rather than the only reviewer. review-roll is review-loop's wording with publish-first steps. og-sol-loop is tstack's `sol-loop` unchanged except for the frontmatter `name` and `description`. All three keep tstack's MIT notice in their `LICENSE-tstack`; keep that file with them when you copy them. If you have a user-level `sol-loop` skill, review-loop replaces it: move sol-loop out of `~/.claude/skills` (don't delete it) and point any skills that name it at review-loop.

## Dependencies between skills

Some of these name skills that live in `../slop/slop-skills`: retire-role uses record-standing-rule and points at coordinator-handoff; review-roll hands live releases to live-system-change; review-loop and ai-debate point at dual-agent. Install those too if you want the pointers to lead somewhere. stop-all-threads and settle-all-threads point at each other.

T3 Code only: stop-all-threads and settle-all-threads (a script's T3 token can't interrupt or settle threads, so they run from an agent's own T3 tools). review-loop, review-roll and ai-debate prefer T3's `delegate_task` but fall back to the harness's own subagent tool.

## Install

A skill is a folder with a `SKILL.md`. Claude Code loads user-level skills from `~/.claude/skills/<name>/SKILL.md` and project skills from `<project>/.claude/skills/<name>/SKILL.md`. Run from this folder (`agent-skills/`); check for name clashes in `~/.claude/skills` first.

```bash
mkdir -p ~/.claude/skills
cp -rn babysit-self review-loop review-roll retire-role ai-debate stop-all-threads settle-all-threads og-sol-loop ~/.claude/skills/
# the skills they point at, from slop/
cp -rn ../slop/slop-skills/{record-standing-rule,coordinator-handoff,live-system-change,dual-agent} ~/.claude/skills/
```

For one project only, copy into `<project>/.claude/skills/` instead.
