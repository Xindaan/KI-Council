import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from ki_council.clients import LLMResponse
from ki_council.evaluate import (
    Candidate,
    EvalConfigError,
    build_candidates,
    build_pairwise_prompt,
    combine_orientations,
    is_conclusive,
    load_judges,
    majority_vote,
    parse_verdict,
    pick_baseline,
    prompts_needed_for,
    render_report,
    result_summary,
    run_eval,
    wilson_interval,
)
from ki_council.promptsets import PromptItem


class ParseVerdictTests(unittest.TestCase):
    def test_plain_letters_and_tie(self):
        self.assertEqual(parse_verdict("Reasoning here.\nA"), "A")
        self.assertEqual(parse_verdict("Reasoning here.\nB"), "B")
        self.assertEqual(parse_verdict("Reasoning here.\nTIE"), "TIE")

    def test_markdown_and_label_decorations(self):
        self.assertEqual(parse_verdict("Both fine.\n**Verdict: A**"), "A")
        self.assertEqual(parse_verdict("Close call.\n- tie"), "TIE")

    def test_trailing_blank_lines(self):
        self.assertEqual(parse_verdict("Solid answer.\nB\n\n  \n"), "B")

    def test_garbage_returns_none(self):
        self.assertIsNone(parse_verdict("I cannot decide between them."))
        self.assertIsNone(parse_verdict(""))


class VoteLogicTests(unittest.TestCase):
    def test_confirmed_in_both_orientations(self):
        # Candidate is B in orientation 1 and A in orientation 2
        self.assertEqual(combine_orientations("B", "A"), "win")
        self.assertEqual(combine_orientations("A", "B"), "loss")

    def test_real_tie_is_a_tie(self):
        self.assertEqual(combine_orientations("TIE", "TIE"), "tie")

    def test_self_contradiction_is_kept_apart_from_a_real_tie(self):
        # The judge picked the same position both times: it is reading position,
        # not quality. Still scored as a tie, but it must be visible as its own
        # thing so a position-blind judge cannot masquerade as "they're equal".
        self.assertEqual(combine_orientations("B", "B"), "inconsistent")
        self.assertEqual(combine_orientations("A", "A"), "inconsistent")
        self.assertEqual(combine_orientations("TIE", "A"), "inconsistent")

    def test_unusable_verdict_is_not_a_tie(self):
        # A tie counts towards the downgrade threshold. A broken judge must not
        # earn the candidate a pass by failing.
        self.assertEqual(combine_orientations(None, "A"), "unknown")
        self.assertEqual(combine_orientations("A", None), "unknown")
        self.assertEqual(combine_orientations(None, None), "unknown")

    def test_majority_vote(self):
        self.assertEqual(majority_vote(["win", "win", "loss"]), "win")
        self.assertEqual(majority_vote(["loss", "loss", "tie"]), "loss")
        self.assertEqual(majority_vote(["win", "loss", "tie"]), "tie")
        self.assertEqual(majority_vote(["win"]), "win")

    def test_unknown_judges_do_not_get_a_say(self):
        # One judge failed, the other two agree: their verdict stands.
        self.assertEqual(majority_vote(["win", "win", "unknown"]), "win")
        # Every judge failed: the pair has no verdict and must drop out.
        self.assertEqual(majority_vote(["unknown", "unknown"]), "unknown")


class WilsonIntervalTests(unittest.TestCase):
    def test_flawless_small_sample_is_still_uncertain(self):
        # The whole point: 25/25 looks like certainty but is not. If this bound
        # ever reads >= 0.9, the report would start calling thin runs conclusive.
        low, high = wilson_interval(25, 25)
        self.assertAlmostEqual(low, 0.866, places=2)
        self.assertEqual(high, 1.0)

    def test_interval_narrows_as_the_sample_grows(self):
        small = wilson_interval(50, 50)
        large = wilson_interval(500, 500)
        self.assertLess(small[0], large[0])

    def test_empty_sample_says_nothing(self):
        self.assertEqual(wilson_interval(0, 0), (0.0, 1.0))

    def test_required_sample_size_matches_the_bound(self):
        # The number the report quotes must actually be sufficient.
        needed = prompts_needed_for(0.9)
        self.assertGreaterEqual(wilson_interval(needed, needed)[0], 0.9)
        self.assertLess(wilson_interval(needed - 1, needed - 1)[0], 0.9)


