"""Per-sender universe config — a universe is one isolated chat-chat world.

A "universe" bundles everything the email pipeline needs to dispatch a
particular sender's task: the allowed project base, the chat-chat DB,
the SSE URL, the MCP config for spawned workers, and the systemd unit
that runs that chat-chat instance.

The primary universe is always present (user@example.com + the prod chat-chat
on 8420). An optional test universe is added when TEST_SENDER is set,
pointed at the isolated claude-chat-test.service on a disjoint path.

Keeping the data in one module avoids spreading 12 env-var lookups
across main.py's config builder.
"""
import os
from dataclasses import dataclass


_DEFAULT_TEST_CHAT_PORT = "8421"
_DEFAULT_TEST_CHAT_URL = f"http://127.0.0.1:{_DEFAULT_TEST_CHAT_PORT}/sse"
_DEFAULT_TEST_CHAT_DB = "claude-chat-test.db"
_DEFAULT_TEST_SERVICE = "claude-chat-test.service"


@dataclass
class Universe:
    sender: str
    allowed_base: str
    chat_db_path: str
    chat_url: str
    mcp_config: str
    service_name_chat: str
    is_test: bool = False


def _repo_root() -> str:
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def build_universes(env: dict | None = None) -> list[Universe]:
    """Return ordered list of universes from the environment.

    Primary first, test (if configured) second.
    """
    src = env if env is not None else os.environ
    primary = Universe(
        sender=src["AUTHORIZED_SENDER"],
        allowed_base=src["CLAUDE_CWD"],
        chat_db_path=src["CHAT_DB_PATH"],
        chat_url=src["CHAT_URL"],
        mcp_config=os.path.join(_repo_root(), ".mcp.json"),
        service_name_chat=src["SERVICE_NAME_CHAT"],
        is_test=False,
    )
    out = [primary]

    test_sender = src.get("TEST_SENDER", "").strip()
    if test_sender:
        out.append(Universe(
            sender=test_sender,
            allowed_base=src.get("TEST_CLAUDE_CWD") or src["CLAUDE_CWD"],
            chat_db_path=src.get("TEST_CHAT_DB_PATH", _DEFAULT_TEST_CHAT_DB),
            chat_url=src.get("TEST_CHAT_URL", _DEFAULT_TEST_CHAT_URL),
            mcp_config=os.path.join(_repo_root(), ".mcp-test.json"),
            service_name_chat=src.get("TEST_SERVICE_NAME_CHAT", _DEFAULT_TEST_SERVICE),
            is_test=True,
        ))
    return out
