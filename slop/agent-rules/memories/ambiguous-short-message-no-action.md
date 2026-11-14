---
name: ambiguous-short-message-no-action
description: "When a short message has two readings and acting changes something running, state both and wait; after \"stop\", restart nothing without an explicit \"go\""
metadata:
  type: feedback
---

When a short message from the user could mean two things AND acting on it changes something running or hard to undo (starting or stopping agents, queueing work, freezing, resetting), state both readings in one line and do nothing until the user picks. After the user says "stop", restart nothing without an explicit "go". Reversible things (reading logs, answering) still just get done.

**Why:** during one live run a coordinating agent flipped a practice run's instructions five times in eight minutes, widened a narrow instruction to cover every agent, and read "NO!!!!!" as "start" while the user was still trying to stop it.

**How to apply:** applies to live control of running work and other hard-to-undo actions; it sharpens the AGENTS.md rule on reading short messages in context (two plausible meanings: state both, ask once). An urgent notice that must reach an agent within seconds goes in restart mode, because a steer waits for the agent's current tool call to finish.
