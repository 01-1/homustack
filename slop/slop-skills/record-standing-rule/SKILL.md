---
name: record-standing-rule
description: Save a new standing rule, preference or decision from the human into the project's AGENTS.md or the matching role file, so every agent follows it. Use when the human says "put in agents.md", "save this", "remember", "new rule" or "I said this before", or states a preference or correction meant beyond the current task.
---

# Record a standing rule

Rules live in the repo, where every agent reads them, never in one tool's private memory.

1. Pick the file:
	1. The project's `AGENTS.md`: rules for every agent.
	2. A role file (for example `roles/<role>.md`): a rule for one role.
	3. The project's docs folder: ordinary facts and notes, with their source.
	4. One-off tasks are not rules: send them to the agent that owns the work, or to whoever routes requests if you don't know who.
2. Write the rule in plain words that keep the human's exact meaning, for example "probably not allowed" or "supervised, not automated", with the reason if they gave one. Put who said it and when in the commit message, not as a dated quote in the rule file (AGENTS.md: replace, don't stack). Mark any interpretation of your own as yours. No secrets or thread links. If the meaning is unclear, ask once first.
3. Replace, don't stack. Search for lines on the same topic (`rg -n`) and update or remove anything the new rule contradicts, so each rule lives in one place. Keep unrelated edits.
4. Keep rules that come from an outside source (a contest's rules, a platform's terms, a client's spec) faithful to that source. The project's own operating rules are a separate thing; don't mix the two.
5. If an agent or script owns commits in this repo, leave the commit to it; otherwise commit the change yourself.
6. Confirm in one or two lines, quoting the new text and the file. If you broke the rule earlier in the conversation, say what you did, once.
7. Apply it now: fix ongoing work it affects in the same turn, and send the change to the running agents it applies to. A running agent read its rule files when it started, so a change reaches it only by message. A written rule isn't an implemented one; hand any automation it needs to its owner.
