import json
import os
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Any, Dict, List

from ki_council.config import get_setting, load_config

class LLMError(RuntimeError):
    pass


@dataclass
class LLMResponse:
    provider: str
    model: str
    content: str


def _post_json(url: str, payload: Dict[str, Any], headers: Dict[str, str]) -> Dict[str, Any]:
    data = json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(url, data=data, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            body = response.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        error_body = exc.read().decode("utf-8")
        raise LLMError(f"Request failed: {exc.code} {exc.reason} {error_body}") from exc
    except urllib.error.URLError as exc:
        raise LLMError(f"Request failed: {exc.reason}") from exc

    try:
        return json.loads(body)
    except json.JSONDecodeError as exc:
        raise LLMError(f"Invalid JSON response: {body}") from exc


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
    def __init__(self, api_key: str, model: str, base_url: str) -> None:
        self.api_key = api_key
        self.model = model
        self.base_url = normalize_base_url(base_url).rstrip("/")

    def generate(self, prompt: str, max_tokens: int = 512) -> LLMResponse:
        url = f"{self.base_url}/chat/completions"
        payload = {
            "model": self.model,
            "messages": [{"role": "user", "content": prompt}],
            "max_tokens": max_tokens,
        }
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        data = _post_json(url, payload, headers)
        try:
            content = data["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as exc:
            raise LLMError(f"Unexpected OpenAI response: {data}") from exc
        return LLMResponse(provider="openai", model=self.model, content=content.strip())


class GeminiClient:
    def __init__(self, api_key: str, model: str) -> None:
        self.api_key = api_key
        self.model = model

    def generate(self, prompt: str, max_tokens: int = 512) -> LLMResponse:
        url = (
            "https://generativelanguage.googleapis.com/v1beta/"
            f"models/{self.model}:generateContent?key={self.api_key}"
        )
        payload = {
            "contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": {"maxOutputTokens": max_tokens},
        }
        headers = {"Content-Type": "application/json"}
        data = _post_json(url, payload, headers)
        try:
            content = data["candidates"][0]["content"]["parts"][0]["text"]
        except (KeyError, IndexError, TypeError) as exc:
            raise LLMError(f"Unexpected Gemini response: {data}") from exc
        return LLMResponse(provider="gemini", model=self.model, content=content.strip())


class AnthropicClient:
    def __init__(self, api_key: str, model: str) -> None:
        self.api_key = api_key
        self.model = model

    def generate(self, prompt: str, max_tokens: int = 512) -> LLMResponse:
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
        data = _post_json(url, payload, headers)
        try:
            content = data["content"][0]["text"]
        except (KeyError, IndexError, TypeError) as exc:
            raise LLMError(f"Unexpected Anthropic response: {data}") from exc
        return LLMResponse(provider="anthropic", model=self.model, content=content.strip())


def load_clients() -> List[Any]:
    config = load_config()
    clients: List[Any] = []

    openai_key = get_setting(config, "OPENAI_API_KEY", "openai_api_key")
    if openai_key:
        model = get_setting(config, "OPENAI_MODEL", "openai_model", "gpt-4o-mini")
        base_url = get_setting(
            config,
            "OPENAI_BASE_URL",
            "openai_base_url",
            "https://api.openai.com/v1",
        )
        clients.append(OpenAIClient(openai_key, model, base_url))

    gemini_key = get_setting(config, "GEMINI_API_KEY", "gemini_api_key")
    if gemini_key:
        model = get_setting(config, "GEMINI_MODEL", "gemini_model", "gemini-1.5-flash")
        clients.append(GeminiClient(gemini_key, model))

    anthropic_key = get_setting(config, "ANTHROPIC_API_KEY", "anthropic_api_key")
    if anthropic_key:
        model = get_setting(
            config,
            "ANTHROPIC_MODEL",
            "anthropic_model",
            "claude-3-haiku-20240307",
        )
        clients.append(AnthropicClient(anthropic_key, model))

    return clients
