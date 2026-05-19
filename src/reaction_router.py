"""Outlook reaction email handler — routes thumbs-up/heart/etc. as ack signals.

A reaction email (body shape "[<reaction-tag>] <Name> reacted to your message")
should never spawn a worker. Positive reactions on a pending chat_ask answer
"yes"; negative reactions answer "no"; anything else records a lightweight
ack on the bus and sends an ACK email so the user knows it was received.
"""
import re
from typing import Tuple

_REACTION_RE = re.compile(
    r"^\s*\[(like|heart|thumbsup|thumbsdown|surprised|laugh|sad|celebrate)\]"
    r".*?reacted to your (?:message|post|email)",
    re.IGNORECASE | re.DOTALL,
)

_POSITIVE_REACTIONS = frozenset({"like", "heart", "thumbsup", "celebrate", "surprised", "laugh"})
_NEGATIVE_REACTIONS = frozenset({"thumbsdown", "sad"})


def extract_reaction(body: str) -> str | None:
    """Return the reaction tag (lowercase) if the body matches the Outlook
    reaction shape, else None."""
    m = _REACTION_RE.match(body or "")
    return m.group(1).lower() if m else None


def handle_reaction(
    chat_db, *, agent_name: str, original_message_id: int, reaction: str,
) -> Tuple[str, str]:
    """Record a reaction on the bus and return (ack_body, subject_tag).

    Never enqueues a task. For chat_ask originals, inserts a yes/no reply
    so the blocking ask returns. For other originals, inserts a lightweight
    ack message for the audit log.
    """
    original = chat_db.get_message(original_message_id)
    is_ask = original is not None and original.get("type") == "ask"
    if is_ask:
        answer = "yes" if reaction in _POSITIVE_REACTIONS else "no"
        chat_db.insert_message(
            "user", agent_name, answer, "reply",
            in_reply_to=original_message_id,
        )
        ack = (
            f"Recorded {reaction} as '{answer}' on the pending question "
            f"for {agent_name} (noted, no work queued)."
        )
        return ack, "Reaction"
    chat_db.insert_message(
        "user", agent_name, f"[{reaction}]", "reply",
        in_reply_to=original_message_id,
    )
    ack = f"Got your {reaction} — noted, no work queued."
    return ack, "Reaction"
