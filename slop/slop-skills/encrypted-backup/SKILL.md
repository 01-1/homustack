---
name: encrypted-backup
description: Make a verified, encrypted backup of the user's work and agent state (one-off archive to a drive, incremental follow-ups, or a frequent restic job to a server with alerts), and back up before any cleanup or destructive change. Use when the user asks to back up, archive, "copy to the SSD", sync to another machine for backup, or before deleting, moving or clearing caches.
---

# Encrypted backup

1. **Before a destructive change (including cache cleanup):** list the affected paths and sizes, confirm backup coverage below, and get authorization for any destructive action not already authorized. Reuse an existing go; ask only about unresolved choices. A backup does not authorize deletion.
2. **Use the machine's automatic backup first.** Find the scheduled job (for example a systemd user timer running restic to a remote repository) and read its script or config for the current paths and exclusions. If the job is failing or its last run is unknown, treat the data as not backed up. Don't contact other machines as a backup fallback unless the user allowed it.
3. **Confirm recency and coverage read-only**, using the commands below. Require a completed snapshot no older than two scheduled intervals, and one that includes the affected data's latest changes. Compare its timestamp with the current time and the affected files' modification times. Timer activity or a successful service result alone is not proof: the job can skip on its lock. Inspect snapshot paths and excludes and list the actual affected subtree; a listed parent directory does not prove all its children are covered. Pin the snapshot ID reported by the listing when recording evidence or doing further checks; if `latest` changed between commands, repeat against that ID. Stop the destructive step if the snapshot is stale, missing, incomplete, or predates relevant changes; let the automatic job complete and recheck.
4. **One-off backups only for gaps.** Automatic jobs usually exclude caches and build output, credentials, SSH keys, proxy state, dotenv files, agent auth and settings, and app secrets, and often most of `~/.config`; many also use one-filesystem and cache-marker exclusions. Even reinstallable caches need a backup before destructive cleanup. For excluded paths, other filesystems, or changes the automatic job cannot capture, prepare a scoped encrypted one-off backup to an authorized destination before going on. Measure source size and destination free space first; if there isn't room, propose a smaller scope and wait. Use an existing secret file without printing or copying its contents; otherwise ask the user for the secret through the harness's secret prompt, never in chat. New credentials go in the machine's credentials folder (for example `~/credentials/<provider>/`, directory mode 700, file mode 600). Run authorized long jobs as a persistent service; delegate only within existing agent authorization and spend limits. Never run a backup, prune, restore or deletion as part of a read-only check.
5. **State verification scope honestly.** For a new archive, save and recheck its checksum and require a complete authenticated decryption and integrity read of every payload (or a complete restore to a separate destination). A checksum or decrypt-and-list alone is not enough. For incremental restic backups, snapshot and file listings prove metadata only; report metadata checks and sampled restores as exactly that. Restic's full payload check is `check --read-data`; use it only when authorized and budgeted (it downloads the whole repository, and provider download caps can stop it), with normal locking so it can't race a prune. An incomplete or failed read means full verification is incomplete. Don't require a full repository download before every cleanup, and don't call a recent automatic snapshot fully data-verified.
6. **Report current evidence.** Check in this turn before saying running, complete or verified. Record snapshot ID and time, covered and uncovered affected paths, verification scope, and any unreadable files or unresolved failures. If relevant files changed after the chosen snapshot, wait for a newer automatic snapshot; use an encrypted incremental one-off only for coverage gaps.

## Read-only preflight (restic)

Run in Bash with tracing disabled. The subshell keeps credentials out of the parent environment; never print environment values or credential file contents. `--no-cache --no-lock` avoids local cache and repository lock writes for these listings. Fill in the job's own unit names (the backup and, if there is one, the prune job), env file, repository, password file, host and tag, and replace the example subtree with each affected absolute directory. Inspect that directory's descendants only, not unrelated personal paths.

```bash
date --iso-8601=seconds
systemctl --user list-timers '<backup-unit>*' --no-pager
systemctl --user show <backup-unit>.service <prune-unit>.service -p Id -p Result -p ExecMainStatus -p ExecMainExitTimestamp
journalctl --user -u <backup-unit>.service -n 8 --no-pager
(
  set +x
  set -a
  source "<path to the job's env file>"
  set +a
  export RESTIC_REPOSITORY='<repository>'
  export RESTIC_PASSWORD_FILE='<path to the password file>'
  restic --no-cache --no-lock snapshots --host '<host>' --tag '<tag>' --latest 1 --json
  restic --no-cache --no-lock ls --host '<host>' --tag '<tag>' --long --recursive latest "<affected directory>"
)
```
