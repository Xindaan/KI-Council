"""Downgrade advisor: find the cheapest model that is good enough for YOUR prompts.

Runs a set of real prompts (files, folders, or ChatGPT/Claude history exports)
against several candidate models, compares every candidate against a baseline
model with blind pairwise LLM judging (anonymized, position-swapped, optional
multi-judge jury), and reports the cheapest candidate that wins or ties in at
least --threshold of the prompts -- including the estimated cost savings.

Usage:
    python -m ki_council.evaluate prompts.jsonl
    python -m ki_council.evaluate ~/Downloads/conversations.json --limit 25
    python -m ki_council.evaluate prompts/ --baseline gpt-4o --threshold 0.85

Candidates default to the configured council providers; define your own set
via "eval_candidates" in .ki-council.json (see README).
"""

import argparse
import json
import math
import logging
import re
import sys
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from datetime import datetime
from functools import lru_cache
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

from ki_council.clients import (
    AnthropicClient,
    GeminiClient,
    LLMResponse,
    OpenAIClient,
)
from ki_council.config import CONFIG_ENV_VAR, get_setting, load_config
from ki_council.classify import CLASSIFY_MAX_TOKENS, classify_prompts, segment_labels
from ki_council.pricing import (
    LIVE_PRICES_URL,
    SOURCE_CONFIG,
    SOURCE_TABLE,
    SOURCE_UNKNOWN,
    TABLE_VERIFIED,
    PriceInfo,
    estimate_cost_usd,
    fetch_live_prices,
    parse_price_overrides,
    resolve_price,
)
from ki_council.promptsets import PromptItem, load_prompts

logger = logging.getLogger(__name__)

DEFAULT_MAX_TOKENS = 1024
DEFAULT_THRESHOLD = 0.9
DEFAULT_WORKERS = 4
JUDGE_MAX_TOKENS = 1024

PROVIDER_KEY_SETTINGS = {
    "openai": ("OPENAI_API_KEY", "openai_api_key"),
    "gemini": ("GEMINI_API_KEY", "gemini_api_key"),
    "anthropic": ("ANTHROPIC_API_KEY", "anthropic_api_key"),
}


class EvalConfigError(RuntimeError):
    """Raised when the evaluation setup is invalid."""
    pass


@dataclass
class Candidate:
    """A model endpoint taking part in the evaluation (also used for judges)."""
    name: str
    provider: str  # openai | gemini | anthropic (openai covers any OpenAI-compatible API)
    model: str
    api_key: str
    base_url: Optional[str] = None
    price: Optional[Tuple[float, float]] = None  # USD per 1M tokens (input, output)
    price_source: str = SOURCE_UNKNOWN  # config | local | live | table | unknown


Z_95 = 1.96


def wilson_interval(successes: int, total: int, z: float = Z_95) -> Tuple[float, float]:
    """95% Wilson score interval for a proportion.

    Wilson rather than the textbook normal approximation: the rates here sit
    near 1.0 (a candidate that wins or ties almost always), where the normal
    approximation produces intervals reaching past 100% and understates the
    uncertainty badly at the small sample sizes this tool runs on.
    """
    if total <= 0:
        return (0.0, 1.0)
    p = successes / total
    denominator = 1 + z**2 / total
    centre = p + z**2 / (2 * total)
    spread = z * math.sqrt(p * (1 - p) / total + z**2 / (4 * total**2))
    low = (centre - spread) / denominator
    high = (centre + spread) / denominator
    return (max(0.0, low), min(1.0, high))


SAMPLE_SIZE_CAP = 100_000


def prompts_needed_for(
    threshold: float, rate: float = 1.0, z: float = Z_95
) -> Optional[int]:
    """How many judged prompts it would take for `rate` to clear `threshold`.

    Answers the question the report owes the reader when the evidence is thin:
    "how many prompts would settle this?" The answer depends on the rate you
    actually observed, not on a hypothetical perfect run -- at 92% against a 90%
    bar the sample has to grow far beyond what a flawless run would need,
    because the margin being defended is thin.

    Returns None when no sample would do it: a rate at or below the threshold
    can never have a lower bound above it, no matter how much data is added.
    """
    if rate <= threshold:
        return None
    low, high = 1, SAMPLE_SIZE_CAP
    if wilson_interval(round(rate * high), high, z)[0] < threshold:
        return None
    while low < high:
        mid = (low + high) // 2
        if wilson_interval(round(rate * mid), mid, z)[0] >= threshold:
            high = mid
        else:
            low = mid + 1
    return low


# Below this many judged pairs a segment says nothing: with 4 prompts even a
# flawless run has a Wilson lower bound around 0.5. Such a segment is reported
# as "too few prompts" rather than as a rate that invites over-reading.
MIN_SEGMENT_SAMPLE = 8


@dataclass
class SegmentStats:
    """One candidate's record within one kind of prompt (e.g. "coding")."""
    label: str
    wins: int = 0
    ties: int = 0
    losses: int = 0
    judged: int = 0

    @property
    def win_or_tie_rate(self) -> Optional[float]:
        if not self.judged:
            return None
        return (self.wins + self.ties) / self.judged

    @property
    def win_or_tie_ci(self) -> Optional[Tuple[float, float]]:
        if not self.judged:
            return None
        return wilson_interval(self.wins + self.ties, self.judged)

    @property
    def has_enough_data(self) -> bool:
        return self.judged >= MIN_SEGMENT_SAMPLE


