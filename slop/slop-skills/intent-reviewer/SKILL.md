---
name: intent-reviewer
description: Check that an agent's finished or planned work matches what the human actually meant, not just their literal words, and fix or flag any gap. Use when the human says "intent reviewer", "intent review", "is this what I meant" or "check intent", before reporting a change made from a short or ambiguous message, and before any change that deletes, renames, merges or duplicates files or roles.
---

# Intent reviewer

1. Collect the human's exact words, with time and sender, and the discussion just before them. Read short messages against that discussion. A single "yes" after several questions answers all of them only if each question was a plain yes/no; otherwise say which ones you took it to answer.
2. Write the intent in one sentence: the outcome the human wants, not the steps. Then list everything that was actually done or is planned: files created, edited, renamed, deleted or committed, and messages sent.
3. Have a fresh Claude Opus subagent that had no part in the work compare the two (a new agent, even when Opus did the work). Use a different model as well only if the human asks. Give it only the human's words, the context and the list from step 2, never your own conclusion. Ask it for each gap of these kinds:
	1. Missed: part of the intent that wasn't done.
	2. Overreach: something done that wasn't asked for, such as an extra edit, a broader approval or a deleted file.
	3. Leftover: a duplicate, an old copy, a stale name or a reference that the change should have removed or updated.
	4. Conflict: the result contradicts the project's AGENTS.md, a role file or an earlier instruction from the same human.
4. Check every gap it returns against the files yourself before acting on it.
5. Fix a confirmed gap yourself when it is inside what the human already approved, and make it look as though the mistake never happened. Anything else, such as a deletion, an approval or a choice between two readings, becomes one short question to the human, with your recommended answer first.
6. Report in at most five lines: the intent sentence, what matched, what you fixed, and any question. Don't report a gap you couldn't confirm.
