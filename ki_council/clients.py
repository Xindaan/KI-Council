import json
import logging
import os
import ssl
import urllib.error
import urllib.parse
import urllib.request
from functools import lru_cache
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Protocol

from ki_council.config import get_setting, load_config

# Constants
DEFAULT_TIMEOUT = 120  # Increased from 60 to handle slower providers
DEFAULT_MAX_TOKENS = 512

logger = logging.getLogger(__name__)


class LLMError(RuntimeError):
    """Exception raised when an LLM API request fails."""
    pass


@dataclass
class LLMResponse:
    """Response from an LLM provider.

    Attributes:
        provider: Name of the LLM provider (e.g., 'openai', 'gemini', 'anthropic')
        model: Model identifier used for generation
        content: Generated text response
        error: Optional error message if the request failed
        tokens_used: Optional token count (prompt + completion)
        tokens_prompt: Optional number of prompt tokens
        tokens_completion: Optional number of completion tokens
    """
    provider: str
    model: str
    content: str
    error: Optional[str] = None
    tokens_used: Optional[int] = None
    tokens_prompt: Optional[int] = None
    tokens_completion: Optional[int] = None


class LLMClient(Protocol):
    """Protocol defining the interface for LLM clients."""

    def generate(self, prompt: str, max_tokens: int = DEFAULT_MAX_TOKENS) -> LLMResponse:
        """Generate a response for the given prompt.

        Args:
            prompt: The text prompt to send to the LLM
            max_tokens: Maximum number of tokens to generate

        Returns:
            LLMResponse with the generated content

        Raises:
            LLMError: If the API request fails
        """
        ...


def _get_timeout() -> int:
    """Get configured timeout value.

    Returns:
        Timeout in seconds (default: 120)
    """
    config = load_config()
    timeout_str = get_setting(config, "KI_COUNCIL_TIMEOUT", "timeout")
    if timeout_str:
        try:
            return int(timeout_str)
        except ValueError:
            logger.warning(f"Invalid timeout value '{timeout_str}', using default {DEFAULT_TIMEOUT}")
    return DEFAULT_TIMEOUT


def _post_json(url: str, payload: Dict[str, Any], headers: Dict[str, str]) -> Dict[str, Any]:
    """Send a POST request with JSON payload and return JSON response.

    Args:
        url: Target URL for the request
        payload: JSON-serializable payload
        headers: HTTP headers

    Returns:
        Parsed JSON response

    Raises:
        LLMError: If the request fails or response is invalid
    """
    data = json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(url, data=data, headers=headers, method="POST")

    timeout = _get_timeout()
    logger.debug(f"Sending POST request to {url} (timeout: {timeout}s)")

    try:
        with urllib.request.urlopen(request, timeout=timeout, context=_get_ssl_context()) as response:
            body = response.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        error_body = exc.read().decode("utf-8")
        logger.error(f"HTTP error {exc.code} from {url}: {error_body}")
        raise LLMError(f"Request failed: {exc.code} {exc.reason} {error_body}") from exc
    except urllib.error.URLError as exc:
        import socket
        reason = exc.reason
        if isinstance(reason, socket.timeout):
            hint = (
                f"Request timed out after {timeout}s. Try increasing the timeout with "
                "KI_COUNCIL_TIMEOUT environment variable or 'timeout' in config file."
            )
            logger.error(f"Timeout error after {timeout}s: {url}")
            raise LLMError(f"Request timed out after {timeout}s. {hint}") from exc
        if isinstance(reason, ssl.SSLCertVerificationError):
            hint = (
                "TLS verification failed. Configure KI_COUNCIL_CA_BUNDLE/ca_bundle "
                "with your CA bundle path, or set KI_COUNCIL_INSECURE=1/"
                "insecure_ssl=true to disable verification (not recommended)."
            )
            logger.error(f"SSL verification error: {reason}")
            raise LLMError(f"Request failed: {reason}. {hint}") from exc
        logger.error(f"URL error: {reason}")
        raise LLMError(f"Request failed: {reason}") from exc

    try:
        return json.loads(body)
    except json.JSONDecodeError as exc:
        logger.error(f"Invalid JSON response from {url}: {body[:200]}")
        raise LLMError(f"Invalid JSON response: {body}") from exc


