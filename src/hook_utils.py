"""Shared helpers for claude-email hook scripts.

Extracted from chat-precompact-hook.py to avoid duplication across hook
scripts. Import these instead of re-implementing in each script.
"""
import json
import os
import sys
from pathlib import Path, PurePosixPath

from src.agent_name import ENV_VAR_NAME, validated_agent_name


def resolved_db_path(root: Path) -> Path:
    """Return the absolute Path to the chat DB, or raise RuntimeError."""
    raw = os.environ.get("CHAT_DB_PATH", "")
    if not raw:
        raise RuntimeError(
            "CHAT_DB_PATH not set — expected it in .env (e.g. claude-chat.db).",
        )
    p = Path(raw)
    return p if p.is_absolute() else root / p


def caller_name() -> str:
    """Return the agent name from env, falling back to cwd basename."""
    fallback = "agent-" + PurePosixPath(os.getcwd()).name
    return validated_agent_name(os.environ.get(ENV_VAR_NAME), fallback)


def read_hook_payload() -> dict:
    """Read and parse the JSON hook payload from stdin. Returns {} on any failure."""
    try:
        if sys.stdin.isatty():
            return {}
        data = sys.stdin.read()
    except (OSError, ValueError):
        return {}
    if not data.strip():
        return {}
    try:
        return json.loads(data)
    except json.JSONDecodeError:
        return {}