@dataclass
class CandidateStats:
    """Aggregated evaluation outcome for one candidate."""
    candidate: Candidate
    wins: int = 0
    ties: int = 0
    losses: int = 0
    errors: int = 0
    judged: int = 0
    inconsistent: int = 0  # judge contradicted itself; scored as a tie
    unknown: int = 0  # no usable verdict; excluded from `judged`
    tokens_prompt: int = 0
    tokens_completion: int = 0
    responses_ok: int = 0
    segments: Dict[str, SegmentStats] = field(default_factory=dict)

    def weakest_segment(self, threshold: float) -> Optional[SegmentStats]:
        """The worst kind of prompt for this candidate, if the data supports one.

        This is the number the headline rate hides: strong everyday performance
        can carry a candidate over the bar while it fails the work the expensive
        model was kept for.
        """
        failing = [
            segment for segment in self.segments.values()
            if segment.has_enough_data
            and segment.win_or_tie_rate is not None
            and segment.win_or_tie_rate < threshold
        ]
        if not failing:
            return None
        return min(failing, key=lambda segment: segment.win_or_tie_rate)

    @property
    def win_or_tie_rate(self) -> Optional[float]:
        if not self.judged:
            return None
        return (self.wins + self.ties) / self.judged

    @property
    def tie_share(self) -> Optional[float]:
        """How much of the win-or-tie rate is carried by ties rather than wins.

        The threshold treats a tie as good enough, so a judge that ties on
        everything hands out downgrade recommendations for free. This is the
        number that exposes it.
        """
        if not self.judged:
            return None
        return self.ties / self.judged

    @property
    def win_or_tie_ci(self) -> Optional[Tuple[float, float]]:
        """95% Wilson interval around the win-or-tie rate."""
        if not self.judged:
            return None
        return wilson_interval(self.wins + self.ties, self.judged)

    @property
    def avg_cost_per_prompt(self) -> Optional[float]:
        if not self.responses_ok:
            return None
        cost = estimate_cost_usd(self.tokens_prompt, self.tokens_completion, self.candidate.price)
        if cost is None:
            return None
        return cost / self.responses_ok


@dataclass
class EvalResult:
    """Everything the report and summary.json are rendered from."""
    baseline: CandidateStats
    candidates: List[CandidateStats]
    prompts_total: int
    prompts_judged: int
    prompts_skipped: int
    judges: List[Candidate]
    threshold: float
    parse_failures: int = 0
    recommendation: Optional[CandidateStats] = None


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

def _provider_default_key(config: Dict[str, Any], provider: str) -> Optional[str]:
    settings = PROVIDER_KEY_SETTINGS.get(provider)
    if not settings:
        return None
    return get_setting(config, settings[0], settings[1])


def _candidate_from_entry(
    config: Dict[str, Any], entry: Dict[str, Any], overrides, live=None
) -> Candidate:
    provider = str(entry.get("provider", "openai")).strip().lower()
    if provider not in PROVIDER_KEY_SETTINGS:
        raise EvalConfigError(
            f"Unknown provider '{provider}' in eval_candidates (expected one of "
            f"{sorted(PROVIDER_KEY_SETTINGS)})"
        )
    model = str(entry.get("model", "")).strip()
    if not model:
        raise EvalConfigError(f"eval_candidates entry without 'model': {entry!r}")
    name = str(entry.get("name") or model).strip()
    base_url = entry.get("base_url")
    api_key = str(entry.get("api_key") or "").strip()
    if not api_key:
        if base_url:
            # Custom endpoint without explicit key: use a placeholder (fine for
            # Ollama etc.) instead of leaking the provider key to a foreign URL.
            api_key = "local"
        else:
            api_key = str(_provider_default_key(config, provider) or "").strip()
    if not api_key:
        raise EvalConfigError(
            f"No API key for candidate '{name}': set 'api_key' in the entry or "
            f"the {provider} provider key in the config."
        )
    if "price_input" in entry and "price_output" in entry:
        try:
            price_info = PriceInfo(
                (float(entry["price_input"]), float(entry["price_output"])), SOURCE_CONFIG
            )
        except (TypeError, ValueError):
            raise EvalConfigError(f"Invalid price_input/price_output for candidate '{name}'")
    else:
        price_info = resolve_price(model, overrides, base_url, live)
    return Candidate(
        name=name, provider=provider, model=model,
        api_key=api_key, base_url=base_url,
        price=price_info.price, price_source=price_info.source,
    )


def _fallback_candidates(config: Dict[str, Any], overrides, live=None) -> List[Candidate]:
    """Derive candidates from the regular council provider config."""
    defaults = [
        ("openai", "OPENAI_MODEL", "openai_model", "gpt-4o-mini"),
        ("gemini", "GEMINI_MODEL", "gemini_model", "gemini-1.5-flash"),
        ("anthropic", "ANTHROPIC_MODEL", "anthropic_model", "claude-3-haiku-20240307"),
    ]
    candidates = []
    for provider, model_env, model_cfg, model_default in defaults:
        key = _provider_default_key(config, provider)
        if not key:
            continue
        model = get_setting(config, model_env, model_cfg, model_default)
        base_url = None
        if provider == "openai":
            base_url = get_setting(config, "OPENAI_BASE_URL", "openai_base_url")
        price_info = resolve_price(model, overrides, base_url, live)
        candidates.append(Candidate(
            name=provider, provider=provider, model=model, api_key=key,
            base_url=base_url, price=price_info.price, price_source=price_info.source,
        ))
    return candidates