@mock.patch.dict("os.environ", {}, clear=True)
class CandidateConfigTests(unittest.TestCase):
    def test_explicit_candidates_with_key_fallback_and_local_endpoint(self):
        config = {
            "openai_api_key": "sk-test",
            "eval_candidates": [
                {"name": "big", "provider": "openai", "model": "gpt-4o"},
                {"name": "local", "provider": "openai", "model": "llama3.1:8b",
                 "base_url": "http://localhost:11434/v1"},
            ],
        }
        candidates = build_candidates(config)
        self.assertEqual(candidates[0].api_key, "sk-test")
        self.assertEqual(candidates[1].api_key, "local")  # placeholder for local endpoints
        self.assertEqual(candidates[1].price, (0.0, 0.0))  # localhost heuristic

    def test_explicit_price_beats_table(self):
        config = {
            "openai_api_key": "sk-test",
            "eval_candidates": [
                {"name": "a", "provider": "openai", "model": "gpt-4o",
                 "price_input": 1.0, "price_output": 2.0},
                {"name": "b", "provider": "openai", "model": "gpt-4o-mini"},
            ],
        }
        self.assertEqual(build_candidates(config)[0].price, (1.0, 2.0))

    def test_live_prices_are_used_and_their_source_is_recorded(self):
        # Injected, never fetched: tests must not touch the network.
        config = {
            "openai_api_key": "sk-test",
            "eval_candidates": [
                {"name": "new", "provider": "openai", "model": "model-released-yesterday"},
                {"name": "known", "provider": "openai", "model": "gpt-4o-mini"},
            ],
        }
        live = {"model-released-yesterday": (1.0, 8.0)}
        candidates = build_candidates(config, live)
        self.assertEqual((candidates[0].price, candidates[0].price_source), ((1.0, 8.0), "live"))
        self.assertEqual(candidates[1].price_source, "table")

    def test_model_unknown_everywhere_has_no_price(self):
        # It must stay unpriced rather than borrow a neighbour's price: a blank
        # cost is honest, a wrong one silently corrupts the verdict.
        config = {
            "openai_api_key": "sk-test",
            "eval_candidates": [
                {"name": "mystery", "provider": "openai", "model": "gpt-9.9-ultra"},
                {"name": "known", "provider": "openai", "model": "gpt-4o-mini"},
            ],
        }
        candidate = build_candidates(config)[0]
        self.assertIsNone(candidate.price)
        self.assertEqual(candidate.price_source, "unknown")

    def test_duplicate_names_rejected(self):
        config = {
            "openai_api_key": "sk-test",
            "eval_candidates": [
                {"name": "same", "provider": "openai", "model": "gpt-4o"},
                {"name": "same", "provider": "openai", "model": "gpt-4o-mini"},
            ],
        }
        with self.assertRaises(EvalConfigError):
            build_candidates(config)

    def test_unknown_provider_rejected(self):
        config = {"eval_candidates": [{"provider": "mistral", "model": "x", "api_key": "k"},
                                      {"provider": "openai", "model": "y", "api_key": "k"}]}
        with self.assertRaises(EvalConfigError):
            build_candidates(config)

    def test_missing_key_without_base_url_rejected(self):
        config = {"eval_candidates": [{"provider": "openai", "model": "gpt-4o"},
                                      {"provider": "openai", "model": "gpt-4o-mini"}]}
        with self.assertRaises(EvalConfigError):
            build_candidates(config)

    def test_fallback_to_council_providers(self):
        config = {"openai_api_key": "sk-1", "anthropic_api_key": "sk-2",
                  "openai_model": "gpt-4o", "anthropic_model": "claude-3-5-haiku-20241022"}
        candidates = build_candidates(config)
        self.assertEqual({c.name for c in candidates}, {"openai", "anthropic"})

    def test_fewer_than_two_candidates_rejected(self):
        with self.assertRaises(EvalConfigError):
            build_candidates({"openai_api_key": "sk-1"})

    def test_baseline_explicit_and_default_most_expensive(self):
        config = {
            "openai_api_key": "sk-test",
            "eval_candidates": [
                {"name": "cheap", "provider": "openai", "model": "gpt-4o-mini"},
                {"name": "big", "provider": "openai", "model": "gpt-4o"},
            ],
        }
        candidates = build_candidates(config)
        self.assertEqual(pick_baseline(candidates, "cheap", config).name, "cheap")
        self.assertEqual(pick_baseline(candidates, None, config).name, "big")
        with self.assertRaises(EvalConfigError):
            pick_baseline(candidates, "nonexistent", config)

    def test_default_judge_from_council_config(self):
        judges = load_judges({"openai_api_key": "sk-test", "judge_model": "gpt-4o-mini"})
        self.assertEqual(len(judges), 1)
        self.assertEqual(judges[0].model, "gpt-4o-mini")

    def test_no_judge_configured_rejected(self):
        with self.assertRaises(EvalConfigError):
            load_judges({})


