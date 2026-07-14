"""Model pricing table and cost estimation for evaluation runs.

Prices are USD per 1 million tokens, as (input, output) pairs. The built-in
table is a fallback snapshot (last reviewed 2026-07) -- provider prices change,
so users can override any entry via the "model_prices" object in the config
file, e.g.:

    "model_prices": {
        "gpt-4o-mini": [0.15, 0.60],
        "my-local-model": [0, 0]
    }

Lookup uses longest-prefix matching on the lowercased model name, so dated
snapshots like "claude-3-5-haiku-20241022" match their base entry.
"""

import logging
from typing import Any, Dict, Optional, Tuple

logger = logging.getLogger(__name__)

# USD per 1M tokens: model name prefix -> (input, output).
# Fallback snapshot, last reviewed 2026-07. Override via "model_prices" config.
DEFAULT_PRICES: Dict[str, Tuple[float, float]] = {
    # OpenAI
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
    # Anthropic
    "claude-3-haiku": (0.25, 1.25),
    "claude-3-5-haiku": (0.80, 4.00),
    "claude-haiku-4-5": (1.00, 5.00),
    "claude-3-5-sonnet": (3.00, 15.00),
    "claude-3-7-sonnet": (3.00, 15.00),
    "claude-sonnet-4": (3.00, 15.00),
    "claude-opus-4": (15.00, 75.00),
    # Google
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
) -> Optional[Tuple[float, float]]:
    """Find the (input, output) USD price per 1M tokens for a model.

    Resolution order: exact/prefix match in overrides, then local-host
    heuristic (local endpoints cost nothing), then the built-in table.
    Returns None if the model is unknown.
    """
    name = _normalize_model_name(model)

    for table in (overrides or {}, ):
        for prefix in sorted(table, key=len, reverse=True):
            if name.startswith(prefix):
                return table[prefix]

    if base_url:
        lowered = base_url.lower()
        if any(host in lowered for host in _LOCAL_HOSTS):
            return (0.0, 0.0)

    for prefix in sorted(DEFAULT_PRICES, key=len, reverse=True):
        if name.startswith(prefix):
            return DEFAULT_PRICES[prefix]

    return None


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
