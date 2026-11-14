"""Small integration-contract tests; no provider requests or state writes."""

import copy
from pathlib import Path
import unittest

from token_cost_model import estimate_points


def fixtures():
    coefficients = {"input": 1, "output": 5, "cache_write": 1.25,
                    "cache_read": .1, "cache_write_1h": 2}
    def meter(usable=True):
        return {"usable": usable, "points_per_million_tokens": {"f": coefficients}}
    model = {"version": 1, "model_families": {"claude-fable-test": "f",
             "claude-sonnet-test": "f", "gpt-test": "f"}, "accounts": {
        "large": {"provider": "claude", "meters": {"weekly_fable": meter(),
                    "weekly_non_fable": meter(False)}},
        "small": {"provider": "claude", "meters": {"weekly_fable": meter()}},
        "codex": {"provider": "codex", "meters": {"weekly_all": meter()}}}}
    policy = {"sources": [{"provider": "claude", "email": "large", "weight": 1},
                          {"provider": "claude", "email": "small", "weight": .1},
                          {"provider": "codex", "email": "codex", "weight": 1/7}]}
    return model, policy


class TokenCostModelTests(unittest.TestCase):
    def setUp(self):
        self.model, self.policy = fixtures()

    def estimate(self, records, **kwargs):
        return estimate_points(records, model=self.model, policy=self.policy, **kwargs)

    def test_pooling_uses_category_units_and_configured_weights(self):
        rows = [{"account": a, "model": "claude-fable-test", "input": 1_000_000}
                for a in ("large", "small")]
        self.assertAlmostEqual(self.estimate(rows)["fable"], 1.1)
        self.policy["sources"][1]["weight"] = .2
        self.assertAlmostEqual(self.estimate(rows)["fable"], 1.2)

    def test_raw_codex_cached_and_reasoning_tokens_are_not_double_counted(self):
        row = {"account": "codex", "model": "gpt-test", "input_tokens": 1_000_000,
               "cached_input_tokens": 800_000, "output_tokens": 20_000,
               "reasoning_output_tokens": 10_000}
        self.assertAlmostEqual(self.estimate([row])["codex"], (.2 + .08 + .1)/7)

    def test_canonical_codex_input_is_already_uncached(self):
        row = {"account": "codex", "model": "gpt-test", "input": 200_000,
               "cache_read": 800_000}
        self.assertAlmostEqual(self.estimate([row])["codex"], .28/7)

    def test_claude_cache_ttls_and_input_are_disjoint(self):
        row = {"email": "large", "model": "claude-fable-test", "input_tokens": 100,
               "cache_creation_input_tokens": 1000, "cache_read_input_tokens": 10_000,
               "cache_creation": {"ephemeral_1h_input_tokens": 400}, "output_tokens": 200}
        self.assertAlmostEqual(self.estimate([row])["fable"], (100+750+800+1000+1000)/1e6)

    def test_unusable_or_missing_model_propagates_none_per_category(self):
        rows = [{"account": "large", "model": "claude-sonnet-test", "input": 20},
                {"account": "large", "model": "claude-fable-test", "input": 1_000_000},
                {"account": "small", "model": "claude-fable-unknown", "input": 10}]
        self.assertEqual(self.estimate(rows), {"fable": None, "non-fable": None, "codex": 0})

    def test_missing_account_or_pooling_weight_never_becomes_zero(self):
        row = {"account": "other", "model": "claude-fable-test", "input": 10}
        self.assertIsNone(self.estimate([row])["fable"])
        self.policy["sources"] = []
        self.assertIsNone(self.estimate([{**row, "account": "large"}])["fable"])

    def test_empty_input_is_zero_but_missing_files_are_unknown(self):
        self.assertEqual(self.estimate([]), dict.fromkeys(("fable", "non-fable", "codex"), 0))
        self.assertEqual(estimate_points([], model_path="/no/such/token-cost-model.json"),
                         dict.fromkeys(("fable", "non-fable", "codex")))

    def test_invalid_counts_are_rejected(self):
        for value in [-1, float("nan"), float("inf"), "12", True]:
            with self.subTest(value=value), self.assertRaises(ValueError):
                self.estimate([{"account": "large", "model": "claude-fable-test", "input": value}])
        with self.assertRaises(ValueError):
            self.estimate([{"account": "codex", "model": "gpt-test", "input_tokens": 1,
                            "cached_input_tokens": 2}])

    def test_does_not_mutate_input_or_model(self):
        before = copy.deepcopy(self.model)
        row = {"account": "large", "model": "claude-fable-test", "input": 5}
        self.estimate([row])
        self.assertEqual(row["input"], 5)
        self.assertEqual(self.model, before)

    def test_shipped_model_has_consistent_usable_contract(self):
        import json
        model = json.loads(Path(__file__).with_name("token_cost_model.json").read_text())
        for account in model["accounts"].values():
            for meter in account["meters"].values():
                self.assertIsInstance(meter["usable"], bool)
                if meter["usable"]:
                    self.assertTrue(meter["points_per_million_tokens"])
                    self.assertIsNotNone(meter["validation"]["actual_points"])


if __name__ == "__main__":
    unittest.main()
