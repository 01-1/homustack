---
name: live-system-change
description: Change a live system safely, such as releasing or switching the active version, changing live parameters, or changing running services, caps or limits. Use when the human asks to upload, deploy, release, cut over, relaunch or change a live parameter, or says "ship when done".
---

# Change a live system

A live system is anything other people or a score depend on right now: the deployed version, a running service, a live configuration, a cap or a limit. If the project names an owner for releases, that agent does them; everyone else hands the change to it.

1. **Restate the exact change before acting** when it involves numbers, conditions or a choice between versions. For example: "release `app.tar` sha256 `abc…`, replacing live `def…`, before the 14:00 cutoff." If the human says "CONFIRM", reply with only the restatement, and act once they agree.
2. **Use real inputs.** Use the measured value, hash or setting the human points to, or the one in the live configuration. Never use a made-up default. If it is missing, say so.
3. **Know whether this change needs a go.** Follow the project's release rules. Without them: a fix that restores intended behaviour (a crash, an error, a timeout) without changing what the system does may ship; a change in behaviour, a switch to a different version, and any change to running services, caps or limits needs an explicit go from the human or a recorded standing approval. "Ship when done" or "release now" counts; a passing check does not. Once it is approved or needs no approval, carry out the steps yourself; don't ask the human to click through them.
4. **Exactly one live version, released one way.** Release only through the project's release script or procedure, never by hand around it. A good release path refuses without a recorded decision tied to the exact hash, checks the exact artifact it ships, checks for an earlier release whose outcome is unknown, respects any freeze or wait, and confirms the new version is live. An upload is not proof the version is live. Keep the last proven live version as the fallback. Respect freezes.
5. **"STOP" means stop your own work.** Leave the live system and running services as they are unless the human explicitly says to stop or replace them. Then report what you changed, if anything.
6. **Low usage:** when the human says usage is nearly gone, do only the release or cutover steps, with as few tool calls as possible and no doc work.
7. **After the change:** confirm it on the live system (the live version's id and hash, or its next health check). Say when you checked, with the time zone. Watch for the failure the human mentioned. Only if something went wrong or a decision is needed, raise it on the project's human channel.