@lru_cache(maxsize=1)
def _get_ssl_context() -> Optional[ssl.SSLContext]:
    config = load_config()
    ca_bundle = get_setting(config, "KI_COUNCIL_CA_BUNDLE", "ca_bundle")
    insecure = get_setting(config, "KI_COUNCIL_INSECURE", "insecure_ssl")
    if insecure and insecure.lower() in {"1", "true", "yes"}:
        return ssl._create_unverified_context()
    if ca_bundle:
        return ssl.create_default_context(cafile=ca_bundle)
    return None


def normalize_base_url(base_url: str) -> str:
    base_url = base_url.strip()
    if not base_url:
        raise LLMError("Invalid base URL: (empty)")
    if base_url.startswith("http:") and not base_url.startswith("http://"):
        base_url = base_url.replace("http:", "http://", 1)
    if base_url.startswith("https:") and not base_url.startswith("https://"):
        base_url = base_url.replace("https:", "https://", 1)
    parsed = urllib.parse.urlparse(base_url)
    if not parsed.scheme:
        base_url = f"https://{base_url}"
        parsed = urllib.parse.urlparse(base_url)
    if not parsed.path and parsed.netloc.endswith("v1") and not parsed.netloc.endswith(".v1"):
        base_url = f"{parsed.scheme}://{parsed.netloc[:-2]}/v1"
        parsed = urllib.parse.urlparse(base_url)
    if not parsed.netloc:
        raise LLMError(f"Invalid base URL: {base_url}")
    return base_url


class OpenAIClient:
    """OpenAI API client for chat completions."""

    def __init__(self, api_key: str, model: str, base_url: str) -> None:
        """Initialize OpenAI client.

        Args:
            api_key: OpenAI API key
            model: Model identifier (e.g., 'gpt-4o-mini')
            base_url: Base URL for API requests
        """
        self.api_key = api_key
        self.model = model
        self.base_url = normalize_base_url(base_url).rstrip("/")

    def _max_tokens_param(self) -> str:
        """Determine the correct max tokens parameter name for the model."""
        config = load_config()
        override = get_setting(
            config, "OPENAI_MAX_TOKENS_PARAM", "openai_max_tokens_param"
        )
        if override:
            return override
        lower_model = self.model.lower()
        if lower_model.startswith(("gpt-5", "o1", "o3")):
            return "max_completion_tokens"
        return "max_tokens"

    def generate(self, prompt: str, max_tokens: int = DEFAULT_MAX_TOKENS) -> LLMResponse:
        """Generate a response using OpenAI's chat completion API.

        Args:
            prompt: The text prompt
            max_tokens: Maximum tokens to generate

        Returns:
            LLMResponse with generated content and token usage

        Raises:
            LLMError: If the API request fails
        """
        url = f"{self.base_url}/chat/completions"
        payload = {
            "model": self.model,
            "messages": [{"role": "user", "content": prompt}],
        }
        payload[self._max_tokens_param()] = max_tokens
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

        logger.info(f"Requesting OpenAI completion with model {self.model}")
        data = _post_json(url, payload, headers)

        try:
            content = data["choices"][0]["message"]["content"]
            usage = data.get("usage", {})
            tokens_prompt = usage.get("prompt_tokens")
            tokens_completion = usage.get("completion_tokens")
            tokens_total = usage.get("total_tokens")
        except (KeyError, IndexError, TypeError) as exc:
            raise LLMError(f"Unexpected OpenAI response: {data}") from exc

        logger.debug(f"OpenAI response: {tokens_total} tokens used")

        return LLMResponse(
            provider="openai",
            model=self.model,
            content=content.strip(),
            tokens_used=tokens_total,
            tokens_prompt=tokens_prompt,
            tokens_completion=tokens_completion,
        )


