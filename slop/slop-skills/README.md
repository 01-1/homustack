# Slop skills

Rougher, less-tested working-practice skills for running coding agents, mostly in T3 Code with Claude. They came from a multi-agent project and have been rewritten so they name no roles, people, project paths or dates. Each skill reads the project's own `AGENTS.md` and role files for anything project-specific.

The main skills (review-loop, review-roll, retire-role, ai-debate, stop-all-threads, settle-all-threads) are in `../../agent-skills`. Where a skill here uses a reviewer or a cross-check, the default is **a fresh Claude Opus agent that didn't write the work** (a new agent, even when Opus wrote it). A different model is an optional extra the human can ask for. The skills use Claude models only unless the human names another provider. Model IDs are never hardcoded: in T3 Code the skills resolve the latest versions with `orchestrator_capabilities`.

## The skills

| Skill | Verdict | What it does |
|---|---|---|
| intent-reviewer | general | Checks finished or planned work against what the human meant: missed, overreach, leftovers, conflicts. |
| record-standing-rule | generalised | Saves a standing rule into the project's AGENTS.md or a role file, replacing rather than stacking. |
| coordinator-handoff | generalised | Moves a long-running role to a fresh thread through a handoff file. |
| draft-for-review | generalised | Drafts messages to outside people for the human to approve; never sends unapproved. |
| now-list | generalised | Ranks what the human should do now: blockers, real deadlines, lead time, then value. |
| live-system-change | generalised | Changes a live system (release, active version, live parameter, caps) safely, with restatement, approval rules and confirmation. |
| research-digest | generalised | Researches a topic with subagents and writes a digest with a provenance label on every claim. |
| dual-agent | generalised | Runs one brief on two independent fresh agents (two Claude Opus by default) in parallel and merges the answers. |
| babysit | generalised | Keeps a coordinator's follow-through honest; includes an autoreviewer brief. |
| making-faster | generalised | Helps one named agent finish faster without taking its work. |
| ping-owning-thread | generalised (T3 Code) | Asks the owning thread for state instead of reading its transcript. |
| encrypted-backup | generalised | Confirms backup coverage before destructive changes, and verifies backups honestly. |

## Related user-level skills

- `council` runs three providers in parallel for review with scores; **dual-agent** runs two independent agents (Opus by default) on the same task and merges. Note that council asks for three different providers, which conflicts with using only Claude.
- `babysit-pr` follows a PR through review and CI; **babysit** follows local work, runs and coordinators, and hands PRs to babysit-pr.
- `auto0` attaches T3 autoreviewers to a general task; **babysit**'s autoreviewer brief can be used with it. auto0's and the `autoreview` tool's default reviewer is Codex Sol, so pass a Claude Opus `target` (babysit's brief says so).

## Install

A skill is a folder with a `SKILL.md`. Claude Code loads user-level skills from `~/.claude/skills/<name>/SKILL.md` and project skills from `<project>/.claude/skills/<name>/SKILL.md`. Copy whole folders (keep `LICENSE-tstack` with dual-agent; it derives from an MIT-licensed tstack skill).

The skills point at each other by name, so install them together: research-digest uses record-standing-rule; coordinator-handoff points at retire-role, and dual-agent and babysit at review-loop (both in `../../agent-skills`); babysit hands PRs to the user-level babysit-pr; making-faster points at ping-owning-thread in T3 Code. Several skills in `../../agent-skills` point at record-standing-rule, coordinator-handoff, live-system-change and dual-agent here.

T3 Code only: ping-owning-thread and babysit's autoreviewer brief. dual-agent prefers T3's `delegate_task` but falls back to the harness's own subagent tool.

### Copy commands for user level

Run from this folder (`slop/slop-skills/`). `cp -rn` never overwrites a skill you already have; check for name clashes first.

```bash
mkdir -p ~/.claude/skills
cp -rn intent-reviewer ~/.claude/skills/
cp -rn record-standing-rule ~/.claude/skills/
cp -rn coordinator-handoff ~/.claude/skills/
cp -rn draft-for-review ~/.claude/skills/
cp -rn now-list ~/.claude/skills/
cp -rn live-system-change ~/.claude/skills/
cp -rn research-digest ~/.claude/skills/
cp -rn dual-agent ~/.claude/skills/
cp -rn babysit ~/.claude/skills/
cp -rn making-faster ~/.claude/skills/
cp -rn ping-owning-thread ~/.claude/skills/
cp -rn encrypted-backup ~/.claude/skills/
```

For one project only, copy into `<project>/.claude/skills/` instead of `~/.claude/skills/`.
