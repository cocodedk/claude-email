"""Email classification and routing logic for the claude-chat system."""
import email.message
import re
from dataclasses import dataclass

from src.chat_db import ChatDB
from src.executor import extract_command

_RE_PREFIX = re.compile(r"^(?:re:\s*)+", re.IGNORECASE)
_META_COMMANDS = {"status", "spawn", "restart"}


@dataclass
class Route:
    kind: str  # "chat_reply", "agent_command", "meta", "cli"
    agent_name: str = ""
    body: str = ""
    original_message_id: int = 0
    meta_command: str = ""
    meta_args: str = ""


def _strip_subject_prefix(subject: str, auth_prefix: str) -> str:
    """Strip Re: prefixes and the AUTH:secret prefix from a subject line.

    Returns just the command part, stripped of whitespace.
    """
    cleaned = _RE_PREFIX.sub("", subject).strip()
    if cleaned.startswith(auth_prefix):
        cleaned = cleaned[len(auth_prefix):].strip()
    return cleaned


def classify_email(
    message: email.message.Message,
    db: ChatDB,
    auth_prefix: str,
) -> Route:
    """Classify an incoming email and return a Route describing how to handle it.

    Priority order:
    1. Chat reply (In-Reply-To matches a known email_message_id in DB)
    2. Agent command (subject starts with @agent-name)
    3. Meta-command (subject starts with status/spawn/restart)
    4. CLI fallback
    """
    # 1. Chat reply: check In-Reply-To header
    in_reply_to = message.get("In-Reply-To", "").strip()
    if in_reply_to:
        original = db.find_message_by_email_id(in_reply_to)
        if original is not None:
            return Route(
                kind="chat_reply",
                agent_name=original["from_name"],
                original_message_id=original["id"],
            )

    # Strip subject to get the command part
    subject = message.get("Subject", "")
    command = _strip_subject_prefix(subject, auth_prefix)

    # 2. Agent command: starts with @agent-name
    if command.startswith("@"):
        parts = command.split(None, 1)
        agent_name = parts[0][1:]  # strip the leading @
        body = extract_command(message)
        return Route(
            kind="agent_command",
            agent_name=agent_name,
            body=body,
        )

    # 3. Meta-command: starts with status, spawn, or restart
    first_word = command.split(None, 1)[0].lower() if command else ""
    if first_word in _META_COMMANDS:
        remaining = command.split(None, 1)
        meta_args = remaining[1] if len(remaining) > 1 else ""
        return Route(
            kind="meta",
            meta_command=first_word,
            meta_args=meta_args,
        )

    # 4. CLI fallback
    return Route(kind="cli")
