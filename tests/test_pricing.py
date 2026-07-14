import unittest

from ki_council.pricing import (
    SOURCE_CONFIG,
    SOURCE_LIVE,
    SOURCE_LOCAL,
    SOURCE_TABLE,
    SOURCE_UNKNOWN,
    _match_table,
    estimate_cost_usd,
    lookup_price,
    parse_live_prices,
    parse_price_overrides,
    resolve_price,
)


def _models_dev_payload():
    """A models.dev-shaped payload: the same model sold by several providers."""
    return {
        "anthropic": {
            "models": {"claude-opus-4-8": {"cost": {"input": 5, "output": 25}}}
        },
        "openai": {
            "models": {
                "gpt-5.4-mini": {"cost": {"input": 0.75, "output": 4.5}},
                "some-image-model": {"cost": {"input": 1.0}},  # no output price
            }
        },
        "google": {
            "models": {"gemini-3.5-flash": {"cost": {"input": 1.5, "output": 9}}}
        },
        # Resellers of the same Anthropic model, at their own prices.
        "venice": {
            "models": {"claude-opus-4-8": {"cost": {"input": 6, "output": 30}}}
        },
        "kenari": {
            "models": {"claude-opus-4-8": {"cost": {"input": 0, "output": 0}}}
        },
    }


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

    def test_newer_version_never_inherits_a_neighbours_price(self):
        # The error class: a prefix match that crosses a version boundary
        # returns a confident, wrong price instead of "unknown". A wrong cost
        # verdict is this tool's worst possible failure, so unknown wins.
        # Tested against a controlled table so it keeps holding for the next
        # generation of models, not just the ones the real table happens to
        # know today.
        table = {
            "gpt-5": (1.25, 10.00),
            "claude-opus-4": (15.00, 75.00),
            "gemini-2.5-flash": (0.30, 2.50),
            "o3": (2.00, 8.00),
        }
        for model in (
            "gpt-5.4-mini",      # a different model, not a variant of gpt-5
            "gpt-5.6-terra",
            "claude-opus-4-8",   # must not inherit claude-opus-4
            "gemini-2.5-flash-lite",
            "o3-pro",
        ):
            with self.subTest(model=model):
                self.assertIsNone(_match_table(model, table))

    def test_dated_snapshots_still_match_their_base_entry(self):
        table = {"gpt-5": (1.25, 10.00)}
        for model in ("gpt-5", "gpt-5-20250807", "gpt-5-2025-08-07", "gpt-5-latest"):
            with self.subTest(model=model):
                self.assertEqual(_match_table(model, table), (1.25, 10.00))

    def test_remote_host_containing_localhost_is_not_free(self):
        # Substring matching on the URL would price a paid endpoint at zero.
        self.assertIsNone(
            lookup_price("some-model", base_url="https://localhost.example.com/v1")
        )

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


class LivePriceTests(unittest.TestCase):
    def test_only_the_first_party_price_is_used(self):
        # The trap: models.dev lists claude-opus-4-8 under 16 providers, one of
        # them at 0/0. Taking a reseller's number would make a paid model look
        # free and hand out a wrong cost verdict.
        prices = parse_live_prices(_models_dev_payload())
        self.assertEqual(prices["claude-opus-4-8"], (5.0, 25.0))

    def test_first_party_models_are_extracted(self):
        prices = parse_live_prices(_models_dev_payload())
        self.assertEqual(prices["gpt-5.4-mini"], (0.75, 4.5))
        self.assertEqual(prices["gemini-3.5-flash"], (1.5, 9.0))

    def test_entries_without_a_token_price_are_skipped(self):
        self.assertNotIn("some-image-model", parse_live_prices(_models_dev_payload()))

    def test_garbage_payload_yields_no_prices(self):
        for payload in (None, [], "nonsense", {"openai": {"models": None}}):
            with self.subTest(payload=payload):
                self.assertEqual(parse_live_prices(payload), {})


class ResolvePriceTests(unittest.TestCase):
    def setUp(self):
        self.live = parse_live_prices(_models_dev_payload())

    def test_config_override_beats_live_source(self):
        info = resolve_price(
            "claude-opus-4-8", overrides={"claude-opus-4-8": (1.0, 2.0)}, live=self.live
        )
        self.assertEqual(info, ((1.0, 2.0), SOURCE_CONFIG))

    def test_live_source_beats_bundled_table(self):
        # gpt-4o is in the bundled table; a live price must take precedence.
        live = {"gpt-4o": (9.0, 9.0)}
        self.assertEqual(resolve_price("gpt-4o", live=live), ((9.0, 9.0), SOURCE_LIVE))

    def test_table_is_used_when_live_lacks_the_model(self):
        info = resolve_price("gpt-4o-mini", live=self.live)
        self.assertEqual(info, ((0.15, 0.60), SOURCE_TABLE))

    def test_local_endpoint_is_free_and_labelled(self):
        info = resolve_price("llama3", base_url="http://localhost:11434/v1")
        self.assertEqual(info, ((0.0, 0.0), SOURCE_LOCAL))

    def test_unknown_model_is_reported_as_unknown(self):
        info = resolve_price("model-from-the-future", live=self.live)
        self.assertEqual(info, (None, SOURCE_UNKNOWN))


if __name__ == "__main__":
    unittest.main()
