---
name: babysit-self
description: Set up a recurring T3 automation in the owning thread when asked to babysit ongoing work and carry out follow-ups itself. Use babysit-pr for pull request review and CI.
---

# Babysit the work yourself

Use the current conversation's goal, plan, spending limits and completion condition. The owning thread acts on results as soon as they arrive. The schedule catches missed follow-ups between events.

1. At setup, use `list_scheduled_tasks` to look for an automation for the same goal and thread. Reuse its ID when updating it. Use `schedule_task` with `bindToCurrentThread: true` and `freshSession: false`. Follow the requested cadence, defaulting to a 15-minute interval. Put the actual goal, plan location, authorized scope and stopping condition in the prompt. Confirm the returned thread binding, enabled state and next wake time.
2. On a result notification or scheduled wake, read the latest human steering and the evidence for the outstanding decision. Reconcile completed work with the plan. Check existing job IDs and receipts before launching a follow-up so repeated wakes are safe. Let T3 deliver completion notifications for running delegates.
3. When a result warrants an authorized action, perform it yourself on that turn. Implement, launch, recover or verify the next step and record what happened. A plan edit or reminder alone leaves the action open. Keep existing workers on their assigned work. Use the agreed permissions and budget for any new work.
4. Report material results, actions or blockers. Let uneventful wakes pass quietly. When the goal and its authorized follow-ups are complete, disable this automation by its own scheduled task ID and report the outcome. Honor a human request to stop immediately.

When only asked to create the automation, register it and report its next wake. Its scheduled turns execute the work. If scheduling is unavailable, say so and distinguish what the current turn can do from what would require a future wake.
