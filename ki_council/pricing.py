"""Model pricing and cost estimation for evaluation runs.

Prices are USD per 1 million tokens, as (input, output) pairs. This tool's whole
value is its cost verdict, so a wrong price is worse than a missing one: an
unknown model reports its cost as uncomputable, while a wrong price quietly
recommends the wrong model. Every price therefore carries its source.

Resolution order (see resolve_price):

    config override  ->  local endpoint (free)  ->  live source  ->  bundled table

The bundled table is only an offline fallback and goes stale: it was verified
against the vendor pricing pages on TABLE_VERIFIED (below) and says nothing
about models released since. Prefer the live source (models.dev), which tracks
new models. Note that the live source is not authoritative either -- on
2026-07-14 it priced claude-opus-4-8 at 6/30 where Anthropic's own page said
5/25 -- so a config override always wins, and the report names the source of
every price.

Lookup matches a model exactly, or as a dated snapshot of a table entry
("claude-3-5-haiku-20241022" -> "claude-3-5-haiku"). It never matches across a
version boundary: "gpt-5.4-mini" is a different model than "gpt-5", not a
variant of it.
"""

import json
import logging
import re
from typing import Any, Dict, NamedTuple, Optional, Tuple
from urllib.error import URLError
from urllib.parse import urlparse
from urllib.request import Request, urlopen

logger = logging.getLogger(__name__)

# The date the entries below were last checked against the vendor pricing pages.
# Anything released after this date is unknown to the table -- that is what the
# live source is for. Bump this only after actually re-reading the pages.
TABLE_VERIFIED = "2026-07-14"

# USD per 1M tokens: model name -> (input, output).
# Offline fallback only. Override via "model_prices" config.
DEFAULT_PRICES: Dict[str, Tuple[float, float]] = {
    # OpenAI (developers.openai.com/api/docs/pricing)
    "gpt-5.6-sol": (5.00, 30.00),
    "gpt-5.6-terra": (2.50, 15.00),
    "gpt-5.6-luna": (1.00, 6.00),
    "gpt-5.5-pro": (30.00, 180.00),
    "gpt-5.5": (5.00, 30.00),
    "gpt-5.4-pro": (30.00, 180.00),
    "gpt-5.4-nano": (0.20, 1.25),
    "gpt-5.4-mini": (0.75, 4.50),
    "gpt-5.4": (2.50, 15.00),
    "gpt-5.3-codex": (1.75, 14.00),
    "gpt-4o-mini": (0.15, 0.60),
    "gpt-4o": (2.50, 10.00),
    "gpt-4.1-nano": (0.10, 0.40),
    "gpt-4.1-mini": (0.40, 1.60),
    "gpt-4.1": (2.00, 8.00),
    "gpt-5-nano": (0.05, 0.40),
    "gpt-5-mini": (0.25, 2.00),
    "gpt-5": (1.25, 10.00),
    "o3-mini": (1.10, 4.40),
    "o3": (2.00, 8.00),
    "o4-mini": (1.10, 4.40),
    # Anthropic (platform.claude.com/docs/en/about-claude/pricing)
    "claude-fable-5": (10.00, 50.00),
    "claude-opus-4-8": (5.00, 25.00),
    "claude-opus-4-7": (5.00, 25.00),
    "claude-opus-4-6": (5.00, 25.00),
    "claude-opus-4-5": (5.00, 25.00),
    "claude-opus-4-1": (15.00, 75.00),
    # Introductory price through 2026-08-31; 3.00/15.00 from 2026-09-01.
    "claude-sonnet-5": (2.00, 10.00),
    "claude-sonnet-4-6": (3.00, 15.00),
    "claude-sonnet-4-5": (3.00, 15.00),
    "claude-3-haiku": (0.25, 1.25),
    "claude-3-5-haiku": (0.80, 4.00),
    "claude-haiku-4-5": (1.00, 5.00),
    "claude-3-5-sonnet": (3.00, 15.00),
    "claude-3-7-sonnet": (3.00, 15.00),
    "claude-sonnet-4": (3.00, 15.00),
    "claude-opus-4": (15.00, 75.00),
    # Google (ai.google.dev/gemini-api/docs/pricing); tiered models listed at
    # their <=200k-token rate.
    "gemini-3.5-flash": (1.50, 9.00),
    "gemini-3.1-pro-preview": (2.00, 12.00),
    "gemini-3.1-flash-lite": (0.25, 1.50),
    "gemini-3-flash-preview": (0.50, 3.00),
    "gemini-1.5-flash": (0.075, 0.30),
    "gemini-1.5-pro": (1.25, 5.00),
    "gemini-2.0-flash": (0.10, 0.40),
    "gemini-2.5-flash-lite": (0.10, 0.40),
    "gemini-2.5-flash": (0.30, 2.50),
    "gemini-2.5-pro": (1.25, 10.00),
}