class GeminiClient:
    """Google Gemini API client."""

    def __init__(self, api_key: str, model: str) -> None:
        """Initialize Gemini client.

        Args:
            api_key: Google API key
            model: Model identifier (e.g., 'gemini-1.5-flash')
        """
        self.api_key = api_key
        self.model = model

    def generate(self, prompt: str, max_tokens: int = DEFAULT_MAX_TOKENS) -> LLMResponse:
        """Generate a response using Google's Gemini API.

        Args:
            prompt: The text prompt
            max_tokens: Maximum tokens to generate

        Returns:
            LLMResponse with generated content

        Raises:
            LLMError: If the API request fails
        """
        url = (
            "https://generativelanguage.googleapis.com/v1beta/"
            f"models/{self.model}:generateContent?key={self.api_key}"
        )
        payload = {
            "contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": {"maxOutputTokens": max_tokens},
        }
        headers = {"Content-Type": "application/json"}

        logger.info(f"Requesting Gemini completion with model {self.model}")
        data = _post_json(url, payload, headers)

        try:
            # Check finish reason first
            finish_reason = data.get("candidates", [{}])[0].get("finishReason", "UNKNOWN")

            # Try to extract content
            content_obj = data["candidates"][0].get("content", {})
            parts = content_obj.get("parts", [])

            if not parts or not parts[0].get("text"):
                # Handle empty content (e.g., MAX_TOKENS, SAFETY, etc.)
                if finish_reason == "MAX_TOKENS":
                    # Check if this is a thinking model that used all tokens for thoughts
                    thoughts_tokens = usage.get("thoughtsTokenCount", 0)
                    if thoughts_tokens > 0:
                        error_msg = (
                            f"Gemini thinking model used {thoughts_tokens} tokens for internal reasoning "
                            f"and hit max_tokens limit ({max_tokens}) before generating output. "
                            f"Try --max-tokens 2048 or higher for thinking models."
                        )
                    else:
                        error_msg = (
                            f"Gemini stopped due to max_tokens limit ({max_tokens}). "
                            "Try increasing --max-tokens."
                        )
                elif finish_reason in ("SAFETY", "RECITATION"):
                    error_msg = f"Gemini blocked the response due to: {finish_reason}"
                else:
                    error_msg = f"Gemini returned empty content (finish_reason: {finish_reason})"

                logger.warning(error_msg)
                raise LLMError(error_msg)

            content = parts[0]["text"]

            # Gemini may include token counts in usageMetadata
            usage = data.get("usageMetadata", {})
            tokens_prompt = usage.get("promptTokenCount")
            tokens_completion = usage.get("candidatesTokenCount")
            tokens_total = usage.get("totalTokenCount")

            # Note if response was cut off
            if finish_reason == "MAX_TOKENS":
                logger.warning(f"Gemini response may be incomplete (finish_reason: MAX_TOKENS)")

        except (KeyError, IndexError, TypeError) as exc:
            logger.error(f"Failed to parse Gemini response: {data}")
            raise LLMError(f"Unexpected Gemini response: {data}") from exc

        logger.debug(f"Gemini response: {tokens_total or 'unknown'} tokens used")

        return LLMResponse(
            provider="gemini",
            model=self.model,
            content=content.strip(),
            tokens_used=tokens_total,
            tokens_prompt=tokens_prompt,
            tokens_completion=tokens_completion,
        )


