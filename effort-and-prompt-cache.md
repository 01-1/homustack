# Effort changes and the prompt cache

Measured 2026-11-09 on the CLI route, not inside T3 Code.

- **Claude:** changing effort between turns kept cache reuse (about 70k cached tokens read on the changed turns, for both Fable and Opus).
- **Codex:** changing effort cut cached input from about 37k to about 9k tokens on that turn.
- **Model switches** were not measured, but the Claude Code docs describe caches as per model.

**Do instead:** pick the model and effort when a thread starts, and don't change them on a running thread.
