---
name: dual-agent
description: Run one task on two independent fresh agents in parallel, then merge their results into one answer. Use when the human says "dual agent", "both models" or "cross-check". To have a fresh agent review a change you made, use review-loop (`../../../agent-skills/review-loop`) instead.
---

# dual-agent

The same brief goes to two fresh children that work without seeing each other; you merge what they return.

## Rules

1. **Authorization.** Read the project's AGENTS.md, your role file if you have one, and the human's words first, and follow them. Never publish, post outward, contact people or spend outside what the human approved. Anything not granted is not authorized.
2. **Agents.** Start only the two children. If you are yourself a subagent, start them only if your parent allows it. The children start no agents.
3. **Models.** Default: two fresh Claude Opus agents at high effort, neither of which wrote the work in question. A different model is an optional extra: when the human names two models or says "both models", the second child runs on the other model (for example Claude Fable). Use the effort the human names, and Claude models unless the human names another provider. In T3 Code, call `orchestrator_capabilities` once per thread before the first `delegate_task` and use the latest versions it returns; never hardcode an ID. If a model or effort is unavailable, say so; never substitute. Record provider, model and effort only as returned.
4. **Routing.** In T3 Code use `delegate_task` only; never `create_threads` or `t3_thread_launch`. Follow `next`, then end your turn with one waiting line; T3 wakes you. Don't poll. Elsewhere, use the harness's subagent tool and run both at once.
5. **Evidence.** A claim without a source (file:line, output, link) is unverified, and the report says so. A timeout, error, crash, no reply or partial answer is not a result.

## Steps

1. Write one self-contained brief: goal, facts already established (to verify, not repeat), constraints (read-only unless granted, no releases, no sign-ins, no purchases), and report format (length cap, sources, unverified items marked).
2. Start both in the same turn with the identical brief (in T3, `delegate_task` with `mode: "async"`). In T3, give the human both thread links in one line, then end the turn.
3. When both report, merge: agreement → confirmed; conflict → both claims with sources, check material disagreements yourself within the real deadline and label anything unresolved; one only → single-source. Give one recommendation, not two.