_LOCAL_HOSTS = ("localhost", "127.0.0.1", "0.0.0.0", "host.docker.internal")


def _normalize_model_name(model: str) -> str:
    """Lowercase and strip provider path prefixes like 'models/'."""
    name = model.strip().lower()
    if name.startswith("models/"):
        name = name[len("models/"):]
    return name


_SNAPSHOT_SUFFIX = re.compile(r"^[-@:](\d{8}|\d{4}-\d{2}-\d{2}|latest)$")


def _is_local_endpoint(base_url: str) -> bool:
    """Whether base_url points at a local model server (which costs nothing).

    Compares the host exactly. A substring test would price a remote endpoint
    like "https://localhost.example.com/v1" at zero and make a paid candidate
    look free.
    """
    host = urlparse(base_url if "//" in base_url else f"//{base_url}").hostname
    return (host or "").lower() in _LOCAL_HOSTS


def _match_table(
    name: str, table: Dict[str, Tuple[float, float]]
) -> Optional[Tuple[float, float]]:
    """Look up a model in a price table.

    Only an exact hit or a dated snapshot of the same model counts
    ("claude-3-5-haiku-20241022" -> "claude-3-5-haiku"). A prefix must never
    reach across a version boundary: "gpt-5.4-mini" is not a snapshot of
    "gpt-5" and "claude-opus-4-8" is not one of "claude-opus-4" -- they are
    different models at different prices. Returning None (unknown) is safe;
    the run reports the cost as uncomputable. Returning a neighbour's price
    is not: it produces a confident, wrong cost verdict.
    """
    if name in table:
        return table[name]
    for prefix in sorted(table, key=len, reverse=True):
        if name.startswith(prefix) and _SNAPSHOT_SUFFIX.match(name[len(prefix):]):
            return table[prefix]
    return None


LIVE_PRICES_URL = "https://models.dev/api.json"

# Where a resolved price came from, most trustworthy first.
SOURCE_CONFIG = "config"
SOURCE_LOCAL = "local"
SOURCE_LIVE = "live"
SOURCE_TABLE = "table"
SOURCE_UNKNOWN = "unknown"


class PriceInfo(NamedTuple):
    """A resolved price and where it came from, so the report can show both."""

    price: Optional[Tuple[float, float]]
    source: str


# models.dev lists a model under every provider that resells it. Only the
# first party is read: on 2026-07-14, claude-opus-4-8 appeared under 16
# providers priced from 0/0 to 6/30, while Anthropic's own entry (5/25) matched
# its pricing page. Reading them all would let an arbitrary reseller -- or a
# 0/0 entry, making a paid model look free -- decide the cost verdict.
# Maps this project's provider names to the keys used by models.dev.
_FIRST_PARTY = {"openai": "openai", "anthropic": "anthropic", "gemini": "google"}


