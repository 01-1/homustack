"""Offline token -> subscription-point estimate; no sampling or state writes.

``estimate_points(records)`` returns ``fable``, ``non-fable`` and ``codex``
in large-account category points. A category is None if ANY contributing
record cannot be estimated. An empty category is 0. Never treat None as zero
when taking the lower of this estimate and a meter estimate.

Each record supplies account (email), model, and token counts. Canonical
input/output/cache_write/cache_read fields are disjoint counts; input means
UNCACHED input. Raw Claude usage field names are also accepted. Raw Codex
input_tokens includes cached_input_tokens, which is subtracted exactly once;
output_tokens already includes reasoning. cache_write_1h, when supplied, is
a subset of total cache_write. Counts must describe NEW usage, not cumulative
session totals. Account attribution is the caller's responsibility.

Pooling weights are read from boxes.toml on every call. No capacity/reset
inventory or five-hour allowance is added to weekly category spending.
The fitted model is provisional; inspect its validation and usable flags.
"""

import json
import math
from pathlib import Path
import tomllib


CATEGORIES = ("fable", "non-fable", "codex")
DEFAULT_MODEL = Path(__file__).with_suffix(".json")
DEFAULT_POLICY = Path.home() / ".config/claude-usage/boxes.toml"


def _number(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError("counts and coefficients must be finite nonnegative numbers")
    if not math.isfinite(value) or value < 0:
        raise ValueError("counts and coefficients must be finite nonnegative numbers")
    return float(value)


def _counts(record, provider):
    def get(*keys):
        return next((record[k] for k in keys if k in record), 0)

    cached = _number(get("cache_read", "cache_read_input_tokens", "cached_input_tokens"))
    uncached = _number(get("input", "input_tokens"))
    if provider == "codex" and "input" not in record:
        if cached > uncached:
            raise ValueError("Codex cached input exceeds inclusive input_tokens")
        uncached -= cached
    written = _number(get("cache_write", "cache_creation_input_tokens", "cache_write_input_tokens"))
    ttl = record.get("cache_creation") or {}
    hour = _number(record.get("cache_write_1h", ttl.get("ephemeral_1h_input_tokens", 0)))
    if hour > written:
        raise ValueError("one-hour cache writes exceed total cache writes")
    return {"input": uncached, "output": _number(get("output", "output_tokens")),
            "cache_write": written - hour, "cache_write_1h": hour, "cache_read": cached}


def estimate_points(records, *, model_path=DEFAULT_MODEL, config_path=DEFAULT_POLICY,
                    model=None, policy=None):
    """Return {category: points | None}, honoring each meter's usable flag.

    model/policy allow callers and tests to supply already loaded dictionaries.
    Otherwise both files are read locally. Missing/invalid files disable every
    category. Invalid token counts raise ValueError rather than undercounting.
    """
    try:
        if model is None:
            model = json.loads(Path(model_path).read_text())
        if policy is None:
            with Path(config_path).open("rb") as stream:
                policy = tomllib.load(stream)
        if model.get("version") != 1:
            return dict.fromkeys(CATEGORIES)
        weights = {(s["provider"], s["email"]): _number(s["weight"])
                   for s in policy["sources"]}
    except (OSError, ValueError, KeyError, TypeError):
        return dict.fromkeys(CATEGORIES)

    result = dict.fromkeys(CATEGORIES, 0.0)
    for record in records:
        email = record.get("account", record.get("email"))
        account = model.get("accounts", {}).get(email, {})
        name = str(record.get("model") or "").lower()
        provider = record.get("provider") or account.get("provider")
        if provider is None:
            provider = "claude" if name.startswith("claude-") else "codex" if name.startswith("gpt-") else None
        if provider == "claude":
            if not name.startswith("claude-"):
                result["fable"] = result["non-fable"] = None
                continue
            category = "fable" if "fable" in name else "non-fable"
            meter_name = "weekly_fable" if category == "fable" else "weekly_non_fable"
        elif provider == "codex":
            category, meter_name = "codex", "weekly_all"
        else:
            return dict.fromkeys(CATEGORIES)

        counts = _counts(record, provider)
        weight = weights.get((provider, email))
        meter = account.get("meters", {}).get(meter_name, {})
        family = model.get("model_families", {}).get(name)
        coeff = (meter.get("points_per_million_tokens") or {}).get(family)
        if (account.get("provider") != provider or weight is None or
                meter.get("usable") is not True or not coeff):
            result[category] = None
            continue
        if result[category] is None:
            continue
        try:
            cost = sum(n * _number(coeff[k]) for k, n in counts.items() if n)
        except (KeyError, ValueError, TypeError):
            result[category] = None
            continue
        result[category] += weight * cost / 1_000_000
    return result
