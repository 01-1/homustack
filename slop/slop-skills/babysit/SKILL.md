---
name: babysit
description: Follow a local task or timed run through completion when asked to babysit it, or have an autoreviewer watch a coordinator for forgotten follow-through. Use when the human says "babysit" about local work, a run or a coordinator thread. For a pull request through review and CI, use babysit-pr instead.
---

# Babysit

Track follow-through within the assigned goal. The coordinator acts; the
autoreviewer flags omissions. Reuse existing reviews, gates and task state; do
not become another correctness reviewer or orchestrator. For PRs and CI, use
babysit-pr.

## Coordinator mode

Read the project's AGENTS.md and the task's current state. Record open
obligations there: owner, opened-at time, expected return or deadline, next
action and closure evidence. Cover children, findings, promises, automation and
the human's decisions; do not sweep unrelated TODOs into the assignment.

Act on work already actionable before yielding. T3 Code wakes the parent when
a `delegate_task` child finishes: follow the tool's `next`. Never write a
watcher, polling or sleep loop. Only the owner arranges wakes. Use existing
deadline wakes or supported one-shot wakes for expected returns and hard
deadlines: interrupted children send no completion notice. Honour any existing
idle pause; do not add a heartbeat. Say so when supervision is unavailable.

On a late expected result, check status once. A completed child needs
integration, not a restart. If interrupted or dead, restart once with the same
brief, partial work and remaining time, within existing authorization and
usage limits. Keep the model, provider, effort and the original absolute
deadline. If still running within its timebox, follow `next`; do not poll.
Cancel obsolete or overdue T3 delegates with `task_cancel` before replacing
them. If recovery fails, record the blocker.

## Close the loop

- **Child result:** inspect and integrate accepted work, run relevant checks and
  record what you did with it. A completion notice is not acceptance. Commit
  verified changes when authorized, following the repo's commit rules; during a
  timed run, commit only where the run's rules allow (for example only the
  orchestrator, at gates).
  Draft-only tasks end with delivery for review. Never include unrelated changes.
- **Review finding:** verify against the current work. Fix real problems and
  recheck affected behaviour, or record a specific reason for declining. Keep
  work within the goal; decline nits and speculative extras. Record agreed
  follow-ups in the project's TODO or state file. Track existing autoreviewer
  and review-loop (`../../../agent-skills/review-loop`) findings; do not launch extra reviewers, and add no review or
  approval gate.
- **Promise:** produce and visibly deliver the promised result, or explicitly
  record why it was cancelled, deferred or blocked. A tool call alone is not delivery.
- **Schedule, watcher or webhook:** check target, timing, time zone and
  registration; confirm an authorized end-to-end delivery. Registration alone is
  unverified. If a safe test is unavailable, track the first real delivery at its
  due time. Keep the responsible owner and next event; do not replay outward
  actions merely to test them or leave duplicate watchers running.
- **The human's decision:** finish the independent work, prepare the exact
  choice with a recommendation, then raise it once when it matters. Keep it
  pending; silence is not consent. Re-raise only for changed facts or a real
  deadline.

## Timed runs

Use the run's own procedure, state file, clock and gates. At launch and phase
boundaries, compare each module's timebox with the phase and freeze cutoffs and
the hard stop. Arrange enforceable time limits or deadline cancellation; an
autoreviewer or wake alone cannot enforce a clock.

Track each module through its report, integration and the gate evidence that
applies to it. Use the gate commands and thresholds the run declares; if a gate
tool does not exist yet, say which gate is unverified instead of inventing a
substitute. Return failures to the owning module agent. Keep the last version
that passed its gates; promote a new one only with the run's declared gate
evidence, recorded in the run's state. Never weaken gates or promote from a
child's claim alone.

At a module's timebox, cancel unfinished work and keep partial results; do not
reset the clock on restart. At freeze, cut features; at the hard stop, stop
development and follow the authorized release or fallback procedure. Review
loops never extend the run. Rules questions go to the project's questions file;
stop the affected thread rather than guessing.

## Stop and speak

Stay silent when nothing changed. Never say "still waiting." Report concrete
results, blockers and decisions the human needs. Stop on verified closure,
cancellation, hard stop, or when remaining work needs the human or an external
change. A blocker is not completion. Before handoff, persist open items,
evidence and the next owner or event; remove obsolete wakes owned by this task.
Leave shared automation intact.

## Autoreviewer brief

Paste this brief into T3 Code's `autoreview` tool; the reviewer observes, never executes. The tool's default reviewer is a Codex model, so pass `target` with the latest Claude Opus that `orchestrator_capabilities` returns:

```text
Watch this coordinator thread for forgotten follow-through within its assigned
goal. Flag: arrived child results not integrated/checked/committed as applicable;
interrupted or overdue children; review findings visible in your batches (T3 does
not show you other autoreviewers' findings) neither acted on nor declined with a
reason; undelivered promises to the human; schedules/watchers/webhooks
registered but not confirmed working; run modules missing their applicable gates
or overrunning deadlines; decisions not raised when needed.

Use supplied thread/state evidence. Cite a tool event, file or message;
keep the first known timestamp, or say "unknown".
An active child within its timebox, future scheduled delivery or already-raised
decision is not itself a failure. Do not infer failure from silence alone.
Do not duplicate correctness, intent, delegation-quality or priority reviews.
Do not act, edit, commit, restart, schedule, message the human, poll or start agents.
Only the coordinator closes the loop. Never require another review round.

For each new or materially changed actionable omission, output one line:
OPEN: <what, with evidence> | since <when> | next step: <one action>
Do not repeat unchanged findings or resolved/declined items. Pending human decisions
stay pending without nagging. Output nothing when there is no new actionable gap.
```
