import argparse
import json
import logging
import os
import sys

from ki_council.council import gather_responses, judge_responses
from ki_council.config import CONFIG_ENV_VAR, get_setting, load_config, resolve_config_path


def build_parser() -> argparse.ArgumentParser:
    """Build the command-line argument parser.

    Returns:
        Configured ArgumentParser instance
    """
    parser = argparse.ArgumentParser(
        description="Send prompts to multiple LLMs and compare their responses.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("prompt", help="Prompt to send to the council.")
    parser.add_argument(
        "--max-tokens",
        type=int,
        default=4096,
        help="Max tokens per model response (default: 4096).",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Output raw JSON instead of formatted text.",
    )
    parser.add_argument(
        "--config",
        help="Path to a config file to use for this run.",
    )
    parser.add_argument(
        "--providers",
        help="Comma-separated list of providers to use (e.g., 'openai,anthropic'). If not specified, uses all configured providers.",
    )
    parser.add_argument(
        "--verbose",
        "-v",
        action="store_true",
        help="Enable verbose logging output.",
    )
    parser.add_argument(
        "--debug",
        action="store_true",
        help="Print provider configuration diagnostics (keys are masked).",
    )
    return parser


def setup_logging(verbose: bool = False) -> None:
    """Configure logging for the application.

    Args:
        verbose: If True, set log level to INFO; otherwise WARNING
    """
    level = logging.INFO if verbose else logging.WARNING
    logging.basicConfig(
        level=level,
        format="%(levelname)s: %(message)s",
        stream=sys.stderr,
    )


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
    """Main entry point for the CLI.

    Returns:
        Exit code (0 for success, non-zero for errors)
    """
    parser = build_parser()
    args = parser.parse_args()

    # Setup logging
    setup_logging(verbose=args.verbose)

    # Set config path if provided
    if args.config:
        os.environ[CONFIG_ENV_VAR] = args.config

    # Print debug info if requested
    if args.debug:
        _print_debug_info()

    # Set providers filter if specified
    if args.providers:
        os.environ["KI_COUNCIL_PROVIDERS"] = args.providers
        logging.info(f"Using providers: {args.providers}")

    try:
        # Gather responses from all providers
        responses = gather_responses(args.prompt, max_tokens=args.max_tokens)

        # Check if we got any successful responses
        successful_responses = [r for r in responses if not r.error]
        if not successful_responses:
            print("Error: All providers failed to generate responses.", file=sys.stderr)
            for response in responses:
                if response.error:
                    print(f"  {response.provider}: {response.error}", file=sys.stderr)
            return 1

        # Generate comparison using judge
        responses_text, judgment = judge_responses(args.prompt, responses)

        # Output results
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

    except KeyboardInterrupt:
        print("\nAborted by user.", file=sys.stderr)
        return 130
    except Exception as exc:
        logging.error(f"Unexpected error: {exc}", exc_info=args.verbose)
        print(f"Error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
