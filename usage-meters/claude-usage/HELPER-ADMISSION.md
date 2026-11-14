# Category helper admission, schema 1 (Codex, Fable, Claude non-Fable)

Installed CLI: `~/.local/bin/usage-helper-admission [--category CATEGORY]`, no stdin.

Canonical categories (exact spelling, lowercase): `codex`, `fable`, `non-fable`.
No arguments means `codex` (the original schema-1 behaviour, unchanged). The only
accepted form is the two words `--category <name>`; `--category=fable`, other names
(`claude`, `Fable`, `opus` ...) or extra words print `unsupported_arguments` and exit 2.
Map a model to its category yourself only from the usage policy: Fable models are
`fable`; every other Claude model (Opus, Sonnet, Haiku) is `non-fable`; Codex/GPT is `codex`.
The result's `category` field echoes the category checked: a caller must deny unless it
equals the category it asked for (it is absent on `unsupported_arguments`).
Installed import: `~/.local/bin/usage_helper_admission.py`,
`check(home: Path | None = None, at: float | None = None, category: str = 'codex') -> dict`.
`home` and `at` are fixture seams only; production callers use `check()`.

The CLI emits one JSON object. Admit a helper only if exit status is **0**,
`schema == 1`, `allowed is true`, and `status == "clear"`. Exit 1 means `hold`;
exit 2 means `unknown`. Missing executable, timeout, invalid JSON, unsupported
schema or any inconsistent result denies admission. Read again immediately before
native helper launch. This is a snapshot, not a reservation or launch authorization.
The caller keeps its own memory, load and concurrency gates, and binds the native target itself.

Fields:

| Field | Meaning |
|---|---|
| `schema` | Integer 1 |
| `category` | The category checked (`codex`, `fable` or `non-fable`) |
| `allowed` | Boolean; true only for clear |
| `status` | `clear`, `hold`, or `unknown` |
| `reasons` | Stable string codes; empty only for clear |
| `measured_at` | UTC ISO timestamp of this check |
| `freshness` | Per-file `age_seconds` and `max_age_seconds` |
| `coverage` | Configured/fresh source counts for the category's provider, source age bound, sampler window metadata when reached, and explicit proxy-owned routing disclaimer |
| `override` | Existing valid human hard-continue for the checked category; never created here |

Hold codes are `<category>_stop_latch`, `<category>_fixed_budget_stop`,
`<category>_fixed_budget_warning`, `<category>_rate_warning`, `<category>_rate_stop`,
`<category>_all_accounts_exhausted`, e.g. `codex_stop_latch`, `fable_rate_stop`,
`non-fable_all_accounts_exhausted`. Exhausted means every configured account's 5-hour or
weekly meter is full (Fable: also its own Fable meter). `unsupported_category` is unknown. Unknown codes identify missing/invalid/stale
evidence or failed calculation. They may include `invalid_source_coverage`,
`rate_coverage_incomplete`, `fixed_box_coverage_incomplete`,
`missing_approved_opening`, `missing_frozen_preparation`,
`stale_or_incoherent_evidence`, `evidence_changed_during_check`, or the fallback
`cached_evidence_unavailable_or_incoherent`. Treat all unknown codes identically
for admission; they do not authorize a probe or repair.

The wrapper reads only existing policy and cached usage metadata:

- `~/.config/claude-usage/boxes.toml`
- `~/.local/state/claude-usage/category-ledger.json`
- `~/.local/state/claude-usage/passive-rate.json`
- `~/.local/state/claude-usage/hard-stops.json`
- optional `~/.local/state/claude-usage/hard-continue.json`

It reuses installed `claude_usage_boxes` pure arithmetic (and that arithmetic's
optional local token calibration). It makes no provider request, subprocess,
auth/config/key read, notification, enforcement call, lock file or state write.
Python bytecode writes are disabled. Stable inode/size/mtime signatures before
and after checking reject observed concurrent changes, including policy changes
not yet sampled. This does not claim a transactional producer snapshot or pin a
proxy-selected account.

Freshness follows the existing units/math: the passive sampler runs every 30s
and itself declares a discontinuity after 90s, so passive evidence must be at
most 90s old. The cached-meter unit runs every 60s; ledger evidence must be at
most two cycles (120s) old. Every configured source of the category's provider (Claude for `fable`/`non-fable`)'s recorded and actual
observation timestamps must independently meet the existing 1200s coverage
bound. Future timestamps, missing configured sources, carried Codex gauges,
pending rebases and invalid numerics deny admission.

For current rates, call `ladder_evaluate` on a local ledger copy with a NEW
crossing state: a fired level then represents this check's actual arithmetic.
Persisted `fired` levels, pending delivery and old warning text never establish
current warning state. The passive sampler must certify continuous coverage to
the beginning of the longest current rate window (60m, clipped to the actual
recorded epoch); a recent sampler restart cannot manufacture clear history.
Missing recorded run/preparation openings are unknown, never initialized here.
Fixed-budget warning/stop checks use `readings`/`warnings` on copies as well.
Existing human hard-continue suspends relevant stops, **not warnings**, matching
the producer. Account latches deny optional helpers even when another account
could serve traffic; no account selection/routing exception is inferred.

Only the currently supported preparation/final ladder regime is admitted;
other rate regimes return unknown pending an explicit owner integration.
An unknown result is not evidence of an account authentication problem.

Uninstall: remove only the two installed wrapper files (the caller then fails
closed). No ledger, latch, policy, meter,
budget, notification route or service needs restoring.

Claude rows: the Claude meter stores `u7`/`u5` as header text (e.g. `"0.42"`); for
Claude rows only, such text is parsed as a finite number (non-numeric, NaN or infinite
denies). Codex rows stay strictly numeric. A recently restarted passive sampler
returns `rate_coverage_incomplete` for every category until it has covered the
current 60-minute window; that is the intended fail-closed behaviour, not a fault.
