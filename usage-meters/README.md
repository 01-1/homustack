# usage-meters

Meters and alerts for Claude and Codex subscription usage when the accounts
are served through [CLIProxyAPI](https://github.com/router-for-me/CLIProxyAPI)
proxies, plus two T3 Code helpers that read those meters.

| Folder | What it is | Status |
| --- | --- | --- |
| `usage-alert/` | Watches both proxies' journal and log files. Pops up "OUT OF USAGE: <provider> — ALL ACCOUNTS" when every account has hit a cap. Model-free and never opens auth files. | **Working.** It ran as a user service for weeks. 23 offline tests pass. |
| `claude-usage/` | The meter. It reads each proxy's stored rate-limit headers and shows usage per account. It keeps a ledger, and checks configurable "usage boxes" (allowances per window) and rate ladders per category (Fable, Claude non-Fable, Codex). It sends warnings to the desktop, an optional Matrix relay and a T3 thread. Also: `uncap`, `usage-helper-admission`, `outside-api-budget`, `usage-target-rates`. | **Partial, tied to one setup.** 557 offline tests pass (see below). |
| `usage-dashboard/` | Local page at `127.0.0.1:8770` showing target-rate %, capacity used and per-account %, with uncap buttons. Needs `claude-usage` installed. | **Working** when last used. Its offline tests pass. |
| `pace/` | Starts a "making-faster" helper (`t3 thread assist`) for a T3 thread that has been working for more than 5 minutes, if memory, load and usage admission allow it. Disabled by default. | **Untested.** It was never switched on. 11 offline tests pass. |

## Status evidence

Run in each folder:

- `usage-alert`: `python3 -I test_usage_alert.py` passes 23 tests. Also
  `usage-alert.sh --test --provider Codex < line` matched a sample cap line.
- `pace`: `python3 -B -m unittest test_pace` passes 11 tests. `pace.py` with
  a config that has no project denies, and writes nothing.
- `usage-dashboard`: `python3 -B -m unittest test_usage_dashboard` passes.
- `claude-usage`: `HOME=$(mktemp -d) python3 -B -m unittest discover -p 'test_*.py'`
  runs 557 tests, all passing.
  - "No hard stops": where a box would stop a category, it reads the
    over-cap boxes aloud on repeat (`usage_speaker.py`, Piper or eSpeak)
    instead of disabling models in the proxies' auth files. The tests check
    the spoken alert and that no auth file is edited. Files an older version
    disabled are still restored.
  - `claude-usage --help` runs offline.

All tests use temporary folders and fixtures, and record the voice instead of
starting it. They never start a process, open a socket or send a signal (checked
with Python audit hooks). On a machine with
`/etc/agent-cloud/category-routes.json` and passwordless sudo, the CATEGORY
publisher would queue real notices; the tests point it at a missing file.

## Requirements

Python 3.11+ standard library. Linux with systemd user services and
`notify-send`. CLIProxyAPI v8.0.54 running as `cliproxyapi.service` (Codex,
port 8317) and `cliproxyapi-claude.service` (Claude, port 8318), with
management keys. Optional: Piper or `espeak-ng` and `pw-play` for spoken
alerts, and T3 Code for thread notices and `pace`.

## Setup and settings

`usage-alert`, `usage-dashboard` and `pace` each have their own README and
install steps. `claude-usage` has none; install it like this (from
`usage-meters/claude-usage/`):

```bash
mkdir -p ~/.local/bin ~/.config/claude-usage ~/.config/systemd/user
# the commands and the modules they import, but not the tests or fixtures
# (test_mock_usage.py is a module, not a test: usage-test-mocks imports it)
cp -n claude-usage uncap usage-helper-admission outside-api-budget usage-target-rates usage-test-mocks \
      claude_usage_*.py outside_api_budget.py token_cost_model.py token_cost_model.json \
      usage_helper_admission.py usage_speaker.py usage_target_rates.py test_mock_usage.py ~/.local/bin/
cp -n boxes.proposed.toml ~/.config/claude-usage/boxes.toml   # then edit: accounts, dates, approved
cp -n claude-usage*.service claude-usage*.timer ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now claude-usage.timer claude-usage-meter.timer claude-usage-rate.timer
```

It reads the proxies' management keys from
`~/credentials/cliproxyapi-claude/management-key` (Claude) and
`~/credentials/cliproxyapi/management-key` (Codex), and the proxies' auth
folders `~/.cli-proxy-api-claude/auth` and `~/.cli-proxy-api/auth`. The
compute-spend timer (`claude-usage-compute.timer`) is optional.

The settings that used to be hard-coded are now environment variables
(defaults in brackets):

- `usage-alert`:
  - `USAGE_ALERT_UNITS` [`cliproxyapi.service=Codex,cliproxyapi-claude.service=Claude`]
  - `USAGE_ALERT_DIRS` [`Codex=~/.cli-proxy-api,Claude=~/.cli-proxy-api-claude`]
  - `USAGE_ALERT_MIRROR`: a command that also gets each all-accounts notice
    [none]
- `claude-usage`:
  - `CLAUDE_USAGE_CLAUDE_URL`, `CLAUDE_USAGE_CODEX_URL` [the proxies'
    management URLs on 8318 and 8317]
  - `CLAUDE_USAGE_ALERT_LOG` [`~/.local/state/claude-usage/usage-alerts.log`]
  - `CLAUDE_USAGE_THREAD`: a T3 thread for notices [none]
  - `CLAUDE_USAGE_NOTICE_THREAD`: a T3 thread for category-death notices
    [none]
  - `CLAUDE_USAGE_COMPUTE_START`: the compute-spend tracking start,
    `YYYY-MM-DD` [`2026-01-01`]. Set it to your install date before enabling
    the compute timer, or the totals reach back to the default.
- `usage-dashboard`: `CLAUDE_USAGE_BIN`, the folder where `claude-usage` is
  installed [`~/.local/bin`].
- `pace`: `~/.config/code-pace/config.json` (see `pace/config.example.json`):
  `projectId` and `environmentId` (required), `excludedThreadIds`,
  `holdFiles`, `admissionCommand`.

`claude-usage` itself reads `~/.config/claude-usage/boxes.toml` (examples in
`claude-usage/fixtures/` and `claude-usage/boxes.proposed.toml`) and
`~/.config/claude-usage/must-last.json` (deadlines usage must last until; no
built-in ones now).

## Limits: what is still tied to the old setup

`claude-usage` can't be made general without a rewrite. It assumes:

- Exactly two CLIProxyAPI proxies and three categories (`fable`, `non-fable`,
  `codex`) by model-name pattern.
- It reads each proxy's local management API (`/v0/management/auth-files`, the
  stored rate-limit headers) and the `excluded_models` field of the Claude
  proxy's auth files. It sends no model traffic, but check that this fits your
  provider's terms before using it on a subscription login.
