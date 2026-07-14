"""Load evaluation prompt sets from files, folders, and chat history exports.

Supported sources (auto-detected from the path):
- Directory: every .txt/.md file is one prompt (filename = prompt id).
- .jsonl file: one prompt per line; either a JSON string, or an object with
  a "prompt", "text", or "input" key.
- .txt/.md file: one prompt per non-empty line (quick ad-hoc sets).
- .json file: a ChatGPT data export (conversations.json with "mapping"
  nodes) or a Claude data export (conversations.json with "chat_messages").
  The first user message of each conversation is extracted as one prompt --
  multi-turn context is intentionally not replayed.
"""

import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any, List, Optional

logger = logging.getLogger(__name__)

MIN_PROMPT_CHARS = 8  # skip greetings/fragments that cannot be judged


@dataclass
class PromptItem:
    """One prompt to evaluate, with a stable id and its origin."""
    id: str
    text: str
    source: str


class PromptSetError(RuntimeError):
    """Raised when a prompt source cannot be read or understood."""
    pass


def _clean(text: Any) -> str:
    if not isinstance(text, str):
        return ""
    return text.strip()


def _dedupe(items: List[PromptItem]) -> List[PromptItem]:
    seen = set()
    unique: List[PromptItem] = []
    for item in items:
        key = " ".join(item.text.split()).lower()
        if key in seen:
            continue
        seen.add(key)
        unique.append(item)
    return unique


def _load_directory(path: Path) -> List[PromptItem]:
    items: List[PromptItem] = []
    for file in sorted(path.iterdir()):
        if file.suffix.lower() not in {".txt", ".md"} or not file.is_file():
            continue
        text = _clean(file.read_text(encoding="utf-8"))
        if text:
            items.append(PromptItem(id=file.stem, text=text, source=str(file)))
    return items


def _load_jsonl(path: Path) -> List[PromptItem]:
    items: List[PromptItem] = []
    for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        line = line.strip()
        if not line:
            continue
        try:
            data = json.loads(line)
        except json.JSONDecodeError:
            logger.warning(f"{path.name}:{lineno}: not valid JSON, line skipped")
            continue
        if isinstance(data, str):
            text = _clean(data)
        elif isinstance(data, dict):
            text = _clean(data.get("prompt") or data.get("text") or data.get("input"))
        else:
            text = ""
        if text:
            item_id = str(data.get("id")) if isinstance(data, dict) and data.get("id") else f"line-{lineno}"
            items.append(PromptItem(id=item_id, text=text, source=f"{path}:{lineno}"))
        else:
            logger.warning(f"{path.name}:{lineno}: no 'prompt'/'text'/'input' key, line skipped")
    return items


def _load_lines(path: Path) -> List[PromptItem]:
    items: List[PromptItem] = []
    for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        text = _clean(line)
        if text:
            items.append(PromptItem(id=f"line-{lineno}", text=text, source=f"{path}:{lineno}"))
    return items


def _first_user_message_chatgpt(conversation: dict) -> Optional[str]:
    """Extract the first user message from a ChatGPT export conversation.

    ChatGPT exports store messages as a "mapping" of nodes; ordering is by
    the per-message create_time. Non-text parts (images etc.) are ignored.
    """
    mapping = conversation.get("mapping")
    if not isinstance(mapping, dict):
        return None
    user_messages = []
    for node in mapping.values():
        message = node.get("message") if isinstance(node, dict) else None
        if not isinstance(message, dict):
            continue
        author = message.get("author") or {}
        if author.get("role") != "user":
            continue
        content = message.get("content") or {}
        parts = content.get("parts") or []
        text = "\n".join(_clean(part) for part in parts if isinstance(part, str)).strip()
        if text:
            user_messages.append((message.get("create_time") or 0, text))
    if not user_messages:
        return None
    user_messages.sort(key=lambda pair: pair[0])
    return user_messages[0][1]


def _first_user_message_claude(conversation: dict) -> Optional[str]:
    """Extract the first human message from a Claude export conversation.

    Handles both the flat "text" field and newer block-style "content" lists.
    """
    messages = conversation.get("chat_messages")
    if not isinstance(messages, list):
        return None
    for message in messages:
        if not isinstance(message, dict) or message.get("sender") != "human":
            continue
        text = _clean(message.get("text"))
        if not text:
            blocks = message.get("content") or []
            chunks = [
                _clean(block.get("text"))
                for block in blocks
                if isinstance(block, dict) and block.get("type") == "text"
            ]
            text = "\n".join(chunk for chunk in chunks if chunk).strip()
        if text:
            return text
    return None


def _load_history_export(path: Path) -> List[PromptItem]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise PromptSetError(f"Not valid JSON: {path} ({exc})") from exc

    if not isinstance(data, list):
        raise PromptSetError(
            f"Unrecognized JSON structure in {path}. Expected a ChatGPT or Claude "
            "conversations.json export (a JSON array of conversations)."
        )

    items: List[PromptItem] = []
    for index, conversation in enumerate(data):
        if not isinstance(conversation, dict):
            continue
        try:
            if "mapping" in conversation:
                text = _first_user_message_chatgpt(conversation)
                title = conversation.get("title")
            elif "chat_messages" in conversation:
                text = _first_user_message_claude(conversation)
                title = conversation.get("name")
            else:
                continue
        except Exception as exc:
            logger.warning(f"Skipping conversation {index} in {path.name}: {exc}")
            continue
        if text:
            item_id = _clean(title) or f"conversation-{index}"
            items.append(PromptItem(id=item_id, text=text, source=f"{path}#'{item_id}'"))

    if not items:
        raise PromptSetError(
            f"No usable prompts found in {path}. Supported: ChatGPT export "
            "(conversations with 'mapping') or Claude export (conversations "
            "with 'chat_messages')."
        )
    return items


def load_prompts(source: str, limit: Optional[int] = None) -> List[PromptItem]:
    """Load, filter, and dedupe prompts from a source path.

    Prompts shorter than MIN_PROMPT_CHARS are dropped (not judgeable).
    Exact duplicates (whitespace/case-insensitive) are dropped. If limit is
    set, the first `limit` prompts in source order are kept -- for history
    exports that is export order, which puts recent conversations first.
    """
    path = Path(source).expanduser()
    if not path.exists():
        raise PromptSetError(f"Prompt source not found: {path}")

    if path.is_dir():
        items = _load_directory(path)
    elif path.suffix.lower() == ".jsonl":
        items = _load_jsonl(path)
    elif path.suffix.lower() == ".json":
        items = _load_history_export(path)
    elif path.suffix.lower() in {".txt", ".md"}:
        items = _load_lines(path)
    else:
        raise PromptSetError(
            f"Unsupported prompt source: {path} (expected a directory or a "
            ".jsonl/.json/.txt/.md file)"
        )

    total_loaded = len(items)
    items = [item for item in items if len(item.text) >= MIN_PROMPT_CHARS]
    items = _dedupe(items)
    dropped = total_loaded - len(items)
    if dropped:
        logger.info(f"Dropped {dropped} prompt(s) (too short or duplicate)")

    if limit is not None and limit > 0 and len(items) > limit:
        logger.info(f"Limiting prompt set from {len(items)} to first {limit} prompt(s)")
        items = items[:limit]

    if not items:
        raise PromptSetError(f"No usable prompts in {path}")
    return items