def _candidate(name, price):
    return Candidate(name=name, provider="openai", model=f"model-{name}", api_key="sk", price=price)


def _make_generate(answers):
    """Fake generation: fixed sentinel answer + fixed token usage per candidate."""
    def generate(candidate, prompt, max_tokens):
        answer = answers[candidate.name]
        if answer is None:
            return LLMResponse(provider="openai", model=candidate.model, content="", error="boom")
        return LLMResponse(provider="openai", model=candidate.model, content=answer,
                           tokens_prompt=100, tokens_completion=200)
    return generate


def _make_quality_judge(quality):
    """Fake judge: reads the anonymized A/B sections and votes for higher quality."""
    def judge(judge_candidate, judge_prompt):
        section_a = judge_prompt.split("[Response A]")[1].split("[Response B]")[0]
        section_b = judge_prompt.split("[Response B]")[1]
        score_a = next((s for key, s in quality.items() if key in section_a), 0)
        score_b = next((s for key, s in quality.items() if key in section_b), 0)
        if score_a > score_b:
            return "Response A is better.\nA"
        if score_b > score_a:
            return "Response B is better.\nB"
        return "Both are comparable.\nTIE"
    return judge


class RunEvalTests(unittest.TestCase):
    def setUp(self):
        self.baseline = _candidate("big", (10.0, 30.0))
        self.cheap = _candidate("cheap", (0.1, 0.4))
        self.bad = _candidate("bad", (0.05, 0.2))
        self.candidates = [self.baseline, self.cheap, self.bad]
        self.judges = [_candidate("judge", None)]
        self.prompts = [PromptItem(id=f"p{i}", text=f"Question number {i}?", source="test")
                        for i in range(4)]
        self.answers = {"big": "BASELINE_ANSWER", "cheap": "CHEAP_ANSWER", "bad": "BAD_ANSWER"}
        self.quality = {"BASELINE_ANSWER": 5, "CHEAP_ANSWER": 5, "BAD_ANSWER": 1}

    def _run(self, out_dir=None, judge_fn=None, answers=None, threshold=0.9):
        return run_eval(
            self.prompts, self.candidates, self.judges, self.baseline,
            threshold=threshold, out_dir=out_dir,
            generate_fn=_make_generate(answers or self.answers),
            judge_fn=judge_fn or _make_quality_judge(self.quality),
        )

    def test_recommends_cheapest_qualifying_candidate(self):
        result = self._run()
        by_name = {entry.candidate.name: entry for entry in result.candidates}
        self.assertEqual(by_name["cheap"].ties, 4)  # equal quality -> ties
        self.assertEqual(by_name["bad"].losses, 4)
        self.assertEqual(by_name["cheap"].win_or_tie_rate, 1.0)
        self.assertEqual(by_name["bad"].win_or_tie_rate, 0.0)
        # bad is cheaper than cheap but fails the threshold -> cheap wins the verdict
        self.assertEqual(result.recommendation.candidate.name, "cheap")

    def test_broken_judge_does_not_hand_out_a_downgrade(self):
        # The attack this guards against: a judge whose output never parses used
        # to degrade to "tie" on every pair, ties count towards the threshold, so
        # the cheapest model sailed through and got recommended. Now those pairs
        # leave the sample and no verdict is possible.
        result = self._run(judge_fn=lambda judge, prompt: "I cannot decide.")
        by_name = {entry.candidate.name: entry for entry in result.candidates}
        self.assertEqual(by_name["bad"].judged, 0)
        self.assertEqual(by_name["bad"].unknown, 4)
        self.assertIsNone(by_name["bad"].win_or_tie_rate)
        self.assertIsNone(result.recommendation)

    def test_position_blind_judge_is_reported_as_inconsistent(self):
        # A judge that always picks whatever sits in slot A contradicts itself
        # once the responses are swapped. It scores as a tie, but is counted.
        result = self._run(judge_fn=lambda judge, prompt: "The first one.\nA")
        by_name = {entry.candidate.name: entry for entry in result.candidates}
        self.assertEqual(by_name["cheap"].ties, 4)
        self.assertEqual(by_name["cheap"].inconsistent, 4)

    def test_verdict_on_a_small_sample_is_not_conclusive(self):
        # 4 prompts, all ties: the observed rate is 100%, but the interval runs
        # far below the 90% bar. The recommendation stands, flagged as such.
        result = self._run()
        cheap = next(e for e in result.candidates if e.candidate.name == "cheap")
        self.assertEqual(cheap.win_or_tie_rate, 1.0)
        self.assertFalse(is_conclusive(cheap, result.threshold))
        self.assertLess(cheap.win_or_tie_ci[0], 0.9)
        self.assertEqual(result.recommendation.candidate.name, "cheap")

    def test_tie_share_exposes_a_verdict_built_on_ties(self):
        result = self._run()
        cheap = next(e for e in result.candidates if e.candidate.name == "cheap")
        self.assertEqual(cheap.tie_share, 1.0)  # every pass came from a tie

    def test_cost_math(self):
        result = self._run()
        # 100 prompt tokens * $10/M + 200 completion tokens * $30/M = $0.007 per prompt
        self.assertAlmostEqual(result.baseline.avg_cost_per_prompt, 0.007)

    def test_position_biased_judge_is_neutralized_by_swap(self):
        result = self._run(judge_fn=lambda judge, prompt: "Position A looks great.\nA")
        by_name = {entry.candidate.name: entry for entry in result.candidates}
        self.assertEqual(by_name["cheap"].wins, 0)
        self.assertEqual(by_name["cheap"].ties, 4)
        self.assertEqual(by_name["bad"].losses, 0)

    def test_candidate_error_counts_as_loss(self):
        answers = dict(self.answers, cheap=None)
        result = self._run(answers=answers)
        by_name = {entry.candidate.name: entry for entry in result.candidates}
        self.assertEqual(by_name["cheap"].losses, 4)
        self.assertEqual(by_name["cheap"].errors, 4)
        self.assertIsNone(result.recommendation)

    def test_baseline_error_skips_prompts(self):
        answers = dict(self.answers, big=None)
        result = self._run(answers=answers)
        self.assertEqual(result.prompts_skipped, 4)
        self.assertEqual(result.prompts_judged, 0)

    def test_artifacts_written(self):
        with tempfile.TemporaryDirectory() as tmp:
            out_dir = Path(tmp) / "run"
            self._run(out_dir=out_dir)
            responses = (out_dir / "responses.jsonl").read_text().strip().splitlines()
            judgments = (out_dir / "judgments.jsonl").read_text().strip().splitlines()
            self.assertEqual(len(responses), 4 * 3)  # prompts x candidates
            self.assertEqual(len(judgments), 4 * 2)  # prompts x non-baseline candidates
            record = json.loads(judgments[0])
            self.assertIn(record["vote"], {"win", "tie", "loss"})

    def test_report_and_summary(self):
        result = self._run()
        report = render_report(result, generated_at="2026-07-13 12:00")
        self.assertIn("Switch to cheap", report)
        self.assertIn("| big (baseline) |", report)
        self.assertIn("**<- verdict**", report)
        summary = result_summary(result)
        self.assertEqual(summary["recommendation"], "cheap")
        self.assertEqual(summary["prompts_judged"], 4)

    def test_no_recommendation_when_threshold_not_met(self):
        # Degrade cheap's quality so it loses everywhere -> nothing qualifies
        quality = {"BASELINE_ANSWER": 5, "CHEAP_ANSWER": 1, "BAD_ANSWER": 1}
        result = self._run(judge_fn=_make_quality_judge(quality))
        self.assertIsNone(result.recommendation)
        report = render_report(result)
        self.assertIn("No downgrade recommended", report)


class PairwisePromptTests(unittest.TestCase):
    def test_template_fills_all_placeholders(self):
        text = build_pairwise_prompt("The question", "Answer one", "Answer two")
        self.assertIn("The question", text)
        self.assertIn("Answer one", text)
        self.assertIn("Answer two", text)
        self.assertNotIn("{prompt}", text)


if __name__ == "__main__":
    unittest.main()
