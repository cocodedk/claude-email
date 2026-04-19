"""Subject-tag + footer helpers for outbound system emails.

Tags make the inbox scannable — `[Question]`, `[Update]`, `[Task done]`,
`[Queued]`, `[Reset ready]`. Footers remind the user what replies do so
they don't have to memorize syntax.

Kept separate so mailer.py stays a pure transport layer and
chat_handlers.py doesn't balloon past the 200-line cap.
"""

FOOTER = (
    "\n\n---\n"
    "Reply to this email → continues in the same project (full session memory).\n"
    "New email → new task, anywhere.\n"
    "`cancel in <project>` → stop the running task.\n"
    "`status of <project>` → see what's running/queued."
)


def prepend_tag(subject: str, tag: str | None) -> str:
    """Prepend [tag] to subject if not already present. No-op when tag is None."""
    if not tag:
        return subject
    marker = f"[{tag}]"
    if marker in subject:
        return subject
    return f"{marker} {subject}" if subject else marker


def with_footer(body: str, enabled: bool = True) -> str:
    return body + FOOTER if enabled else body


def tag_for_message_type(msg_type: str) -> str | None:
    return {
        "ask": "Question",
        "notify": "Update",
        "reply": "Update",
        "command": "Dispatch",
    }.get(msg_type)
