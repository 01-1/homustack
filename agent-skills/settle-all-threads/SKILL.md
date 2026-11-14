---
name: settle-all-threads
description: Settle every thread in one or more T3 Code projects that isn't already settled or archived, including subagents and folder-filed threads, using your own T3 MCP tools. Never interrupts anything. Use when the human says "settle all threads", "settle the project", "settle everything", "settle all threads in <project> and <project>" or "settle threads in every project". Defaults to this thread's own project; also takes several named projects, or every project when told "all projects" or "everywhere". Never touches your own thread or projects the human didn't name, and never archives. To stop running threads, use stop-all-threads.
---

# Settle All Threads

Settle every thread in the chosen T3 Code projects from this thread's own T3 MCP tools, without interrupting anything. Settling does not stop a running turn: a running thread stays running after it is settled. If the human wants everything stopped and filed, run the stop-all-threads skill first, then this one.

Do the settling with your own tools, not from a script: T3 caps a script's token session at approval-required, and agent threads usually run full-access, so a script's settle calls fail with `runtime_mode_escalation_denied`. A script can still list threads, because listing is allowed.

## Load the tools

Call ToolSearch with a select query naming these T3 tools, each with the `mcp__t3-code__` prefix: t3_project_list, t3_thread_list, t3_thread_folder, t3_thread_read and t3_thread_organize. The tools below are named without the prefix.

## Choose the projects

There are three cases.

- Nothing named: only this thread's own project. Read your own thread (the `T3CODE_THREAD_ID` environment variable) with t3_thread_read and use its projectId.
- Several projects named, such as "settle all threads in Alpha and Beta": call t3_project_list (page with cursor until there is no next cursor) and resolve each name by exact title or workspace root, or by an obvious unique match. If a name fits more than one project, or none, ask before going on.
- "In every project", "all projects" or "everywhere": every project t3_project_list returns, across all pages. Settling everywhere needs no extra confirmation.

The project set is exactly the projects the human named, or every project when the human said so. Never act on a project outside that set.

Note your own thread id (`T3CODE_THREAD_ID`) and leave that thread alone throughout, even when settling every project. It is the only thread ever skipped: every other thread in a project in the set gets settled.

## List every thread

Do this for each project in the set. Call t3_thread_list with projectId set to the project id, includeSubagents true, limit 100 and cursor 0, then call it again with cursor set to each returned nextCursor until nextCursor is missing or repeats. Don't pass settled or statuses, so settled and unsettled threads in every status come back. For every-project settles, t3_thread_list with allProjects true can replace the per-project calls when that is simpler; still page to the end and group the results by projectId.

Archived threads can be missing from that list. Call t3_thread_folder with action list; for every id in each folder's threadIds that the list didn't already return, call t3_thread_read on it with a small limit and keep it only if its projectId is in the project set.

Drop any thread whose projectId isn't in the project set, and drop your own thread. This is the working set, grouped by project.

## Settle

For every thread in the working set that is neither settled nor archived, call t3_thread_organize with threadId and action settle. Do not interrupt or cancel any thread, even an active one; settle it as it is. Leave settled and archived threads as they are. Never use the archive action.

List every project in the set once more to verify that everything in the working set is settled or archived, except threads recorded as failed.

## Remove the folders

Then remove the sidebar folders the settled threads were in. Call t3_thread_folder with action list. Delete (action delete, one folderId each) every folder whose threadIds are all threads you settled or found already settled or archived in this run, or threads not in any project in the set; a folder that still holds an unsettled thread from a project in the set (one that failed, or your own) stays. Never delete a catch-all folder such as `Miscellaneous`, and never a folder holding a thread from a project outside the set. Deleting a folder doesn't touch its threads.

## Rules

- Only the projects the human named, or every project when the human said so, matched by projectId on every thread you touch. Never act on a thread you couldn't confirm belongs to a project in the set.
- Never settle your own thread.
- Never interrupt or cancel threads; that is stop-all-threads.
- Never archive, delete or move threads. Deleting folders is allowed only as in "Remove the folders".

## Report

For each project, give its name and id, then counts: threads found (including how many came only from folders), settled, already settled or archived, settled while still active, skipped (your own thread) and failed, then the folders deleted and the folders kept, with why. When more than one project was involved, also give the same counts in total. List each failure with its project, thread id, title and the error. If any settled thread was still active, say so and point to stop-all-threads.
