import argparse
import json
import os
import sys

from ki_council.council import gather_responses, judge_responses
from ki_council.config import CONFIG_ENV_VAR, get_setting, load_config, resolve_config_path


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Send prompts to multiple LLMs and compare.")
    parser.add_argument("prompt", help="Prompt to send to the council.")
    parser.add_argument(
        "--max-tokens",
        type=int,
        default=2048,
        help="Max tokens per model response.",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Output raw JSON instead of formatted text.",
    )
    parser.add_argument(
        "--config",
        help="Path to a .ki-council.json file to use for this run.",
    )
    parser.add_argument(
        "--debug",
        action="store_true",
        help="Print provider configuration diagnostics (keys are masked).",
    )
    return parser


def _mask_secret(value: str) -> str:
    if len(value) <= 8:
        return "***"
    return f"{value[:4]}...{value[-4:]}"


def _print_debug_info() -> None:
    config_path = resolve_config_path()
    config = load_config()
    lines = [
        f"Config path: {config_path or 'not found'}",
    ]
    providers = [
        ("openai", "OPENAI_API_KEY", "openai_api_key", "OPENAI_MODEL", "openai_model"),
        ("gemini", "GEMINI_API_KEY", "gemini_api_key", "GEMINI_MODEL", "gemini_model"),
        ("anthropic", "ANTHROPIC_API_KEY", "anthropic_api_key", "ANTHROPIC_MODEL", "anthropic_model"),
    ]
    for provider, key_env, key_cfg, model_env, model_cfg in providers:
        key = get_setting(config, key_env, key_cfg)
        model = get_setting(config, model_env, model_cfg)
        key_display = _mask_secret(key) if key else "missing"
        lines.append(f"{provider}: key={key_display}, model={model or 'default'}")
    print("\n".join(lines), file=sys.stderr)


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()

    if args.config:
        os.environ[CONFIG_ENV_VAR] = args.config

    if args.debug:
        _print_debug_info()

    responses = gather_responses(args.prompt, max_tokens=args.max_tokens)
    responses_text, judgment = judge_responses(args.prompt, responses)

    if args.json:
        payload = {
            "responses": [response.__dict__ for response in responses],
            "comparison": judgment,
        }
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        print("=== Antworten ===\n")
        print(responses_text)
        print("\n=== Vergleich ===\n")
        print(judgment)

    return 0


if __name__ == "__main__":
    sys.exit(main())
