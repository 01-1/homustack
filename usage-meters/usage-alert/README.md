# usage-alert

A passive watcher for CLIProxyAPI v8.0.54. It makes no model, OAuth or
profile calls, and never opens auth files.

- Follows both proxies' systemd journal (JSON keeps provider attribution)
  and their `logs/*.log` files. It skips existing content on start, then
  follows new lines and rotation every second.
- Only response sections and upstream-failure warnings are matched.
  Messages, keys and tokens are never copied.
- Popups only for "OUT OF USAGE: <provider> — ALL ACCOUNTS". That needs a
  fresh, attributed cap with a future reset for every `.json` name in that
  proxy's `auth/` folder. Single-account events and cooldowns are tracked
  but not shown.
- Evidence expires after 10 minutes or at the cooldown's end. Any observed
  success, or an unknown failure, cancels it.
- Optional: `USAGE_ALERT_MIRROR` names a command that also gets each
  all-accounts notice as `title\nbody` on stdin.

Settings: `USAGE_ALERT_UNITS`, `USAGE_ALERT_DIRS`, `USAGE_ALERT_MIRROR`
(see the script's docstring or the top-level README).

Install: copy `usage-alert.sh` to `~/.local/lib/usage-alert/`, and
`usage-alert.service` to `~/.config/systemd/user/`. Then run
`systemctl --user enable --now usage-alert.service`.

Checks:

- `python3 -I test_usage_alert.py` runs synthetic tests only.
- `usage-alert.sh --test --provider Codex < lines.txt` prints matches as
  JSON, with no popup.
- `--test --notify-test` sends at most one popup, titled "Usage alert test
  (ignore)".
