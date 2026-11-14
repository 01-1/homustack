---
name: stop-all-threads
description: Stop every running thread in one or more T3 Code projects, including subagents and folder-filed threads, using your own T3 MCP tools. Only stops, never settles. Use when the human says "stop all threads", "stop everything", "kill the run", "stop all threads in <project> and <project>" or "stop threads in every project". Defaults to this thread's own project; also takes several named projects, or every project when told "all projects" or "everywhere". Never touches your own thread or projects the human didn't name, and never archives. To settle threads instead, use settle-all-threads.
---

# Stop All Threads

Stop every active thread in the chosen T3 Code projects from this thread's own T3 MCP tools. This skill only stops threads; it does not settle them. If the human also wants the threads settled, finish this skill first, then follow the settle-all-threads skill.

Do the stop with your own tools, not from a script: T3 caps a script's token session at approval-required, and agent threads usually run full-access, so a script's interrupt calls fail with `runtime_mode_escalation_denied`. A script can still list threads and their status, because listing is allowed.

## Load the tools

Call ToolSearch with a select query naming these T3 tools, each with the `mcp__t3-code__` prefix: t3_project_list, t3_thread_list, t3_thread_folder, t3_thread_read, t3_thread_interrupt and task_cancel. The tools below are named without the prefix.

## Choose the projects

There are three cases.

- Nothing named: only this thread's own project. Read your own thread (the `T3CODE_THREAD_ID` environment variable) with t3_thread_read and use its projectId.
- Several projects named, such as "stop all threads in Alpha and Beta": call t3_project_list (page with cursor until there is no next cursor) and resolve each name by exact title or workspace root, or by an obvious unique match. If a name fits more than one project, or none, ask before going on.
- "In every project", "all projects" or "everywhere": every project t3_project_list returns, across all pages.

The project set is exactly the projects the human named, or every project when the human said so. Never act on a project outside that set.

Note your own thread id (`T3CODE_THREAD_ID`) and leave that thread alone throughout, even when stopping every project. It is the only thread ever skipped: every other thread in a project in the set gets stopped, including the thread that asked.

## List every thread

Do this for each project in the set. Call t3_thread_list with projectId set to the project id, includeSubagents true, limit 100 and cursor 0, then call it again with cursor set to each returned nextCursor until nextCursor is missing or repeats. Don't pass settled or statuses, so settled and unsettled threads in every status come back. For every-project stops, t3_thread_list with allProjects true can replace the per-project calls when that is simpler; still page to the end and group the results by projectId.

Archived threads can be missing from that list. Call t3_thread_folder with action list; for every id in each folder's threadIds that the list didn't already return, call t3_thread_read on it with a small limit and keep it only if its projectId is in the project set.

Drop any thread whose projectId isn't in the project set, and your own thread. This is the working set, grouped by project.

## Confirm before a wide stop

Before interrupting anything, if more than a few projects are involved (as is usual for every-project stops), tell the human which projects you are about to stop and how many active threads each has, and ask for one confirmation. Wait for it. For one project or a short named list, go straight on.

## Stop the background scripts

Agents start long-running scripts that keep sending messages, and every message wakes a stopped thread again. So before interrupting, stop them for each project in the set. Take the project's workspace root and find the processes whose command line contains its scripts folder (by default `<workspace root>/scripts/`), with `ps -eo pid,args`. Kill the restart loops first (the `bash -c while true; do python3 <workspace root>/scripts/...` processes), then the scripts themselves, by pid with `kill`. Never use `pkill -f` with a pattern that also matches your own shell command. Check again with `ps` a few seconds later; anything left gets `kill -9`. Touch no process whose command line doesn't contain that path. Note what each script was: a script that manages cloud machines may have left them running, which this skill doesn't touch; mention it in the report.

## Interrupt active threads

A thread is active when its status is preparing, queued, starting, running or waiting. For each active thread, call t3_thread_interrupt with threadId, stop true and a short reason such as "Stopping all threads in this project at the human's request." Interrupt parents before their subagents, because a parent may start a new subagent when one stops; then interrupt the subagents too.

If the interrupt fails and the threadId starts with `thread:delegated-task:`, the thread belongs to a delegated task. Take the rest of the id after that prefix, URL-decode it, and call task_cancel with that as taskId and the same reason. Record the thread as failed only if both calls fail.

## Re-list until nothing is active

Repeat the listing step for every project in the set. Interrupt anything still or newly active the same way, and check `ps` again for any script that came back. Keep going, a few seconds apart, until no thread in the working set is active, or a thread has stayed active for about 90 seconds after its stop; record that one as failed and move on.

Do not settle anything. Settling is the settle-all-threads skill's job.

## Rules

- Only the projects the human named, or every project when the human said so, matched by projectId on every thread you touch. Never act on a thread you couldn't confirm belongs to a project in the set.
- Never interrupt or cancel your own thread. Every other thread in a project in the set is stopped.
- Never settle, archive, delete or move threads.
- Kill only processes under the set's workspace roots' scripts folders.

## Report

For each project, give its name and id, then counts: threads found (including how many came only from folders), active at the start, interrupted, cancelled with task_cancel, skipped (your own thread) and failed, plus the background scripts killed. When more than one project was involved, also give the same counts in total. List each failure with its project, thread id, title and the error. End by noting that the threads were stopped but not settled, and that settle-all-threads will settle them if wanted.
