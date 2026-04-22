"""Per-sender universe config — one isolated chat-chat world per sender.

A universe bundles everything the email pipeline needs to dispatch a
particular sender's task: allowed project base, chat-chat DB, SSE URL,
MCP config for spawned workers, the systemd unit name, AND its own auth
credentials (shared secret + GPG fingerprint). Credential isolation
means a compromised test sender can't authenticate against prod.

The primary universe is always present (AUTHORIZED_SENDER + prod
chat-chat on 8420, reading SHARED_SECRET / GPG_FINGERPRINT from .env).

An optional test universe is added when a .env.test file is present:
its contents (SENDER, SHARED_SECRET, CLAUDE_CWD, CHAT_*, ROUTER_MCP_CONFIG,
GPG_FINGERPRINT, GPG_HOME) are read as a *separate* dict — NOT merged into
os.environ — so a test secret cannot accidentally leak into the primary
auth gate.
"""
import os
from dataclasses import dataclass, field


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
    shared_secret: str = ""
    gpg_fingerprint: str = ""
    gpg_home: str | None = None
    is_test: bool = False
    aliases: tuple[str, ...] = field(default_factory=tuple)

    @property
    def auth_prefix(self) -> str:
        return f"AUTH:{self.shared_secret}"

    @property
    def all_senders(self) -> tuple[str, ...]:
        """Canonical sender first, then any aliases. Every address in this
        tuple is authorized against the same creds/DB/project base; used by
        dispatch to route any matching From back to this universe."""
        return (self.sender, *self.aliases)


def _parse_senders(raw: str) -> tuple[str, tuple[str, ...]]:
    """Split ``"a@x,b@x,c@x"`` into (canonical, (alias, alias)).

    Strips whitespace, drops empties, preserves order. Raises ValueError
    when no usable address remains so a mis-set AUTHORIZED_SENDER fails
    loudly at startup instead of silently producing a no-sender universe.
    """
    parts = [p.strip() for p in raw.split(",") if p.strip()]
    if not parts:
        raise ValueError("AUTHORIZED_SENDER must contain at least one address")
    return parts[0], tuple(parts[1:])


def _repo_root() -> str:
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def build_universes(env: dict | None = None, test_env: dict | None = None) -> list[Universe]:
    """Return ordered list of universes from the environment.

    Primary first, test (if test_env has SENDER set) second.
    test_env is a separate dict — typically the parsed contents of .env.test
    via dotenv_values(). It is NOT read from os.environ to keep test creds
    from leaking into the primary universe.
    """
    src = env if env is not None else os.environ
    canonical, aliases = _parse_senders(src["AUTHORIZED_SENDER"])
    primary = Universe(
        sender=canonical,
        aliases=aliases,
        allowed_base=src["CLAUDE_CWD"],
        chat_db_path=src["CHAT_DB_PATH"],
        chat_url=src["CHAT_URL"],
        mcp_config=os.path.join(_repo_root(), ".mcp.json"),
        service_name_chat=src["SERVICE_NAME_CHAT"],
        shared_secret=src.get("SHARED_SECRET", ""),
        gpg_fingerprint=src.get("GPG_FINGERPRINT", ""),
        gpg_home=src.get("GPG_HOME") or None,
        is_test=False,
    )
    out = [primary]

    if test_env and test_env.get("SENDER", "").strip():
        out.append(Universe(
            sender=test_env["SENDER"].strip(),
            allowed_base=test_env.get("CLAUDE_CWD") or src["CLAUDE_CWD"],
            chat_db_path=test_env.get("CHAT_DB_PATH", _DEFAULT_TEST_CHAT_DB),
            chat_url=test_env.get("CHAT_URL", _DEFAULT_TEST_CHAT_URL),
            mcp_config=os.path.join(_repo_root(), ".mcp-test.json"),
            service_name_chat=test_env.get("SERVICE_NAME_CHAT", _DEFAULT_TEST_SERVICE),
            shared_secret=test_env.get("SHARED_SECRET", ""),
            gpg_fingerprint=test_env.get("GPG_FINGERPRINT", ""),
            gpg_home=test_env.get("GPG_HOME") or None,
            is_test=True,
        ))
    return out
