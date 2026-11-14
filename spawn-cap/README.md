# spawn-cap
An automated check that stops agent threads from spawning too many subagents or agents, plus a tree graph of who started whom. Install it as a PreToolUse hook for Claude Code and Codex.

- Tree: parent->child spawn tree from T3 statev2.sqlite (lineage.parentThreadId, subagents) + a launches ledger (~/.local/state/spawn-cap/edges.json; t3_thread_launch has no lineage parent). `spawn-cap tree` prints it as JSON, with each node's status (running/idle) and subtree_running.
- Running: a thread with runs counts while it has an active run; a native Claude/Codex subagent (no runs) counts while its subagent row is active; a Claude workflow container is not counted; deleted threads never count.
- Check: PreToolUse hook in ~/.claude/settings.json and ~/.codex/hooks.json runs `spawn-cap hook`; exits 2 (blocks) when the call would put more than the limit of agents running at once under any thread (the limit is per subtree, not per thread):
  - spawns (Agent, Task, spawn_agent, delegate_task, t3_thread_launch, create_threads, run_scheduled_task_now on an unbound task): checked against the caller and each ancestor;
  - starting an existing idle agent or thread (t3_thread_send, t3_thread_send_attachments, run_scheduled_task_now, Claude SendMessage, Codex send_input, resume_agent, followup_task): checked against the target's ancestors.
  - Never blocked: a message to an agent that is already running, a hand-back to an idle ancestor of the caller, SendMessage with only notify_when_idle, targets on another machine or that cannot be resolved. Fails open.
- Default: 10 running at once per subtree (the subtree's root not counted). Change in ~/.config/spawn-cap/config.json {"limit":N,"exempt":[ids]}.
- Exempt: thread ids in `"exempt"` and threads whose title starts with one of `"exempt_title_prefixes"` (for example a recovery agent), and everything called from inside their subtrees. None by default.
- Override (human-only): `spawn-cap allow <thread-id|all> [extra] [minutes]`.
- Blocks log: ~/.local/state/spawn-cap/blocks.jsonl (wake blocks carry action "start-existing" and target). `spawn-cap status` shows running counts per subtree.
- Tests: `python3 test_spawn_cap.py` (fixture database; never touches live state).
- Install: put the script on your PATH as `spawn-cap` (for example `ln -s "$PWD/spawn_cap.py" ~/.local/bin/spawn-cap`), then add the hook to `~/.claude/settings.json` (and the same `hooks` block to `~/.codex/hooks.json`). PostToolUse records launch edges that T3 itself doesn't store:

```json
"hooks": {
  "PreToolUse": [{"matcher": "Agent|Task|spawn_agent|SendMessage|send_input|resume_agent|followup_task|mcp__.*__(delegate_task|t3_thread_launch|create_threads|t3_thread_send|t3_thread_send_attachments|run_scheduled_task_now)|.*\\.(delegate_task|t3_thread_launch|create_threads|t3_thread_send|t3_thread_send_attachments|run_scheduled_task_now)",
                  "hooks": [{"type": "command", "command": "spawn-cap hook", "timeout": 10}]}],
  "PostToolUse": [{"matcher": "Agent|Task|spawn_agent|mcp__.*__delegate_task|mcp__.*__t3_thread_launch|mcp__.*__create_threads",
                   "hooks": [{"type": "command", "command": "spawn-cap hook", "timeout": 10}]}]
}
```

- Needs: Python 3, standard library only; T3 Code's database at `~/.t3/userdata/statev2.sqlite` (without it the hook fails open).
- Uninstall: remove the `spawn-cap hook` PreToolUse and PostToolUse entries from ~/.claude/settings.json and ~/.codex/hooks.json.
