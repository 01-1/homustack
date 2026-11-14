---
name: coordinator-handoff
description: Hand a coordinator's or any long-running role's thread over to a fresh top-level thread without losing state. Use when the human says "handoff", "hand everything to a new agent", "write a handoff" or "new <role>", or when a thread's context is getting full and the human agrees to move on. To split a role across several owners, use retire-role (`../../../agent-skills/retire-role`) instead.
---

# Coordinator handoff

A handoff moves the work into a file first and into a new thread second, so nothing lives only in chat.

1. Write the handoff file at `docs/handoff-<role>-<date>.md` in the project. Commit it (or leave it to the repo's committer if one runs). No secrets: say where they live instead.
2. Put these in the file, in this order:
	1. New facts that override the project's AGENTS.md or the role file, each marked as the human's decision (with who said it and when) or as observed.
	2. State: what is done and verified, what is running and who owns it, and what is unverified. During a timed run, also include the clock against the run's start, the live version, pending releases and open approvals.
	3. Open items in priority order, real deadlines first, each saying who it waits on.
	4. Standing rules, as pointers to AGENTS.md and the role file, not restated.
	5. Don'ts: things the human already decided against, so they aren't proposed again.
	6. Read first: the exact file paths.
3. Start the new thread as a top-level thread, keeping the role's model and effort unless the human said otherwise, with the same working directory and the role's name as its title. Its prompt: "You are <name>. Follow the instructions in <role file>. HANDOFF from <old thread>: read <handoff file>, acknowledge briefly, then continue." Tell it to wait only if the human asked for a paused handoff.
4. Send any late update to the new thread as a short "HANDOFF UPDATE" message, and add it to the file too.
5. If the project keeps a registry of agent threads (for example a `threads.tsv` of `<name><TAB><threadId>` lines that scripts read), replace the role's line with the new thread id, and tell whoever routes messages the new owner, so messages stop going to the old thread.
6. Reply in one line: the new thread's link and the file path. The old thread takes no more work.

If the human says "write the handoff and nothing else", write only the file and reply with its path.