def build_candidates(
    config: Dict[str, Any], live: Optional[Dict[str, Tuple[float, float]]] = None
) -> List[Candidate]:
    """Build the candidate list from "eval_candidates" or the provider config.

    Pass `live` to supply prices from the live source; without it, prices come
    from the config and the bundled table only.
    """
    overrides = parse_price_overrides(config)
    entries = config.get("eval_candidates")
    if entries:
        if not isinstance(entries, list):
            raise EvalConfigError("'eval_candidates' must be a JSON array")
        candidates = [
            _candidate_from_entry(config, entry, overrides, live) for entry in entries
        ]
    else:
        candidates = _fallback_candidates(config, overrides, live)

    if len(candidates) < 2:
        raise EvalConfigError(
            "Need at least two candidates to compare. Configure provider API keys "
            "or an 'eval_candidates' list in .ki-council.json."
        )
    names = [candidate.name for candidate in candidates]
    duplicates = {name for name in names if names.count(name) > 1}
    if duplicates:
        raise EvalConfigError(f"Duplicate candidate name(s): {sorted(duplicates)} -- add distinct 'name' fields.")
    return candidates


def pick_baseline(candidates: List[Candidate], requested: Optional[str], config: Dict[str, Any]) -> Candidate:
    """Choose the baseline: explicit flag/config, else the priciest candidate."""
    name = requested or get_setting(config, "KI_COUNCIL_EVAL_BASELINE", "eval_baseline")
    if name:
        for candidate in candidates:
            if candidate.name == name:
                return candidate
        raise EvalConfigError(
            f"Baseline '{name}' is not a configured candidate "
            f"(candidates: {[c.name for c in candidates]})"
        )
    priced = [c for c in candidates if c.price is not None]
    if priced:
        baseline = max(priced, key=lambda c: c.price[1])
        logger.info(f"No baseline configured; using most expensive candidate '{baseline.name}'")
        return baseline
    logger.info(f"No baseline configured and no prices known; using first candidate '{candidates[0].name}'")
    return candidates[0]


def load_judges(config: Dict[str, Any]) -> List[Candidate]:
    """Load the judge jury from "eval_judges", else fall back to the council judge."""
    overrides = parse_price_overrides(config)
    entries = config.get("eval_judges")
    if entries:
        if not isinstance(entries, list):
            raise EvalConfigError("'eval_judges' must be a JSON array")
        return [_candidate_from_entry(config, entry, overrides) for entry in entries]

    judge_key = (
        get_setting(config, "JUDGE_API_KEY", "judge_api_key")
        or get_setting(config, "OPENAI_API_KEY", "openai_api_key")
    )
    if not judge_key:
        raise EvalConfigError(
            "No judge configured. Set 'eval_judges' in the config, or JUDGE_API_KEY/"
            "OPENAI_API_KEY for the default judge."
        )
    judge_model = get_setting(config, "JUDGE_MODEL", "judge_model", "gpt-4o-mini")
    judge_base_url = get_setting(
        config, "JUDGE_BASE_URL", "judge_base_url",
        get_setting(config, "OPENAI_BASE_URL", "openai_base_url", "https://api.openai.com/v1"),
    )
    return [Candidate(
        name=f"judge:{judge_model}", provider="openai", model=judge_model,
        api_key=judge_key, base_url=judge_base_url,
    )]


def make_client(candidate: Candidate):
    """Instantiate the right API client for a candidate."""
    if candidate.provider == "gemini":
        return GeminiClient(candidate.api_key, candidate.model)
    if candidate.provider == "anthropic":
        return AnthropicClient(candidate.api_key, candidate.model)
    base_url = candidate.base_url or "https://api.openai.com/v1"
    return OpenAIClient(candidate.api_key, candidate.model, base_url)


# ---------------------------------------------------------------------------
# Judging
# ---------------------------------------------------------------------------

@lru_cache(maxsize=1)
def _load_pairwise_template() -> str:
    base_path = Path(__file__).parent
    local = base_path / "pairwise_judge_prompt.txt.local"
    if local.exists():
        logger.info(f"Using custom pairwise judge prompt from {local}")
        return local.read_text(encoding="utf-8")
    return (base_path / "pairwise_judge_prompt.txt").read_text(encoding="utf-8")


def build_pairwise_prompt(prompt: str, response_a: str, response_b: str) -> str:
    return _load_pairwise_template().format(
        prompt=prompt, response_a=response_a, response_b=response_b
    )


def parse_verdict(judge_output: str) -> Optional[str]:
    """Extract 'A', 'B', or 'TIE' from the last non-empty line of judge output."""
    for line in reversed(judge_output.strip().splitlines()):
        line = line.strip()
        if not line:
            continue
        tokens = re.sub(r"[^A-Z]+", " ", line.upper()).split()
        if tokens and tokens[-1] in {"A", "B", "TIE"}:
            return tokens[-1]
        return None
    return None


def combine_orientations(verdict_ab: Optional[str], verdict_ba: Optional[str]) -> str:
    """Combine both judging orientations into one debiased vote for the candidate.

    Orientation 1 shows the candidate as B, orientation 2 as A. Only a verdict
    confirmed in both orientations counts as a win or a loss.

    Three outcomes that used to collapse into "tie" are kept apart, because a
    tie counts towards the downgrade threshold and must be earned:

    - "tie":          the judge said TIE in both orientations. A real verdict.
    - "inconsistent": the judge contradicted itself, i.e. it picked the same
                      *position* both times. Scored as a tie (the standard
                      remedy for position bias), but counted separately: a run
                      full of these means the judge cannot tell the responses
                      apart, not that they are equally good.
    - "unknown":      the judge failed or its output was unparseable. Not a
                      verdict at all, so it must not be scored as one.
    """
    if verdict_ab is None or verdict_ba is None:
        return "unknown"
    if verdict_ab == "B" and verdict_ba == "A":
        return "win"
    if verdict_ab == "A" and verdict_ba == "B":
        return "loss"
    if verdict_ab == "TIE" and verdict_ba == "TIE":
        return "tie"
    return "inconsistent"