class AnthropicClient:
    """Anthropic Claude API client."""

    def __init__(self, api_key: str, model: str) -> None:
        """Initialize Anthropic client.

        Args:
            api_key: Anthropic API key
            model: Model identifier (e.g., 'claude-3-haiku-20240307')
        """
        self.api_key = api_key
        self.model = model

    def generate(self, prompt: str, max_tokens: int = DEFAULT_MAX_TOKENS) -> LLMResponse:
        """Generate a response using Anthropic's Messages API.

        Args:
            prompt: The text prompt
            max_tokens: Maximum tokens to generate

        Returns:
            LLMResponse with generated content and token usage

        Raises:
            LLMError: If the API request fails
        """
        url = "https://api.anthropic.com/v1/messages"
        payload = {
            "model": self.model,
            "max_tokens": max_tokens,
            "messages": [{"role": "user", "content": prompt}],
        }
        headers = {
            "x-api-key": self.api_key,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        }

        logger.info(f"Requesting Anthropic completion with model {self.model}")
        data = _post_json(url, payload, headers)

        try:
            content = data["content"][0]["text"]
            usage = data.get("usage", {})
            tokens_prompt = usage.get("input_tokens")
            tokens_completion = usage.get("output_tokens")
            tokens_total = (tokens_prompt or 0) + (tokens_completion or 0) if tokens_prompt and tokens_completion else None
        except (KeyError, IndexError, TypeError) as exc:
            raise LLMError(f"Unexpected Anthropic response: {data}") from exc

        logger.debug(f"Anthropic response: {tokens_total or 'unknown'} tokens used")

        return LLMResponse(
            provider="anthropic",
            model=self.model,
            content=content.strip(),
            tokens_used=tokens_total,
            tokens_prompt=tokens_prompt,
            tokens_completion=tokens_completion,
        )


def load_clients(provider_filter: Optional[List[str]] = None) -> List[LLMClient]:
    """Load all configured LLM clients.

    Reads configuration and initializes clients for providers that have API keys configured.

    Args:
        provider_filter: Optional list of provider names to load (e.g., ['openai', 'anthropic']).
                        If None, loads all configured providers.

    Returns:
        List of initialized LLM clients

    Note:
        Returns an empty list if no API keys are configured.
    """
    config = load_config()
    clients: List[LLMClient] = []

    # Check for provider filter from environment
    if provider_filter is None:
        env_providers = get_setting(config, "KI_COUNCIL_PROVIDERS", "providers")
        if env_providers:
            provider_filter = [p.strip().lower() for p in env_providers.split(",")]

    def should_load(provider_name: str) -> bool:
        """Check if provider should be loaded based on filter."""
        if provider_filter is None:
            return True
        return provider_name.lower() in provider_filter

    openai_key = get_setting(config, "OPENAI_API_KEY", "openai_api_key")
    if openai_key and should_load("openai"):
        model = get_setting(config, "OPENAI_MODEL", "openai_model", "gpt-4o-mini")
        base_url = get_setting(
            config,
            "OPENAI_BASE_URL",
            "openai_base_url",
            "https://api.openai.com/v1",
        )
        clients.append(OpenAIClient(openai_key, model, base_url))
        logger.info(f"Loaded OpenAI client with model {model}")

    gemini_key = get_setting(config, "GEMINI_API_KEY", "gemini_api_key")
    if gemini_key and should_load("gemini"):
        model = get_setting(config, "GEMINI_MODEL", "gemini_model", "gemini-1.5-flash")
        clients.append(GeminiClient(gemini_key, model))
        logger.info(f"Loaded Gemini client with model {model}")

    anthropic_key = get_setting(config, "ANTHROPIC_API_KEY", "anthropic_api_key")
    if anthropic_key and should_load("anthropic"):
        model = get_setting(
            config,
            "ANTHROPIC_MODEL",
            "anthropic_model",
            "claude-3-haiku-20240307",
        )
        clients.append(AnthropicClient(anthropic_key, model))
        logger.info(f"Loaded Anthropic client with model {model}")

    logger.info(f"Loaded {len(clients)} LLM client(s)")
    return clients
