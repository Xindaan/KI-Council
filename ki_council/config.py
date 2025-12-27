import json
import os
import re
from pathlib import Path
from typing import Any, Dict, Optional

CONFIG_ENV_VAR = "KI_COUNCIL_CONFIG"
DEFAULT_CONFIG_NAME = ".ki-council.json"


_COMMENT_PATTERN = re.compile(r"(^\\s*(//|#).*$)|(/\\*.*?\\*/)", re.MULTILINE | re.DOTALL)
_TRAILING_COMMA_PATTERN = re.compile(r",\\s*([}\\]])")
_SMART_QUOTE_TRANSLATION = str.maketrans(
    {
        "“": '"',
        "”": '"',
        "„": '"',
        "‟": '"',
        "’": "'",
        "‘": "'",
        "‚": "'",
        "‛": "'",
    }
)


def _find_default_config_path() -> Optional[Path]:
    for candidate_dir in [Path.cwd(), *Path.cwd().parents]:
        candidate = candidate_dir / DEFAULT_CONFIG_NAME
        if candidate.is_file():
            return candidate
    return None


def resolve_config_path() -> Optional[Path]:
    configured_path = os.getenv(CONFIG_ENV_VAR)
    if configured_path:
        return Path(configured_path).expanduser()
    return _find_default_config_path()


def load_config() -> Dict[str, Any]:
    config_path = resolve_config_path()
    if not config_path:
        return {}

    try:
        raw = config_path.read_text(encoding="utf-8").lstrip("\ufeff")
        sanitized = raw.translate(_SMART_QUOTE_TRANSLATION)
        sanitized = _COMMENT_PATTERN.sub("", sanitized)
        sanitized = _TRAILING_COMMA_PATTERN.sub(r"\1", sanitized)
        data = json.loads(sanitized)
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(
            f"Failed to read config file: {config_path} ({exc})"
        ) from exc

    if not isinstance(data, dict):
        raise RuntimeError(f"Config file must contain a JSON object: {config_path}")

    return data


def get_setting(
    config: Dict[str, Any],
    env_key: str,
    config_key: str,
    default: Optional[str] = None,
) -> Optional[str]:
    env_value = os.getenv(env_key)
    if env_value is not None:
        env_value = env_value.strip()
        if env_value:
            return env_value

    config_value = config.get(config_key)
    if config_value is not None:
        value = str(config_value).strip()
        if value:
            return value

    return default