def majority_vote(votes: List[str]) -> str:
    """Strict-majority jury decision over the judges' votes.

    Judges that returned nothing usable ("unknown") do not get a say; if none
    of them did, the pair itself is unknown and drops out of the sample rather
    than silently scoring as a tie.
    """
    usable = [vote for vote in votes if vote != "unknown"]
    if not usable:
        return "unknown"
    wins = usable.count("win")
    losses = usable.count("loss")
    if wins > len(usable) / 2:
        return "win"
    if losses > len(usable) / 2:
        return "loss"
    return "tie"


def _default_generate(candidate: Candidate, prompt: str, max_tokens: int) -> LLMResponse:
    try:
        return make_client(candidate).generate(prompt, max_tokens=max_tokens)
    except Exception as exc:
        logger.warning(f"Candidate '{candidate.name}' failed: {exc}")
        return LLMResponse(provider=candidate.provider, model=candidate.model, content="", error=str(exc))


def _default_judge(judge: Candidate, judge_prompt: str) -> str:
    return make_client(judge).generate(judge_prompt, max_tokens=JUDGE_MAX_TOKENS).content


# ---------------------------------------------------------------------------
# Evaluation run
# ---------------------------------------------------------------------------

def run_eval(
    prompts: List[PromptItem],
    candidates: List[Candidate],
    judges: List[Candidate],
    baseline: Candidate,
    max_tokens: int = DEFAULT_MAX_TOKENS,
    threshold: float = DEFAULT_THRESHOLD,
    workers: int = DEFAULT_WORKERS,
    out_dir: Optional[Path] = None,
    generate_fn: Callable[[Candidate, str, int], LLMResponse] = _default_generate,
    judge_fn: Callable[[Candidate, str], str] = _default_judge,
    segment_of: Optional[Dict[str, str]] = None,
) -> EvalResult:
    """Run the full evaluation: generate, judge pairwise, aggregate.

    `segment_of` maps a prompt id to the kind of prompt it is (see classify.py).
    Given it, the result carries a per-segment breakdown alongside the overall
    rate, which is what keeps a good average from hiding a bad segment.
    """
    segment_of = segment_of or {}
    responses_log = out_dir / "responses.jsonl" if out_dir else None
    judgments_log = out_dir / "judgments.jsonl" if out_dir else None
    if out_dir:
        out_dir.mkdir(parents=True, exist_ok=True)

    stats = {candidate.name: CandidateStats(candidate=candidate) for candidate in candidates}

    # Phase 1: generate all responses in parallel.
    logger.info(f"Phase 1: generating {len(prompts)} prompt(s) x {len(candidates)} candidate(s)")
    responses: Dict[Tuple[str, str], LLMResponse] = {}
    with ThreadPoolExecutor(max_workers=workers) as executor:
        futures = {
            executor.submit(generate_fn, candidate, item.text, max_tokens): (item, candidate)
            for item in prompts
            for candidate in candidates
        }
        for future in as_completed(futures):
            item, candidate = futures[future]
            response = future.result()
            responses[(item.id, candidate.name)] = response
            entry = stats[candidate.name]
            if response.error:
                entry.errors += 1
            else:
                entry.responses_ok += 1
                entry.tokens_prompt += response.tokens_prompt or 0
                entry.tokens_completion += response.tokens_completion or 0
            if responses_log:
                _append_jsonl(responses_log, {
                    "prompt_id": item.id,
                    "candidate": candidate.name,
                    "model": candidate.model,
                    "content": response.content,
                    "error": response.error,
                    "tokens_prompt": response.tokens_prompt,
                    "tokens_completion": response.tokens_completion,
                })

    # Phase 2: blind pairwise judging, both orientations, full jury.
    others = [candidate for candidate in candidates if candidate.name != baseline.name]
    pairs_to_judge = []
    prompts_skipped = 0
    parse_failures = 0
    for item in prompts:
        baseline_response = responses[(item.id, baseline.name)]
        if baseline_response.error:
            prompts_skipped += 1
            logger.warning(f"Skipping prompt '{item.id}': baseline failed ({baseline_response.error})")
            continue
        for candidate in others:
            pairs_to_judge.append((item, candidate, baseline_response))

    logger.info(
        f"Phase 2: judging {len(pairs_to_judge)} pair(s) with {len(judges)} judge(s), "
        "2 orientations each"
    )

    def judge_pair(item: PromptItem, candidate: Candidate, baseline_response: LLMResponse) -> Dict[str, Any]:
        nonlocal parse_failures
        candidate_response = responses[(item.id, candidate.name)]
        record: Dict[str, Any] = {"prompt_id": item.id, "candidate": candidate.name}
        if candidate_response.error:
            record.update({"vote": "loss", "reason": "candidate_error"})
            return record
        votes = []
        votes_raw = []
        judge_details = []
        for judge in judges:
            prompt_ab = build_pairwise_prompt(item.text, baseline_response.content, candidate_response.content)
            prompt_ba = build_pairwise_prompt(item.text, candidate_response.content, baseline_response.content)
            try:
                verdict_ab = parse_verdict(judge_fn(judge, prompt_ab))
                verdict_ba = parse_verdict(judge_fn(judge, prompt_ba))
            except Exception as exc:
                logger.warning(f"Judge '{judge.name}' failed on prompt '{item.id}': {exc}")
                verdict_ab = verdict_ba = None
            if verdict_ab is None or verdict_ba is None:
                parse_failures += 1
            vote = combine_orientations(verdict_ab, verdict_ba)
            # A self-contradiction is scored as a tie (the standard position-bias
            # remedy) but kept visible, so a judge that just cannot tell the two
            # responses apart shows up as exactly that in the report.
            votes.append("tie" if vote == "inconsistent" else vote)
            votes_raw.append(vote)
            judge_details.append({
                "judge": judge.name, "verdict_ab": verdict_ab, "verdict_ba": verdict_ba, "vote": vote,
            })
        record.update({
            "vote": majority_vote(votes),
            "inconsistent": votes_raw.count("inconsistent") > len(judges) / 2,
            "judges": judge_details,
        })
        return record

    with ThreadPoolExecutor(max_workers=workers) as executor:
        futures = {
            executor.submit(judge_pair, item, candidate, baseline_response): (item, candidate)
            for item, candidate, baseline_response in pairs_to_judge
        }
        for future in as_completed(futures):
            record = future.result()
            entry = stats[record["candidate"]]
            vote = record["vote"]
            label = segment_of.get(record["prompt_id"])
            if label:
                record["segment"] = label
            if vote == "unknown":
                # No usable verdict: drop the pair instead of scoring it as a
                # tie, which the threshold would read as "good enough".
                entry.unknown += 1
            else:
                entry.judged += 1
                segment = None
                if label:
                    segment = entry.segments.setdefault(label, SegmentStats(label=label))
                    segment.judged += 1
                if vote == "win":
                    entry.wins += 1
                    if segment:
                        segment.wins += 1
                elif vote == "loss":
                    entry.losses += 1
                    if segment:
                        segment.losses += 1
                else:
                    entry.ties += 1
                    if segment:
                        segment.ties += 1
            if record.get("inconsistent"):
                entry.inconsistent += 1
            if judgments_log:
                _append_jsonl(judgments_log, record)

    baseline_stats = stats.pop(baseline.name)
    result = EvalResult(
        baseline=baseline_stats,
        candidates=sorted(stats.values(), key=lambda s: (s.avg_cost_per_prompt is None, s.avg_cost_per_prompt or 0)),
        prompts_total=len(prompts),
        prompts_judged=len(prompts) - prompts_skipped,
        prompts_skipped=prompts_skipped,
        judges=judges,
        threshold=threshold,
        parse_failures=parse_failures,
    )
    result.recommendation = pick_recommendation(result)
    return result