- A Matrix relay at `~/.local/lib/matrix-relay/notify_mirror.py` and a Discord
  sender in `~/.local/lib/discord-notify`. Without them those channels just fail,
  and their notices keep retrying.
- A recovery agent's events file (`recovery-category-events.jsonl`, next to the
  log), "category death" notices posted into running T3 threads, and an optional
  CATEGORY publisher for cloud agents (`/etc/agent-cloud/category-routes.json`).
- The repeating voice is started from the `Type=oneshot` timer units, which
  use the default `KillMode=control-group`, so systemd probably kills it as
  each run ends (the speaker was seen respawning every minute without
  finishing its phrase). Not fixed: `KillMode=process` on the three meter
  units, or starting the speaker with `systemd-run --user`, would fix it.
- A preparation period followed by one "final" run: automock and test-mock
  budget shares (test runs are T3 projects titled `Mock *` with workspaces in
  `~/mock/run-*`), a "prep" total before 18:00, a final-run preflight from
  17:45, and US Eastern Time in messages.
- The compute-spend reminder reads boat, Modal, RunPod and Vast billing through
  their CLIs and counts from a fixed start date (`claude_usage_compute.py`).
- The docs `HELPER-ADMISSION.md`, `TARGET-RATES.md` and `OUTSIDE-API.md`
  describe the interfaces as they were built, terse and with the original
  numbers as examples.

`claude-usage/token_cost_model.json` is a small example with made-up numbers.
The real one is fitted from your own meter readings and session logs (its
`reproduction` list gives the method); until you fit one, the token estimate
gives no figure and the meter estimate is used.
