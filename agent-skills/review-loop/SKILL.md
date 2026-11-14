---
name: review-loop
description: Have a fresh reviewer (Sol by default) check a change in bounded rounds before it ships. Use when the user says "review loop", "rev loop" or "<model> loop" (sol loop, opus loop, fable loop, astra loop and so on; the model named is the reviewer), or when a role file, skill or brief says "with review loop", "with rev loop" or "with <model> loop". To run one task on two independent agents and merge the results, use dual-agent (`../../slop/slop-skills/dual-agent`) instead. To publish first and apply the reviewer's fixes afterwards, use review-roll.
---

# review-loop

This is a workflow for making your changes more correct before (or
sometimes after) you commit, publish, release, or file a pull request.
tl;dr - use a fresh reviewer (OpenAI's GPT Sol by default) to review your
change in a loop.

Make the most minimal possible changes to appease the reviewers. Be careful
of scope creep and don't be scared to push back.

## Budget

- Default: 3 rounds total. Round 1 is full. Later rounds check only
  changed code and open findings.
- Extend to 5 only for security, permissions, auth, data loss, or
  state/concurrency, while a blocker or should stays open with evidence. Or when
  the user asks.
- Stop early if a round returns only nits. Declined nits and comment,
  doc, or format-only fixes need no new round.
- One reviewer only. The reviewer must not start subagents or reviewers.
- Keep a ledger: id, severity, outcome, usage if returned. Never guess it.

## 1. Scope

Run `git status --short`. Do not stash, discard, or include unrelated
changes. Limit review to the change paths if they separate cleanly.
Otherwise stop and ask.

Find the base (after `git fetch origin` if there is a remote):

- PR: `gh pr view <n> --json baseRefName,headRefOid`. Check out the branch
  only if clean. Confirm `HEAD` is the PR head, or ahead only by this
  change. Base: `git merge-base HEAD origin/<baseRefName>`.
- Local: a base the user names; else the parent of this change's first
  commit, if it made commits; else `HEAD` for uncommitted changes only. A
  whole local branch: `git merge-base HEAD origin/<default-branch>`.
- Not in git (live config, scripts, units): copy the original files
  before editing. The diff is `diff -ruN <copy> <live>`.

The change is base to working tree in those paths, with untracked files.
Use one diff command all rounds. State intent and out-of-scope in 1-2
sentences. Run covering checks. Do not send code this change breaks. Note
missing or pre-failing checks in the brief.

## 2. Start the reviewer

In T3, call `orchestrator_capabilities`. Pick the latest Sol unless the user named
another model or a version; use an effort only if the user named one. Do not
hardcode an ID. Record provider, model, and effort only if exposed. Use `delegate_task`
every round (`role: "review"`, `mode: "async"`). Save `taskId`. New
`clientRequestId` per round. Keep `childThreadId` for resuming that round only.
Do not use `t3_thread_send` to start a new round, and never use
`create_threads` or `t3_thread_launch` for review.

If T3 tools are not listed, try one direct capability call. Outside T3, use
a native subagent on that model and keep its ID. In Claude Code, use a custom
subagent only if it is set up to run that model. If no route runs it, report
the blocker. Do
not substitute a model.

Do not edit scoped files while review runs. Wait for the notice. Do not
poll or start a second reviewer. Timeout, error, or no reply is not
approval. If the reviewer fails or is interrupted, resume that same thread:
`t3_thread_send` its `childThreadId` (`mode: "auto"`) with exactly the
message "Continue where you left off." No output does not mean no work,
and a new thread throws that work away. Do not start a fresh
`delegate_task`. Instead, if you get repeated failures, search in all
projects for a non-settled thread containing "provider failure investigation" and send it a
message; if it does not exist, start a new thread containing "provider failure
investigation" to investigate the root cause.

Round 1 brief (T3 children get no history):

```
Review only. Do not edit, commit, push, publish, comment on PRs, or start subagents.
Repo: <abs path or folders> Base: <sha or copy> Intent: <intent> Out of scope: <scope> Checks: <results>
See: <diff command>; git status --short (read untracked).
Read surrounding code and files. Find bugs, broken callers or references, mismatch with intent.
Skip style unless it hides a bug. Hold to the smallest change. Flag code,
tests, or guards the intent does not need. Other paths, rare cases, small
perf, extra compat: nits.
blocker = intended case wrong. should = real bug callers will hit, in intent. nit = rest.
Reply:
VERDICT: APPROVE or CHANGES
S1 [blocker|should|nit] <file>:<line> <claim>. Fix: <suggestion> (stable ids)
```

## 3. Answer findings

Check each finding and severity yourself. Another path, rare case, or
small cost is a nit, whatever label it came with.

- Agree: smallest fix. Prefer delete or narrow over new lock, cache,
  helper, or option. Fix the case that happened. Add one focused test
  that fails without the fix, if intent needs it.
- Disagree: say why, with evidence (file:line, test, output).
- Out of scope or nit: decline in one line. List as follow-up.

Update the ledger. Re-run checks and the diff command with `--shortstat`
(or `| diffstat`). If review grew the diff, cut first. Do not agree just to
end the loop.

## 4. Next round

Start one only if budget remains and a blocker or should is open, behavior code
changed, or approval does not cover the current behavior diff. Use a fresh
`delegate_task` in T3, or follow up the saved native ID. Send the round 1 brief,
the ledger (`fixed:` / `disagree:` / `declined:` + evidence), the same base
diff, and the change since last round. Tell the reviewer: open ids and changed code only.
Mark each old id `resolved` or `still open: <reason>`.

## 5. Stop and ship

Agreed: APPROVE on the current behavior diff, no blocker or should open,
no concern of your own. Stuck: an item stays disputed after two replies,
or budget ends. Show both sides in 2-3 lines each. The user decides.
Budget end is not approval.

Do not push, publish, or release during the loop. Do so after agreement only
if the user asked to push, ship, publish, release, or update the PR. If you
push to a PR, add above the model blurb:

`Reviewed with review-loop: <N> rounds with <model>. <model> verdict: approved`.

Add `on <effort>` only if verified.

Report: rounds, model, what the review changed, diff size before/after, fixed and
declined findings with reasons, usage if exposed (else say not), pushed,
published, or not.