def pick_recommendation(result: EvalResult) -> Optional[CandidateStats]:
    """Cheapest candidate that clears the threshold and undercuts the baseline."""
    baseline_cost = result.baseline.avg_cost_per_prompt
    qualifying = []
    for entry in result.candidates:
        rate = entry.win_or_tie_rate
        cost = entry.avg_cost_per_prompt
        if rate is None or rate < result.threshold or cost is None:
            continue
        if baseline_cost is not None and cost >= baseline_cost:
            continue
        qualifying.append(entry)
    if not qualifying:
        return None
    return min(qualifying, key=lambda entry: entry.avg_cost_per_prompt)


def is_conclusive(entry: CandidateStats, threshold: float) -> bool:
    """Whether the sample actually supports the verdict, or merely suggests it.

    The recommendation itself still runs on the observed rate -- requiring
    significance would mean the default run (25 prompts) could never recommend
    anything, since even a flawless 25/25 has a Wilson lower bound of 0.87
    against a 0.9 threshold. So the verdict stands, but it is labelled: the
    lower bound has to clear the threshold before it counts as established.
    """
    interval = entry.win_or_tie_ci
    return interval is not None and interval[0] >= threshold


def _append_jsonl(path: Path, record: Dict[str, Any]) -> None:
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, ensure_ascii=False) + "\n")


# ---------------------------------------------------------------------------
# Report
# ---------------------------------------------------------------------------

def _fmt_cost(cost_per_prompt: Optional[float]) -> str:
    if cost_per_prompt is None:
        return "unknown"
    return f"${cost_per_prompt * 1000:.2f}"


_PRICE_SOURCE_LABEL = {
    "config": "your config (`model_prices`)",
    "local": "local endpoint (free)",
    "live": f"live ({LIVE_PRICES_URL})",
    "table": f"bundled table (checked {TABLE_VERIFIED})",
    "unknown": "**unknown** -- no cost computed",
}


def _fmt_price(candidate: Candidate) -> str:
    """Render a candidate's price with its source, e.g. "$5.00/$25.00 (live)"."""
    if candidate.price is None:
        return "unknown"
    price_in, price_out = candidate.price
    return f"${price_in:g}/${price_out:g} ({candidate.price_source})"


def _fmt_rate(rate: Optional[float]) -> str:
    return f"{rate * 100:.0f}%" if rate is not None else "n/a"