def parse_live_prices(payload: Any) -> Dict[str, Tuple[float, float]]:
    """Extract {model_id: (input, output)} from a models.dev API payload.

    Kept separate from the network call so it can be tested against a fixture.
    Entries without both text prices are skipped: a model priced per image or
    per minute has no meaningful per-token cost here.
    """
    prices: Dict[str, Tuple[float, float]] = {}
    if not isinstance(payload, dict):
        logger.warning("Live price source returned an unexpected shape; ignoring it.")
        return prices
    for provider_key in _FIRST_PARTY.values():
        provider = payload.get(provider_key)
        if not isinstance(provider, dict):
            continue
        models = provider.get("models")
        if not isinstance(models, dict):
            continue
        for model_id, entry in models.items():
            cost = entry.get("cost") if isinstance(entry, dict) else None
            if not isinstance(cost, dict):
                continue
            try:
                price_in = float(cost["input"])
                price_out = float(cost["output"])
            except (KeyError, TypeError, ValueError):
                continue
            prices[_normalize_model_name(str(model_id))] = (price_in, price_out)
    return prices


def fetch_live_prices(
    url: str = LIVE_PRICES_URL, timeout: int = 20
) -> Dict[str, Tuple[float, float]]:
    """Fetch current model prices from an open source. Never raises.

    The bundled table cannot know models released after TABLE_VERIFIED, so this
    is the primary source. It is not authoritative either -- config overrides
    still win, and the report names the source of every price. A failed fetch
    is not fatal: the run falls back to the table and says so.
    """
    request = Request(url, headers={"User-Agent": "ki-council"})
    try:
        with urlopen(request, timeout=timeout) as response:  # noqa: S310 (fixed https URL)
            payload = json.load(response)
    except (URLError, TimeoutError, ValueError, OSError) as exc:
        logger.warning(f"Could not fetch live prices from {url}: {exc}")
        return {}
    prices = parse_live_prices(payload)
    logger.info(f"Fetched {len(prices)} model prices from {url}")
    return prices


def resolve_price(
    model: str,
    overrides: Optional[Dict[str, Tuple[float, float]]] = None,
    base_url: Optional[str] = None,
    live: Optional[Dict[str, Tuple[float, float]]] = None,
) -> PriceInfo:
    """Resolve a model's price and record which layer supplied it."""
    name = _normalize_model_name(model)

    hit = _match_table(name, overrides or {})
    if hit is not None:
        return PriceInfo(hit, SOURCE_CONFIG)

    if base_url and _is_local_endpoint(base_url):
        return PriceInfo((0.0, 0.0), SOURCE_LOCAL)

    hit = _match_table(name, live or {})
    if hit is not None:
        return PriceInfo(hit, SOURCE_LIVE)

    hit = _match_table(name, DEFAULT_PRICES)
    if hit is not None:
        return PriceInfo(hit, SOURCE_TABLE)

    return PriceInfo(None, SOURCE_UNKNOWN)


def parse_price_overrides(config: Dict[str, Any]) -> Dict[str, Tuple[float, float]]:
    """Read the optional "model_prices" object from the config file.

    Invalid entries are skipped with a warning rather than failing the run.
    """
    raw = config.get("model_prices")
    overrides: Dict[str, Tuple[float, float]] = {}
    if not isinstance(raw, dict):
        return overrides
    for model, pair in raw.items():
        try:
            price_in, price_out = float(pair[0]), float(pair[1])
        except (TypeError, ValueError, IndexError, KeyError):
            logger.warning(f"Ignoring invalid model_prices entry for '{model}': {pair!r}")
            continue
        overrides[_normalize_model_name(str(model))] = (price_in, price_out)
    return overrides


def lookup_price(
    model: str,
    overrides: Optional[Dict[str, Tuple[float, float]]] = None,
    base_url: Optional[str] = None,
    live: Optional[Dict[str, Tuple[float, float]]] = None,
) -> Optional[Tuple[float, float]]:
    """Find the (input, output) USD price per 1M tokens, or None if unknown.

    Use resolve_price when the caller needs to know where the price came from.
    """
    return resolve_price(model, overrides, base_url, live).price


def estimate_cost_usd(
    tokens_prompt: Optional[int],
    tokens_completion: Optional[int],
    price: Optional[Tuple[float, float]],
) -> Optional[float]:
    """Estimate the USD cost of one generation, or None if not computable."""
    if price is None or tokens_prompt is None or tokens_completion is None:
        return None
    price_in, price_out = price
    return (tokens_prompt * price_in + tokens_completion * price_out) / 1_000_000
