---
name: never-test-real-commands
description: "Never invoke a real-action command (stop-all, killall, other stops) to \"test\" its refusal path"
metadata:
  type: feedback
---

Never run a command that performs a real action just to check that it refuses. An agent once ran a stop-everything command to show that it refused other callers, but that agent was the allowed caller, so it really stopped every agent turn, disabled schedules and usage routes, and everything had to be restored by hand.

**Why:** the guard you are "testing" may pass for you; the action is then real and hits live agents.

**How to apply:** check guards by sourcing or calling only the guard function, or with `--dry-run`; never execute the wrapper itself.