def render_report(result: EvalResult, generated_at: Optional[str] = None) -> str:
    """Render the human-facing markdown report."""
    baseline = result.baseline
    lines = [
        "# KI-Council Downgrade Report",
        "",
        f"Generated: {generated_at or datetime.now().strftime('%Y-%m-%d %H:%M')} | "
        f"Prompts: {result.prompts_judged}/{result.prompts_total} judged | "
        f"Baseline: **{baseline.candidate.name}** ({baseline.candidate.model}) | "
        f"Jury: {', '.join(judge.model for judge in result.judges)} | "
        f"Threshold: {result.threshold * 100:.0f}% win-or-tie",
        "",
        "## Verdict",
        "",
    ]
    baseline_cost = baseline.avg_cost_per_prompt
    recommendation = result.recommendation
    if recommendation:
        cost = recommendation.avg_cost_per_prompt
        savings = ""
        if baseline_cost and cost is not None and baseline_cost > 0:
            savings = f", saving **{(1 - cost / baseline_cost) * 100:.0f}%**"
        lines.append(
            f"**Switch to {recommendation.candidate.name}** ({recommendation.candidate.model}): "
            f"it wins or ties in {_fmt_rate(recommendation.win_or_tie_rate)} of your prompts at "
            f"{_fmt_cost(cost)} per 1000 prompts vs {_fmt_cost(baseline_cost)} for the baseline{savings}."
        )
        interval = recommendation.win_or_tie_ci
        if is_conclusive(recommendation, result.threshold):
            lines += [
                "",
                f"This holds up statistically: even the pessimistic end of the 95% "
                f"interval ({_fmt_rate(interval[0])}) clears the {result.threshold * 100:.0f}% bar.",
            ]
        else:
            needed = prompts_needed_for(result.threshold, recommendation.win_or_tie_rate)
            if needed and needed > recommendation.judged:
                remedy = (
                    f"At the rate observed here it would take about {needed} judged prompts to "
                    f"settle it; re-run with `--limit {needed}`."
                )
            else:
                remedy = (
                    "Its margin over the bar is too thin for any realistic sample to settle; treat "
                    "the two models as tied on this prompt set."
                )
            lines += [
                "",
                f"**Treat this as provisional.** With {recommendation.judged} judged prompt(s) the "
                f"true rate could be as low as {_fmt_rate(interval[0])} (95% interval: "
                f"{_fmt_rate(interval[0])}-{_fmt_rate(interval[1])}), which is below the "
                f"{result.threshold * 100:.0f}% bar. {remedy}",
            ]
        tie_share = recommendation.tie_share
        if tie_share is not None and tie_share > 0.5:
            lines += [
                "",
                f"**Careful: {_fmt_rate(tie_share)} of this verdict rests on ties, not wins.** A tie "
                "counts as good enough, so a judge that cannot tell the responses apart produces a "
                "downgrade recommendation for free. Check the judge before you trust this.",
            ]
    else:
        lines.append(
            f"**No downgrade recommended.** No cheaper candidate reached the "
            f"{result.threshold * 100:.0f}% win-or-tie bar against {baseline.candidate.name} "
            "on this prompt set."
        )
    lines += [
        "",
        "## Results",
        "",
        "| Candidate | Model | Win | Tie | Loss | Win+Tie (95% CI) | Cost / 1k prompts | Errors | Unusable |",
        "|---|---|---|---|---|---|---|---|---|",
        f"| {baseline.candidate.name} (baseline) | {baseline.candidate.model} | - | - | - | - | "
        f"{_fmt_cost(baseline_cost)} | {baseline.errors} | - |",
    ]
    for entry in result.candidates:
        marker = " **<- verdict**" if entry is recommendation else ""
        interval = entry.win_or_tie_ci
        rate = _fmt_rate(entry.win_or_tie_rate)
        if interval:
            rate += f" ({_fmt_rate(interval[0])}-{_fmt_rate(interval[1])})"
        lines.append(
            f"| {entry.candidate.name}{marker} | {entry.candidate.model} | {entry.wins} | {entry.ties} | "
            f"{entry.losses} | {rate} | "
            f"{_fmt_cost(entry.avg_cost_per_prompt)} | {entry.errors} | {entry.unknown} |"
        )
    segment_labels = sorted({
        label for entry in result.candidates for label in entry.segments
    })
    if segment_labels:
        lines += [
            "",
            "## By kind of prompt",
            "",
            "The headline rate is an average over everything you asked. This is where it "
            "breaks down -- a model can ace your everyday prompts and still fail the work you "
            "kept the expensive model for.",
            "",
            "| Kind | " + " | ".join(entry.candidate.name for entry in result.candidates) + " |",
            "|---" * (len(result.candidates) + 1) + "|",
        ]
        for label in segment_labels:
            cells = []
            for entry in result.candidates:
                segment = entry.segments.get(label)
                if segment is None or not segment.judged:
                    cells.append("-")
                elif not segment.has_enough_data:
                    cells.append(f"({_fmt_rate(segment.win_or_tie_rate)}, n={segment.judged})")
                else:
                    interval = segment.win_or_tie_ci
                    cells.append(
                        f"{_fmt_rate(segment.win_or_tie_rate)} "
                        f"({_fmt_rate(interval[0])}-{_fmt_rate(interval[1])}, n={segment.judged})"
                    )
            lines.append(f"| {label} | " + " | ".join(cells) + " |")
        lines += [
            "",
            f"Rates in brackets without an interval come from fewer than {MIN_SEGMENT_SAMPLE} "
            "prompts and are not evidence of anything -- feed the run more prompts of that kind "
            "before reading them.",
        ]
        if recommendation:
            weakest = recommendation.weakest_segment(result.threshold)
            if weakest:
                lines += [
                    "",
                    f"**{recommendation.candidate.name} falls below the bar on '{weakest.label}' "
                    f"prompts ({_fmt_rate(weakest.win_or_tie_rate)} over {weakest.judged} of them), "
                    f"even though it clears it overall.** If that kind of work matters to you, the "
                    f"headline verdict is not the one to act on.",
                ]

    judged_total = sum(entry.judged for entry in result.candidates)
    ties_total = sum(entry.ties for entry in result.candidates)
    inconsistent_total = sum(entry.inconsistent for entry in result.candidates)
    unknown_total = sum(entry.unknown for entry in result.candidates)
    if judged_total:
        lines += [
            "",
            "## Judge health",
            "",
            f"- Ties: {ties_total}/{judged_total} ({_fmt_rate(ties_total / judged_total)} of judged "
            "pairs). Ties count towards the threshold, so a high share means the verdict was handed "
            "out rather than earned.",
            f"- Self-contradictions: {inconsistent_total} pair(s) where the judge picked the same "
            "position in both orderings. Scored as ties. A high number means the judge is reading "
            "position, not quality.",
        ]
        if unknown_total:
            lines.append(
                f"- Unusable: {unknown_total} pair(s) produced no parseable verdict and were dropped "
                "from the sample entirely (they are not counted as ties)."
            )

    all_candidates = [baseline] + list(result.candidates)
    lines += [
        "",
        "## Prices",
        "",
        "The verdict is only as good as these numbers. Check them.",
        "",
        "| Candidate | USD / 1M tokens (in/out) | Source |",
        "|---|---|---|",
    ]
    for entry in all_candidates:
        candidate = entry.candidate
        price = (
            "unknown" if candidate.price is None
            else f"{candidate.price[0]:g} / {candidate.price[1]:g}"
        )
        lines.append(f"| {candidate.name} | {price} | {_PRICE_SOURCE_LABEL[candidate.price_source]} |")

    lines += [
        "",
        "## Caveats",
        "",
        "- Verdicts come from LLM judges (anonymized, position-swapped"
        + (", jury of " + str(len(result.judges)) if len(result.judges) > 1 else "")
        + "). Judge bias is mitigated, not eliminated.",
        "- Cost per 1k prompts is extrapolated from measured token usage on this prompt "
        "set; local endpoints count as $0.",
        "- Single-turn replay only: multi-turn behavior, tool use, and long-context work are not evaluated.",
    ]
    if any(entry.candidate.price_source == SOURCE_TABLE for entry in all_candidates):
        lines.append(
            f"- **Some prices come from the bundled table**, last checked against the "
            f"vendor pages on {TABLE_VERIFIED}. Prices change and models are released; "
            f"verify them or set `model_prices` in the config."
        )
    if any(entry.candidate.price_source == SOURCE_UNKNOWN for entry in all_candidates):
        lines.append(
            "- **Some prices are unknown**, so those candidates have no cost and cannot "
            "win on price. Set `price_input`/`price_output` for them to get a full verdict."
        )
    if result.prompts_skipped:
        lines.append(f"- {result.prompts_skipped} prompt(s) skipped because the baseline model failed.")
    if result.parse_failures:
        lines.append(
            f"- {result.parse_failures} judge call(s) returned nothing parseable. Where no judge "
            "produced a usable verdict, the pair was dropped from the sample rather than scored."
        )
    lines.append("")
    return "\n".join(lines)


