---
name: retire-role
description: Split a role's responsibilities across existing owners that explicitly accept and new successor threads, so no receiving thread gets more than 50% of the original load, then retire the role. Use when the human asks to split up or retire a role. For moving one role to a fresh thread, use coordinator-handoff (`../../slop/slop-skills/coordinator-handoff`) instead.
---

# Split up and retire a role

The point is to split, so no thread is overloaded. Handing everything to one replacement is not a retirement; it moves the overload. A retirement both offloads to existing owners and creates new successors where needed.

## The 50% cap

1. No receiving thread, whether an existing owner or a new successor, gets more than 50% of the retired role's original load.
2. Estimate each responsibility's share from its actual recent load: turns, tool and run volume, standing duties, timers and open work. A receiving owner's existing load counts too.
3. That means at least two receiving threads, and more if needed. Split a responsibility by sub-scope if it doesn't fit. If it truly can't be split, or nobody can take a part, record it as UNASSIGNED rather than overloading someone.
4. An earlier retirement that broke the cap gets corrected under this skill when the human asks.

## Steps

1. Read the project's AGENTS.md, the role file and the target thread. Record the human's exact request, who said it and when, and the target's thread id.
2. Inventory the role: standing duties, unfinished tasks, running work and delegates, timers, routes that send it messages, pending approvals and blockers. A matching title or an old send receipt doesn't make someone the current owner.
3. Write `docs/retire-<role>-<date>.md` with this table, followed by each receiving thread's total share (50% or less each):

| Responsibility | Share of load | Proposed owner (existing or new) | Accepted or declined | State kept and next step |
|---|---|---|---|---|

4. Offer each part to a current owner once, with its exact scope and an ACCEPT or DECLINE ask. A part counts as accepted only when the owner's reply says so.
5. For parts with no owner, start successor threads yourself (in T3 Code, `t3_thread_launch`). Don't ask the human to do it. Give each a neutral title, a stable request id so a retry never makes a duplicate, and a prompt with: the authorization and its source, the exact scope with an ACCEPT or DECLINE ask, the open work and next step, the role file and rules to follow, and "reply to <you> with the accepted scope and your thread link". Use the role's model and effort, or the human's choice. No secrets. If a successor declines, reassess; don't chain replacements without limit.
6. While retiring:
	1. Stop only the retired role's own timers, and record which ones.
	2. Keep running work going. Checkpoint or hand it off; don't cancel or restart it just to tidy the table.
	3. Tell whoever routes messages the new owners once, so new messages go to them. Messages already sent stay where they are. If a helper script sends to the retired role, tell the script's owner, and update the project's thread registry only for a successor that takes the role's whole name.
	4. Delete no history.
7. Record the ownership change in the role files and AGENTS.md with the record-standing-rule skill (`../../slop/slop-skills/record-standing-rule`). If the role file itself should go, that is a file deletion and needs the human's OK.
8. Report: accepted parts with their thread links, shares per thread, UNASSIGNED gaps and who owns the next decision. Keep "accepted" separate from "done". The retired role takes no new work, timers or wakes.
