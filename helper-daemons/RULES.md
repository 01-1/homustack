# Rules for agent helper scripts

Rules for small scripts and daemons that read and message T3 Code agent
threads. `t3helper.py` implements the mechanical ones. In the multi-agent
project they came from, every messaging script built this way logged 0 failed sends.

## Reading and messaging threads

Use the `t3` CLI (`~/.t3/bin/t3`). It talks to the running T3 app, so it works
from any shell or script. Run it from the project directory or pass
`--project <dir>`. Add `--json` for machine-readable output.

- `t3 thread list --project <dir> --json`: newest first. Each entry has
  `threadId`, `title`, `status`, `parentThreadId`, `link`, `createdAt` and
  `updatedAt`. `completed` means the last turn ended. The agent is idle but
  alive, and a message starts its next turn.
- `t3 thread show <threadId> --messages <n> --json`: the thread (plus
  `activeRunId`, `pendingRequestCount`) and messages with `role`, `runId` and
  `text`. Messages carry no timestamp, so use `runId` or `updatedAt`.
- `t3 thread send [flags] <threadId> -` with the message on stdin. `--mode queue`
  (the default) waits for the next turn. `--mode auto` steers into a running turn
  or starts one. `--mode restart` interrupts and restarts.
- `t3 thread activity --project <dir> --json`: activity of every thread
  without reading messages.
- There is no stop command. To stop an agent, send `--mode restart` with a
  message telling it to stop.
- Pass messages as one argument or on stdin, never through a shell string
  built from untrusted text.
- A message steered into a Claude Code agent while its tools run can make
  Claude Code report a cancelled call as "The user doesn't want to take this
  action right now". No human declined anything. The agent should check
  whether the action took effect, redo it if not, and carry on.

## Rules for the scripts

- **Form.** Python 3, standard library only, one file, `#!/usr/bin/env python3`,
  mode 755. Never read, print or store API keys or tokens.
- **Calling t3.** `subprocess.run` with an argument list (never a shell string),
  output captured as text, a 30-second timeout. A non-zero exit, a timeout or
  an exception is a failure.
- **Finding an agent by name.** (1) The last line of `threads.tsv` whose first
  field equals the name, after stripping whitespace from both fields. (2)
  Otherwise, among `t3 thread list` entries whose title equals the name, starts
  with `<name>:` or `<name> `, or starts with `You are <name>.`, the only
  one. If several match, the name is ambiguous: fail the send at once with
  every matching id in the log line, and don't guess. (3) Otherwise the agent
  is not found, which is a failed send. A script that means to message every
  match lists them on purpose (`search_all`) instead. Look it up again before every send and never cache, because
  agents restart. Don't filter by status.
- **Sending.** `--mode auto` with the message on stdin. If that fails, try
  `--mode queue` straight away as part of the same try. If both fail, wait
  2 s and retry, three tries in all.
- **State.** One JSON file per script, written to `<file>.tmp` and then
  `os.replace`d. Save only after a successful send, so a restart neither
  repeats nor loses a message. An unreadable state file is reported and
  treated as missing.
- **One copy only.** Long-running scripts (except under `--dry-run`) take a
  non-blocking `fcntl.flock`. If it is held, print `already running` and
  exit 3.
- **Loop.** Catch every exception inside the loop, print it with the time and
  carry on. On Ctrl-C or SIGTERM, exit 0 without a traceback.
- **Restart.** Run it under `run-forever.sh`, detached with `setsid nohup`. The
  loop restarts the script 5 s after any exit except code 3. Check on it by
  reading the log's last lines.
- **Logging.** One line per send or failure: `HH:MM:SS`, `SENT` / `FAILED` /
  `WOULD SEND`, the recipient, the thread id or `NOT FOUND`, the first 80
  characters with newlines collapsed, and on failure t3's last stderr line.
  Print with `flush=True`.
- **`--dry-run`.** Does everything except sending, writing state and taking the
  lock, and prints each message in full after its `WOULD SEND` line. A
  long-running script makes one pass and exits 0. Test only with `--dry-run`,
  and never send a test message to a live agent.