def result_summary(result: EvalResult) -> Dict[str, Any]:
    """Machine-readable summary (written to summary.json)."""
    def entry_dict(entry: CandidateStats) -> Dict[str, Any]:
        return {
            "name": entry.candidate.name,
            "model": entry.candidate.model,
            "provider": entry.candidate.provider,
            "wins": entry.wins, "ties": entry.ties, "losses": entry.losses,
            "errors": entry.errors, "judged": entry.judged,
            "inconsistent": entry.inconsistent, "unknown": entry.unknown,
            "win_or_tie_rate": entry.win_or_tie_rate,
            "win_or_tie_ci95": entry.win_or_tie_ci,
            "tie_share": entry.tie_share,
            "conclusive": is_conclusive(entry, result.threshold),
            "avg_cost_per_prompt_usd": entry.avg_cost_per_prompt,
            "price_source": entry.candidate.price_source,
            "segments": {
                label: {
                    "wins": segment.wins, "ties": segment.ties, "losses": segment.losses,
                    "judged": segment.judged,
                    "win_or_tie_rate": segment.win_or_tie_rate,
                    "win_or_tie_ci95": segment.win_or_tie_ci,
                    "enough_data": segment.has_enough_data,
                }
                for label, segment in sorted(entry.segments.items())
            },
        }

    return {
        "baseline": entry_dict(result.baseline),
        "candidates": [entry_dict(entry) for entry in result.candidates],
        "prompts_total": result.prompts_total,
        "prompts_judged": result.prompts_judged,
        "prompts_skipped": result.prompts_skipped,
        "threshold": result.threshold,
        "judges": [judge.model for judge in result.judges],
        "recommendation": result.recommendation.candidate.name if result.recommendation else None,
    }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Find the cheapest model that is good enough for your own prompts.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("source", help="Prompt source: .jsonl/.txt/.md file, folder, or ChatGPT/Claude conversations.json export.")
    parser.add_argument("--baseline", help="Candidate name to compare against (default: most expensive candidate).")
    parser.add_argument("--limit", type=int, default=25, help="Max prompts to evaluate (default: 25; cost control).")
    parser.add_argument("--threshold", type=float, default=DEFAULT_THRESHOLD, help="Required win-or-tie rate (default: 0.9).")
    parser.add_argument("--max-tokens", type=int, default=DEFAULT_MAX_TOKENS, help=f"Max tokens per response (default: {DEFAULT_MAX_TOKENS}).")
    parser.add_argument("--workers", type=int, default=DEFAULT_WORKERS, help=f"Parallel API calls (default: {DEFAULT_WORKERS}).")
    parser.add_argument("--out", help="Output directory (default: eval_out/<timestamp>).")
    parser.add_argument("--config", help="Path to a .ki-council.json file to use for this run.")
    parser.add_argument("--dry-run", action="store_true", help="Show the run plan (prompts, candidates, call count) without calling any API.")
    parser.add_argument("--no-classify", action="store_true", help="Skip prompt classification; report one overall rate instead of a breakdown by kind of prompt.")
    parser.add_argument("--segment-by", choices=("category", "difficulty"), default="category", help="Group the breakdown by kind of task or by difficulty (default: category).")
    parser.add_argument("--no-live-prices", action="store_true", help="Do not fetch current prices; use the config and the bundled table only.")
    parser.add_argument("--prices-url", default=LIVE_PRICES_URL, help=f"Where to fetch current model prices (default: {LIVE_PRICES_URL}).")
    parser.add_argument("--verbose", "-v", action="store_true", help="Enable verbose logging output.")
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    logging.basicConfig(
        level=logging.INFO if args.verbose else logging.WARNING,
        format="%(levelname)s: %(message)s",
        stream=sys.stderr,
    )
    if args.config:
        import os
        os.environ[CONFIG_ENV_VAR] = args.config

    try:
        config = load_config()
        prompts = load_prompts(args.source, limit=args.limit)
        live = {} if args.no_live_prices else fetch_live_prices(args.prices_url)
        candidates = build_candidates(config, live)
        baseline = pick_baseline(candidates, args.baseline, config)
        judges = load_judges(config)
    except Exception as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    generation_calls = len(prompts) * len(candidates)
    judge_calls = len(prompts) * (len(candidates) - 1) * len(judges) * 2
    classify_calls = 0 if args.no_classify else len(prompts)
    plan = [
        f"Prompts:     {len(prompts)} (limit {args.limit})",
        f"Candidates:  " + ", ".join(
            f"{c.name} ({c.model}{'|baseline' if c.name == baseline.name else ''})" for c in candidates
        ),
        f"Jury:        " + ", ".join(judge.model for judge in judges),
        f"API calls:   {generation_calls} generations + {judge_calls} judge calls"
        + (f" + {classify_calls} classification calls" if classify_calls else ""),
        f"Threshold:   {args.threshold * 100:.0f}% win-or-tie",
        f"Prices:      " + ", ".join(
            f"{c.name}={_fmt_price(c)}" for c in candidates
        ),
    ]
    print("=== Run plan ===\n" + "\n".join(plan) + "\n", file=sys.stderr)
    for candidate in candidates:
        if candidate.price_source == SOURCE_UNKNOWN:
            print(
                f"Warning: no price for '{candidate.name}' ({candidate.model}). Its cost "
                f"stays blank and it cannot win on price. Set price_input/price_output "
                f"in the config to fix this.",
                file=sys.stderr,
            )
        elif candidate.price_source == SOURCE_TABLE:
            print(
                f"Warning: price for '{candidate.name}' comes from the bundled table "
                f"(checked {TABLE_VERIFIED}), not the live source -- it may be stale.",
                file=sys.stderr,
            )
    if args.dry_run:
        return 0

    out_dir = Path(args.out) if args.out else Path("eval_out") / datetime.now().strftime("%Y%m%d-%H%M%S")

    segment_of: Dict[str, str] = {}
    if not args.no_classify:
        # The first judge doubles as the classifier: it is already a model the
        # user trusts to read their prompts, and it saves a second knob.
        classifier = judges[0]
        classifications = classify_prompts(
            prompts,
            lambda text: make_client(classifier).generate(
                text, max_tokens=CLASSIFY_MAX_TOKENS
            ).content,
            workers=args.workers,
        )
        segment_of = segment_labels(classifications, by=args.segment_by)
        counts = Counter(segment_of.values())
        print(
            "Segments: " + ", ".join(f"{label}={n}" for label, n in sorted(counts.items())),
            file=sys.stderr,
        )
        thin = [label for label, n in counts.items() if n < MIN_SEGMENT_SAMPLE]
        if thin:
            print(
                f"Warning: {', '.join(sorted(thin))} have fewer than {MIN_SEGMENT_SAMPLE} prompts. "
                f"Those segments will not support a verdict -- raise --limit or feed more prompts "
                f"of that kind.",
                file=sys.stderr,
            )

    try:
        result = run_eval(
            prompts, candidates, judges, baseline,
            max_tokens=args.max_tokens, threshold=args.threshold,
            workers=args.workers, out_dir=out_dir, segment_of=segment_of,
        )
    except KeyboardInterrupt:
        print("\nAborted by user.", file=sys.stderr)
        return 130

    report = render_report(result)
    (out_dir / "report.md").write_text(report, encoding="utf-8")
    (out_dir / "summary.json").write_text(
        json.dumps(result_summary(result), ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(report)
    print(f"Artifacts written to {out_dir}/ (report.md, summary.json, responses.jsonl, judgments.jsonl)", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
