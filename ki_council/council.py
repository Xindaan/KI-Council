from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Iterable, List, Tuple

from ki_council.clients import LLMResponse, OpenAIClient, load_clients
from ki_council.config import get_setting, load_config


def gather_responses(prompt: str, max_tokens: int = 512) -> List[LLMResponse]:
    clients = load_clients()
    if not clients:
        raise RuntimeError(
            "No LLM clients configured. Set API keys in environment variables or "
            "in a .ki-council.json config file."
        )

    responses: List[LLMResponse] = []
    with ThreadPoolExecutor(max_workers=len(clients)) as executor:
        future_map = {
            executor.submit(client.generate, prompt, max_tokens): client for client in clients
        }
        for future in as_completed(future_map):
            response = future.result()
            responses.append(response)

    responses.sort(key=lambda r: r.provider)
    return responses


def format_responses(responses: Iterable[LLMResponse]) -> str:
    blocks = []
    for response in responses:
        blocks.append(
            "\n".join(
                [
                    f"Provider: {response.provider}",
                    f"Model: {response.model}",
                    "Response:",
                    response.content,
                ]
            )
        )
    return "\n\n---\n\n".join(blocks)


def _build_judge_prompt(prompt: str, responses_text: str) -> str:
    return (
        "Du bist ein Analyst, der Antworten verschiedener LLMs vergleicht. "
        "Analysiere die Antworten auf Gemeinsamkeiten, Unterschiede, Stärken, "
        "Schwächen und gib eine kurze Empfehlung. Antworte strukturiert mit "
        "den Abschnitten: Gemeinsamkeiten, Unterschiede, Bewertung, Empfehlung.\n\n"
        f"Ursprungs-Prompt:\n{prompt}\n\n"
        f"Antworten:\n{responses_text}"
    )


def judge_responses(prompt: str, responses: List[LLMResponse]) -> Tuple[str, str]:
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

    responses_text = format_responses(responses)
    judge_prompt = _build_judge_prompt(prompt, responses_text)
    result = judge_client.generate(judge_prompt, max_tokens=512)
    return responses_text, result.content
