import logging
import os
from concurrent.futures import ThreadPoolExecutor, as_completed
from functools import lru_cache
from pathlib import Path
from typing import Iterable, List, Tuple

from ki_council.clients import LLMClient, LLMError, LLMResponse, OpenAIClient, load_clients
from ki_council.config import get_setting, load_config

logger = logging.getLogger(__name__)

# Constants
DEFAULT_JUDGE_TIMEOUT = 600  # 10 minutes for judge to analyze multiple responses


@lru_cache(maxsize=1)
def _load_judge_prompt_template() -> str:
    """Load the judge prompt template from file.

    Returns:
        Template string with {prompt} and {responses_text} placeholders
    """
    template_path = Path(__file__).with_name("judge_prompt.txt")
    logger.debug(f"Loading judge prompt template from {template_path}")
    return template_path.read_text(encoding="utf-8")


def gather_responses(prompt: str, max_tokens: int = 512) -> List[LLMResponse]:
    """Gather responses from all configured LLM providers in parallel.

    Sends the same prompt to all available LLM clients concurrently. If a provider
    fails, it continues with others and includes an error response for the failed provider.

    Args:
        prompt: The text prompt to send to all providers
        max_tokens: Maximum tokens for each response

    Returns:
        List of LLMResponse objects, sorted by provider name

    Raises:
        RuntimeError: If no LLM clients are configured
    """
    clients = load_clients()
    if not clients:
        raise RuntimeError(
            "No LLM clients configured. Set API keys in environment variables or "
            "in a .ki-council.json config file."
        )

    logger.info(f"Gathering responses from {len(clients)} provider(s)")

    responses: List[LLMResponse] = []
    with ThreadPoolExecutor(max_workers=len(clients)) as executor:
        future_map = {
            executor.submit(client.generate, prompt, max_tokens): client for client in clients
        }
        for future in as_completed(future_map):
            client = future_map[future]
            try:
                response = future.result()
                responses.append(response)
                logger.info(f"Received response from {response.provider}")
            except LLMError as exc:
                # Individual provider failed, but continue with others
                provider_name = getattr(client, "model", "unknown")
                logger.warning(f"Provider failed: {exc}")
                responses.append(
                    LLMResponse(
                        provider=getattr(client, "__class__", type(client)).__name__.replace("Client", "").lower(),
                        model=provider_name,
                        content="",
                        error=str(exc),
                    )
                )
            except Exception as exc:
                # Unexpected error, but still continue
                logger.error(f"Unexpected error from provider: {exc}", exc_info=True)
                responses.append(
                    LLMResponse(
                        provider="unknown",
                        model="unknown",
                        content="",
                        error=f"Unexpected error: {exc}",
                    )
                )

    responses.sort(key=lambda r: r.provider)
    logger.info(f"Gathered {len(responses)} response(s), {sum(1 for r in responses if not r.error)} successful")
    return responses


def format_responses(responses: Iterable[LLMResponse]) -> str:
    """Format LLM responses for display or further processing.

    Args:
        responses: Iterable of LLMResponse objects

    Returns:
        Formatted string with all responses separated by dividers
    """
    blocks = []
    for response in responses:
        parts = [
            f"Provider: {response.provider}",
            f"Model: {response.model}",
        ]

        # Add token information if available
        if response.tokens_used:
            parts.append(f"Tokens: {response.tokens_used} (prompt: {response.tokens_prompt}, completion: {response.tokens_completion})")

        # Add error or response content
        if response.error:
            parts.extend([
                "Status: ERROR",
                f"Error: {response.error}",
            ])
        else:
            parts.extend([
                "Response:",
                response.content,
            ])

        blocks.append("\n".join(parts))

    return "\n\n---\n\n".join(blocks)


def build_judge_prompt(prompt: str, responses_text: str) -> str:
    """Build the prompt for the judge LLM to compare responses.

    Loads the judge prompt template from judge_prompt.txt and fills in the
    placeholders with the provided prompt and responses.

    Args:
        prompt: Original user prompt
        responses_text: Formatted responses from all providers

    Returns:
        Judge prompt in German
    """
    template = _load_judge_prompt_template()
    return template.format(prompt=prompt, responses_text=responses_text)


def judge_responses(prompt: str, responses: List[LLMResponse]) -> Tuple[str, str]:
    """Use a judge LLM to compare and analyze responses from different providers.

    Args:
        prompt: Original user prompt
        responses: List of responses from different providers

    Returns:
        Tuple of (formatted_responses, judge_analysis)

    Raises:
        RuntimeError: If no judge API key is configured
    """
    config = load_config()
    judge_key = (
        get_setting(config, "JUDGE_API_KEY", "judge_api_key")
        or get_setting(config, "OPENAI_API_KEY", "openai_api_key")
    )
    if not judge_key:
        raise RuntimeError("No judge API key configured. Set JUDGE_API_KEY or OPENAI_API_KEY.")

    judge_model = get_setting(config, "JUDGE_MODEL", "judge_model", "gpt-4o-mini")
    judge_base_url = get_setting(
        config,
        "JUDGE_BASE_URL",
        "judge_base_url",
        get_setting(
            config,
            "OPENAI_BASE_URL",
            "openai_base_url",
            "https://api.openai.com/v1",
        ),
    )
    judge_client = OpenAIClient(judge_key, judge_model, judge_base_url)

    logger.info(f"Using judge model {judge_model} for comparison")

    responses_text = format_responses(responses)
    judge_prompt = build_judge_prompt(prompt, responses_text)

    # Filter out failed responses for judgment (optional: include them for context)
    successful_count = sum(1 for r in responses if not r.error)
    logger.info(f"Judging {successful_count} successful response(s) out of {len(responses)} total")

    # Set higher timeout for judge (analyzing multiple responses takes longer)
    judge_timeout = get_setting(config, "KI_COUNCIL_JUDGE_TIMEOUT", "judge_timeout")
    if judge_timeout:
        try:
            os.environ["KI_COUNCIL_TIMEOUT"] = judge_timeout
        except Exception:
            pass
    else:
        # Use default judge timeout
        os.environ["KI_COUNCIL_TIMEOUT"] = str(DEFAULT_JUDGE_TIMEOUT)

    try:
        result = judge_client.generate(judge_prompt, max_tokens=16384)
        logger.info("Judge comparison complete")
    finally:
        # Restore normal timeout after judge completes
        if "KI_COUNCIL_TIMEOUT" in os.environ:
            del os.environ["KI_COUNCIL_TIMEOUT"]

    return responses_text, result.content
