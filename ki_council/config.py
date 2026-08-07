"""Configuration management for KI-Council.

This module handles loading and parsing configuration from JSON files and environment variables.
Supports lenient JSON parsing with comments, trailing commas, and smart quotes.
"""

import json
import os
import re
from pathlib import Path
from typing import Any, Dict, Optional

CONFIG_ENV_VAR = "KI_COUNCIL_CONFIG"
DEFAULT_CONFIG_NAME = ".ki-council.json"
XDG_CONFIG_HOME_ENV_VAR = "XDG_CONFIG_HOME"
USER_CONFIG_DIR_NAME = "ki-council"
USER_CONFIG_NAMES = ("config.json", DEFAULT_CONFIG_NAME)


# Zeilen- und Blockkommentar bewusst getrennt: ".*$" darf NICHT unter re.DOTALL
# laufen, sonst frisst der erste "//"-Kommentar den Rest der Datei.
_LINE_COMMENT_PATTERN = re.compile(r"^[ \t]*(//|#).*$", re.MULTILINE)
_BLOCK_COMMENT_PATTERN = re.compile(r"/\*.*?\*/", re.DOTALL)
_TRAILING_COMMA_PATTERN = re.compile(r",\s*([}\]])")
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
    """Search for the default config file in current and parent directories.

    Returns:
        Path to the config file if found, None otherwise
    """
    for candidate_dir in [Path.cwd(), *Path.cwd().parents]:
        candidate = candidate_dir / DEFAULT_CONFIG_NAME
        if candidate.is_file():
            return candidate
    return None


def user_config_dir() -> Path:
    """Return the per-user configuration directory for KI-Council.

    Follows the XDG base directory spec: ``$XDG_CONFIG_HOME/ki-council`` if
    that variable is set, otherwise ``~/.config/ki-council``.

    Returns:
        Path to the directory (which may not exist)
    """
    xdg_home = os.getenv(XDG_CONFIG_HOME_ENV_VAR)
    base = Path(xdg_home).expanduser() if xdg_home else Path.home() / ".config"
    return base / USER_CONFIG_DIR_NAME


def _find_user_config_path() -> Optional[Path]:
    """Search for a config file in the per-user configuration directory.

    Returns:
        Path to the config file if found, None otherwise
    """
    directory = user_config_dir()
    for name in USER_CONFIG_NAMES:
        candidate = directory / name
        if candidate.is_file():
            return candidate
    return None


def resolve_config_path() -> Optional[Path]:
    """Resolve the path to the configuration file.

    Search order (first hit wins):

    1. The ``KI_COUNCIL_CONFIG`` environment variable (also set by ``--config``)
    2. ``.ki-council.json`` in the current or any parent directory
    3. ``config.json`` (or ``.ki-council.json``) in the per-user config
       directory, see :func:`user_config_dir`

    Returns:
        Path to the config file if found, None otherwise
    """
    configured_path = os.getenv(CONFIG_ENV_VAR)
    if configured_path:
        return Path(configured_path).expanduser()
    return _find_default_config_path() or _find_user_config_path()


def load_config() -> Dict[str, Any]:
    """Load and parse the configuration file.

    Performs lenient JSON parsing that tolerates:
    - Comments (// single-line, # shell-style, /* multi-line */)
    - Trailing commas
    - Smart quotes (" " ' ' etc.)

    Returns:
        Dictionary with configuration values, or empty dict if no config file found

    Raises:
        RuntimeError: If config file cannot be read or parsed, or doesn't contain a JSON object
    """
    config_path = resolve_config_path()
    if not config_path:
        return {}

    try:
        raw = config_path.read_text(encoding="utf-8").lstrip("\ufeff")
        sanitized = raw.translate(_SMART_QUOTE_TRANSLATION)
        sanitized = _BLOCK_COMMENT_PATTERN.sub("", sanitized)
        sanitized = _LINE_COMMENT_PATTERN.sub("", sanitized)
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
    """Get a configuration setting from environment or config file.

    Environment variables take precedence over config file values.

    Args:
        config: Configuration dictionary from load_config()
        env_key: Environment variable name to check
        config_key: Key to look up in config dictionary
        default: Default value if setting not found

    Returns:
        Setting value (stripped of whitespace), or default if not found
    """
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
