# Shared outside-API box

One **total** dollar cap (not daily or per provider) for every outside API a
caller uses. Cerebras is the only enabled provider; `spare` has a reporting line but
remains refused until separately approved. No key, sign-in, paid call or provider
usage query is part of installing/testing this tool.

`~/.local/bin/outside-api-budget reserve` reads stdin JSON
`{"id":"CALL_ID","provider":"cerebras","upper_usd":"0.10"}` and returns
`{"allowed":true,"reservation_id":"CALL_ID"}` or allowed=false/reason. Reserve
the conservative input+max-output cost **before** every attempted HTTP call.
Duplicate call IDs are refused, never permission to call again. Missing cap,
unreadable guard/policy, unknown/unapproved provider, a persisted hard stop or
insufficient total/rate allowance denies. No provider fetch occurs.

`outside-api-budget settle` reads `{"id":"CALL_ID","actual_usd":"0.03"}`.
Settlement replaces that reservation with measured actual cost; duplicate equal
settlement is harmless, conflicting settlement fails. Unknown/incomplete failure
without trustworthy usage retains its full reservation indefinitely. Settlement
is accepted even if stopped and records an unexpectedly larger actual charge,
then hard-latches if necessary. It never hides already-billed cost.

SQLite reservation ledger under `~/.local/state/outside-api-budget/`0700,
`budget.sqlite`0600, FULL-sync transactions and BEGIN IMMEDIATE reserve serialize
all callers. Authoritative total = settled actuals + outstanding upper bounds,
across all providers. The caller also records the tokens/model/latency audit stream at
its own `calls.jsonl` (0600): same-id fsynced started/reserved
before HTTP and finished after. These are events, never two additive charges.
The pre-call reservation closes the crash gap; missing finished keeps the upper
bound. Output tokens already include reasoning tokens (informational subset).
Computed cost comes from the caller's local metered record, not remote billing.

Installed command to set a future human-approved cap:
`outside-api-budget set-cap 40`. It requires interactive stdin AND stdout;
installation initializes the approved cap without touching old state.
Same authoritative H/active run scale and stops are read from existing
`~/.config/claude-usage/boxes.toml`: the target rate is cap/H dollars per hour at 1x.
The existing RATE_LADDER and notify_levels are imported unchanged:5m2/2.5/2.75→3x,
15m1.5/1.75/1.875→2x,30m1.25/1.35/1.45→1.5x,60m1.1/1.2/1.25→1.3x.
Total cap preserves existing50%NOTE/75%WARNING/90%WARNING/100%stop.
No projection or elapsed-hour accounting. Rate text says `152 target %`.

Outstanding reservations count in every current window until settled, protecting
unknown in-flight costs; actual charges enter windows at settlement time.
Admission refuses a reservation that would exceed any current total/rate stop.
Measured settled charges reaching a stop exactly or exceeding it persist the
latch; time passing cannot release it. Pending upper bounds block admission but
do not permanently latch unused cost when a lower measured settlement arrives.
Existing category guards are unchanged.

Human `uncap` now also releases this box; `uncap <minutes>` adds fixed, stacking
target-rate×minutes/60 dollars to it with no spending deletion. Inadequate credit
keeps it stopped and reports shortfall. Hard release retains cumulative spend,
gives another cap-sized total allowance and restarts rate windows, matching the
existing command's human forgiveness semantics. Dedicated `outside-api-budget
uncap [minutes]` also requires real interactive stdin/stdout. Agents never run
either release command. Existing long per-category subscription forms remain.

`outside-api-budget status` exposes total/provider/pending/rate/stop JSON;
`claude-usage` adds one compact line. Existing usage --warn cadence calls local
check/notification processing; no new timer/provider read. The reserve/settle
paths do no subprocess/notification work, preserving the caller's5s deadline.
Warnings/stops/releases persist locally and retry the optional Matrix mirror on
the usage cadence.
