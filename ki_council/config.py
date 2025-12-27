import json
import os
from pathlib import Path
from typing import Any, Dict, Optional

CONFIG_ENV_VAR = "KI_COUNCIL_CONFIG"
DEFAULT_CONFIG_NAME = ".ki-council.json"


def load_config() -> Dict[str, Any]:
    config_path = Path(os.getenv(CONFIG_ENV_VAR, DEFAULT_CONFIG_NAME))
    if not config_path.exists():
        return {}

    try:
        raw = config_path.read_text(encoding="utf-8")
        data = json.loads(raw)
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"Failed to read config file: {config_path}") from exc

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
    if env_value:
        return env_value

    config_value = config.get(config_key)
    if config_value is not None:
        return str(config_value)

    return default
