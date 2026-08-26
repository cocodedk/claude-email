#!/usr/bin/env python3
"""Install the claude-chat MCP server entry into every project under a base dir.

Usage:
    scripts/install-chat-mcp.py [--single] TARGET_DIR

Without --single, TARGET_DIR is a parent directory whose child subdirectories
are each treated as a separate project (batch mode).

With --single, TARGET_DIR is a single project directory to install into directly.

TARGET_DIR resolution (first non-empty wins):
    1. first positional argument
    2. $CLAUDE_CWD (loaded from .env by default)

CHAT_URL resolution:
    1. $CHAT_URL (loaded from .env by default)

Both must be set — the script errors out with guidance if either is missing.

Skips (batch mode only):
  - non-directories (loose files, logs, scripts)
  - hidden directories (starting with '.')

Writes per project:
  - .mcp.json  — declares the claude-chat MCP SSE server
  - .claude/settings.local.json — Claude Code hooks:
      * SessionStart: register on the bus + inject the bus guide
      * UserPromptSubmit: drain pending mail into the next turn
      * Stop: reinject peer messages that arrived mid-response so the
        agent keeps the conversation alive without polling
    (scripts live in this repo's scripts/)

Also updates the user's Claude Code config (``$CLAUDE_CONFIG_DIR/.claude.json``,
defaulting to ``~/.claude.json``) to pre-approve the ``claude-chat`` MCP
server for each project — without this, Claude Code silently ignores the
``.mcp.json`` entry on first session start.

Idempotent: all helpers merge into existing files so re-running is safe —
new hook events (e.g. Stop added after an earlier install) get appended
without disturbing third-party hook entries.
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
    CHAT_MCP_SERVER_NAME,
    HOOK_SCRIPT,
    approve_mcp_server_for_project,
    inject_mcp_config,
    inject_session_start_hook,
)

SKIP_NAMES: set[str] = set()


def _parse_args(argv: list[str]) -> tuple[bool, str]:
    single = False
    positional = ""
    for arg in argv[1:]:
        if arg == "--single":
            single = True
        else:
            positional = arg
    return single, positional


def _install_one(project_dir: str, chat_url: str, config_dir: str) -> None:
    inject_mcp_config(project_dir, chat_url)
    inject_session_start_hook(project_dir, HOOK_SCRIPT)
    approve_mcp_server_for_project(config_dir, project_dir, CHAT_MCP_SERVER_NAME)


def _resolve_env() -> tuple[str, str, int]:
    chat_url = os.environ.get("CHAT_URL", "")
    if not chat_url:
        print(
            "error: CHAT_URL not set — expected it in .env "
            "(e.g. http://127.0.0.1:8420/sse).",
            file=sys.stderr,
        )
        return "", "", 2
    config_dir = os.environ.get("CLAUDE_CONFIG_DIR") or os.path.expanduser("~")
    return chat_url, config_dir, 0


def main() -> int:
    single, dir_arg = _parse_args(sys.argv)
    dir_arg = dir_arg or os.environ.get("CLAUDE_CWD", "")
    if not dir_arg:
        print(
            "error: TARGET_DIR not provided.\n"
            "  Pass it as a positional argument, or set CLAUDE_CWD in .env.",
            file=sys.stderr,
        )
        return 2

    target = Path(dir_arg).expanduser().resolve()

    chat_url, config_dir, rc = _resolve_env()
    if rc:
        return rc

    if not target.is_dir():
        print(f"error: {target} is not a directory", file=sys.stderr)
        return 1

    if single:
        return _main_single(target, chat_url, config_dir)
    return _main_batch(target, chat_url, config_dir)


def _main_single(target: Path, chat_url: str, config_dir: str) -> int:
    _install_one(str(target), chat_url, config_dir)
    print(f"Installed claude-chat MCP into {target.name}")
    return 0


def _main_batch(base: Path, chat_url: str, config_dir: str) -> int:
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
            _install_one(str(entry), chat_url, config_dir)
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
