import unittest

from ki_council.pricing import estimate_cost_usd, lookup_price, parse_price_overrides


class LookupPriceTests(unittest.TestCase):
    def test_exact_match(self):
        self.assertEqual(lookup_price("gpt-4o-mini"), (0.15, 0.60))

    def test_longest_prefix_wins_over_shorter(self):
        # "gpt-4o-mini-2024-07-18" must match gpt-4o-mini, not gpt-4o
        self.assertEqual(lookup_price("gpt-4o-mini-2024-07-18"), (0.15, 0.60))

    def test_dated_anthropic_snapshot_matches_base(self):
        self.assertEqual(lookup_price("claude-3-5-haiku-20241022"), (0.80, 4.00))

    def test_models_path_prefix_is_stripped(self):
        self.assertEqual(lookup_price("models/gemini-1.5-flash"), (0.075, 0.30))

    def test_unknown_model_returns_none(self):
        self.assertIsNone(lookup_price("totally-unknown-model"))

    def test_override_beats_default_table(self):
        overrides = {"gpt-4o-mini": (1.0, 2.0)}
        self.assertEqual(lookup_price("gpt-4o-mini", overrides), (1.0, 2.0))

    def test_local_base_url_is_free(self):
        self.assertEqual(
            lookup_price("llama3.1:8b", base_url="http://localhost:11434/v1"),
            (0.0, 0.0),
        )

    def test_override_beats_local_heuristic(self):
        overrides = {"llama3.1:8b": (0.5, 0.5)}
        self.assertEqual(
            lookup_price("llama3.1:8b", overrides, base_url="http://localhost:11434/v1"),
            (0.5, 0.5),
        )


class ParseOverridesTests(unittest.TestCase):
    def test_valid_and_invalid_entries(self):
        config = {"model_prices": {"my-model": [1, 2], "broken": "cheap", "also-broken": [1]}}
        self.assertEqual(parse_price_overrides(config), {"my-model": (1.0, 2.0)})

    def test_missing_key_returns_empty(self):
        self.assertEqual(parse_price_overrides({}), {})


class EstimateCostTests(unittest.TestCase):
    def test_cost_calculation(self):
        # 1000 prompt tokens at $2/M + 500 completion tokens at $10/M
        cost = estimate_cost_usd(1000, 500, (2.0, 10.0))
        self.assertAlmostEqual(cost, 0.007)

    def test_missing_inputs_return_none(self):
        self.assertIsNone(estimate_cost_usd(None, 500, (2.0, 10.0)))
        self.assertIsNone(estimate_cost_usd(1000, None, (2.0, 10.0)))
        self.assertIsNone(estimate_cost_usd(1000, 500, None))


if __name__ == "__main__":
    unittest.main()
