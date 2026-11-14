# Read-only target-% feed — schema 1

Installed command: `~/.local/bin/usage-target-rates`, no arguments/stdin.
Import `~/.local/bin/usage_target_rates.py:snapshot()` (no live arguments).
The optional home/at parameters are disposable fixture seams only.

Stdout one JSON object, exit0 for a valid envelope including unavailable cells;
unsupported arguments exit2. Consumers require schema1/scope main/unit
target_percent; missing command, invalid output or old envelope => unavailable,
never zero. No credential or account identifiers are exported.
This is presentation, not an admission API; do not use it to change guards.

Envelope: measured_at UTC ISO, windows_minutes [5,15,30,60], context string|null,
scale number|null, freshness {ledger_age_seconds:number|null,
sampler_age_seconds:number|null}, freshness_limits {ledger_seconds:120,
sampler_seconds:90,source_seconds:1200}, categories array.

Each category row: category fable|non-fable|codex|outside-api, label,
target_per_hour:number|null, windows mapping string minute keys to
{target_percent:number|null,status:ok|stale|incomplete|unavailable,
reason:string|null}. A number is present ONLY when status=ok. Values are not
clamped to100. Render “152 target %”;100 target %=usage equals target rate.
If a consumer caches the whole envelope, stop treating it as current after90s
from measured_at (or any future timestamp); refresh via its existing request
path. No new timer/service was installed.

Subscription freshness follows existing cadence: ledger<=120s (two60s reads),
passive snapshot/token_last_sampled<=90s (existing sampler cutoff), all relevant
actual category observations<=1200s (Fable fable_observed_at; NF nf_observed_at;
Codex observed_at). NF without explicit paired points requires fresh account
AND Fable observations; the existing NF estimated fallback already records its
actual account-based nf_observed_at. A fresh account timestamp cannot certify
a stale model/category meter. Partial passive sampling, per-window meter
gaps, missing/carried source gauges, or inadequate sample coverage return null;
long windows can be incomplete while5m remains valid. Missing frozen baseline
never creates one. Metadata mtime is not provider observation time. Ledger,
sampler and policy signatures must remain consistent during computation.

Math is shared with the installed warning ladder through pure
claude_usage_boxes.target_rate_window(), extracted unchanged from
ladder_evaluate(). It uses current rate_context scale/reference and the existing
meter/passive estimate choice and soft-credit semantics. Ratio times100 is the
current target %. Confirmed stop arithmetic/caps/multiples stay unchanged.

Outside API is independent: existing outside_api_budget.readings() on a
mode=ro/query_only SQLite read transaction, subtracting existing soft credit.
Outstanding reservations count as existing authoritative rules require. No new
calls mean genuine0 target %. Missing/unreadable ledger is null, independent of
subscription meter status. No provider fetch, auth/config/key reads, state
mutation, notification or subprocess in the adapter.

Sources: installed category-ledger/passive-rate and approved boxes.toml; outside
API budget.sqlite. Consumers call the command, not account state directly. Source readings can be fresh while longer-window
sampling is incomplete, truthfully reported per cell.

Uninstall: remove usage-target-rates and usage_target_rates.py; consumers then
show unavailable. No stored usage/notification history is rewritten.
