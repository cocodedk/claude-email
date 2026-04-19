#!/usr/bin/env python3
"""Install the claude-chat MCP server entry into every project under a base dir.

Usage:
    scripts/install-chat-mcp.py [BASE_DIR]

BASE_DIR resolution (first non-empty wins):
    1. argv[1]
    2. $CLAUDE_CWD (loaded from .env by default)

CHAT_URL resolution:
    1. $CHAT_URL (loaded from .env by default)

Both must be set — the script errors out with guidance if either is missing.

Skips:
  - non-directories (loose files, logs, scripts)
  - hidden directories (starting with '.')

Writes two files per project:
  - .mcp.json  — declares the claude-chat MCP SSE server
  - .claude/settings.json — SessionStart hook telling the agent to register
    and how to use the bus (script lives in this repo's scripts/)

Idempotent: both helpers merge into existing files so re-running is safe.
"""
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

try:
    from dotenv import load_dotenv
    load_dotenv(ROOT / ".env")
except ImportError:
    pass

from src.spawner import (  # noqa: E402
    HOOK_SCRIPT,
    inject_mcp_config,
    inject_session_start_hook,
)

SKIP_NAMES: set[str] = set()


def main() -> int:
    base_arg = sys.argv[1] if len(sys.argv) > 1 else os.environ.get("CLAUDE_CWD", "")
    if not base_arg:
        print(
            "error: BASE_DIR not provided.\n"
            "  Pass it as the first argument, or set CLAUDE_CWD in .env.",
            file=sys.stderr,
        )
        return 2
    base = Path(base_arg).expanduser().resolve()

    chat_url = os.environ.get("CHAT_URL", "")
    if not chat_url:
        print(
            "error: CHAT_URL not set — expected it in .env "
            "(e.g. http://127.0.0.1:8420/sse).",
            file=sys.stderr,
        )
        return 2

    if not base.is_dir():
        print(f"error: {base} is not a directory", file=sys.stderr)
        return 1

    installed: list[str] = []
    skipped: list[tuple[str, str]] = []

    for entry in sorted(base.iterdir()):
        if not entry.is_dir():
            skipped.append((entry.name, "not a directory"))
            continue
        if entry.name.startswith("."):
            skipped.append((entry.name, "hidden"))
            continue
        if entry.name in SKIP_NAMES:
            skipped.append((entry.name, "excluded (hosts server)"))
            continue
        try:
            inject_mcp_config(str(entry), chat_url)
            inject_session_start_hook(str(entry), HOOK_SCRIPT)
            installed.append(entry.name)
        except Exception as exc:  # noqa: BLE001
            skipped.append((entry.name, f"error: {exc}"))

    print(f"Installed claude-chat MCP into {len(installed)} project(s):")
    for name in installed:
        print(f"  + {name}")

    if skipped:
        print(f"\nSkipped {len(skipped)}:")
        for name, reason in skipped:
            print(f"  - {name}  ({reason})")

    return 0


if __name__ == "__main__":
    sys.exit(main())
