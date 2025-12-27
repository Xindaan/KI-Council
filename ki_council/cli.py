import argparse
import json
import os
import sys

from ki_council.council import gather_responses, judge_responses
from ki_council.config import CONFIG_ENV_VAR


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Send prompts to multiple LLMs and compare.")
    parser.add_argument("prompt", help="Prompt to send to the council.")
    parser.add_argument(
        "--max-tokens",
        type=int,
        default=512,
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
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()

    if args.config:
        os.environ[CONFIG_ENV_VAR] = args.config

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
