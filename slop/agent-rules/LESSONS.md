# Lessons from running dozens of agents

From a two-week multi-agent project: dozens of T3 Code agent threads, each with its own role, working toward one deadline. Each lesson gives what happened and what to do instead.

## Process

1. **"Done" was measured on the artifact, not on the behaviour.** A ledger, a rule and a checker each passed while nothing invoked them, and ownership stopped at each handoff between the agent that wrote the rule, the one that triaged it and the one that built it. *Do instead:* a fix is complete only when something triggers it automatically and it has been seen working end to end; one owner keeps the obligation until then. (`../fix-ledger` is the tool built from this.)

2. **Queued messages to busy agents weren't read.** Agents kept long turns running, so a queued message waited for the turn to end, and messages sent during a compaction or start-up gap stayed queued until a T3 patch promoted them to steers. *Do instead:* send with `--mode auto` (steer).

3. **Bookkeeping between agents cost a lot.** One agent wrote 99 reports: 40 on settling T3 threads owner by owner and 26 that passed rule changes and handoffs to running agents. *Do instead:* one rule file plus one file per role, and a broadcast script for changes; scripts do relaying and bookkeeping.

4. **Too many named agents and too much churn.** Most of the first repo's commits landed on a single day, and its rule file grew to tens of kilobytes of stacked, dated quotes. A restart in a clean repo with one rule file and one file per role helped, but even then only about a fifth of the rule file was general rules; the rest was the role list, models and project-specific pages. *Do instead:* replace rules rather than stack them, keep the rule file short, and keep reference material in separate files.

5. **The channel to the humans changed three times, across four systems:** Discord, then Matrix, then a dashboard feed, then a Wekan board. Effort went into plumbing instead of the work. *Do instead:* pick one channel at the start and keep it.

6. **Built and never used.** The rehearsal log had a 20-column schema and no rows. *Do instead:* build measurement only when a run will fill it, and check after the first run that it did.

7. **Most practice runs settled nothing.** Most practice runs meant to compare models gave no clear answer: they were aborted, or the difference was within the noise, or the compared setups differed in more than the model. The few that did give an answer held everything but the model fixed. *Do instead:* hold everything but the variable fixed and decide how the result will be judged before the run. Once real data arrives, use it directly.

8. **What paid off was small and strict:** single-purpose scripts that followed the helper-script rules, the launcher, and one role file per agent. The messaging scripts' logs showed 0 failed sends (for example 201 sent by one runner, 72 by a relay, 26 and 17 by two others). By contrast the rehearsal metrics were never filled in (lesson 6); that a dashboard and a quick-answers service did not pay off either is a judgement, not something measured.

## Agents and humans

9. **Agents misread short messages during live runs.** In one morning a coordinator flipped a practice run's instructions five times in eight minutes and read "NO!!!!!" as "start". *Do instead:* when a short message has two readings and acting would change something running, state both and wait; after "stop", nothing restarts without "go". (Saved as `memories/ambiguous-short-message-no-action.md`.)

10. **A router added its own guesses.** Forwarding a human's request with a guessed interpretation sent the owner the wrong spec, and the human had to correct it four times. *Do instead:* forward the exact words and why that owner, nothing more.

11. **Retiring a role into one replacement just moved the overload.** One lead role was first handed whole to a single successor. The rule became: split so that no receiving thread gets more than 50% of the original load, and create new agents as well as offloading to existing ones; the next split gave the largest receiver about 35%. (See `../../agent-skills/retire-role`.)

## Tools and platform

12. **Testing a guard by running the real command made the action real.** An agent ran a stop command to show that it refused other callers, but that agent was the allowed caller, so it stopped every agent turn and had to be restored by hand. *Do instead:* test guards with `--dry-run` or by calling only the guard function. (Saved as `memories/never-test-real-commands.md`.)

13. **Steered messages produce false "user declined" results** in Claude Code (a known bug, anthropics/claude-code issues #89231, #108736, #29499): the incoming message cancels running tool calls and reports one as declined by the user. An agent that takes it at face value stops and waits for a human who never asked. *Do instead:* only a human's own message means stop; check whether the action happened, redo it, carry on.

14. **Scripts can list T3 threads but can't stop or settle them.** T3 caps a script's token at approval-required while agent threads run full-access, so a script's interrupt and settle calls fail with `runtime_mode_escalation_denied`. *Do instead:* stop and settle from an agent's own T3 tools (`../../agent-skills/stop-all-threads`, `../../agent-skills/settle-all-threads`).

15. **A retried thread start can create a duplicate agent.** The launcher that started the final run's threads avoided it by giving every start a fixed `--client-request-id` and retrying once with the same id. *Do instead:* the same, for any script or agent that starts threads.

16. **Model and effort changes and the prompt cache (measured on the CLI route, not inside T3).** Changing Claude's effort between turns kept cache reuse (about 70k cached tokens read on the changed turns for both Fable and Opus). Changing Codex's effort cut cached input from about 37k to about 9k tokens on that turn. Model switches were not measured, but the Claude Code docs describe caches as per model. *Do instead:* pick model and effort when a thread starts.

17. **Use a Claude subscription only through Claude Code.** Scripts, curl and other clients on a subscription login are outside the provider's terms and put the account at risk, so give scripts an API key instead.

18. **Backups failed on a provider limit, not a bug.** About 18 minutes after a scheduled prune finished, the backup hit the storage provider's download cap (`download_cap_exceeded`, HTTP 403), and no new snapshot could be saved while the cap held; whether the prune caused it was not established. An earlier failure was probably a race: a Git pack file vanished mid-backup (the cause was inferred, not observed). *Do instead:* know the provider's caps, keep prune and full-read checks away from them, and alert on the first failed backup.

19. **A "duplicate" copy held files that existed nowhere else.** Two repo copies had no commits the main repo lacked, yet each held 10 file versions found nowhere else, two of them the user's own notes. Deleting those copies and 308 snapshots also freed almost no disk space: on btrfs they were reflink copies sharing blocks, and 5 snapshots were subvolumes that plain deletion can't remove. *Do instead:* before deleting a copy, check each file's content (for example its git hash against the main repo's objects), not just the git refs; save anything unique first; and on btrfs, expect little space back and delete subvolumes with `btrfs subvolume delete`.
