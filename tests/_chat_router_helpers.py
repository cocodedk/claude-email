"""Shared helpers for tests of src/chat_router.py."""
import email.message


AUTH_PREFIX = "AUTH:mysecret"


def _make_msg(
    subject: str = "",
    body: str = "",
    in_reply_to: str = "",
) -> email.message.EmailMessage:
    msg = email.message.EmailMessage()
    if subject:
        msg["Subject"] = subject
    if in_reply_to:
        msg["In-Reply-To"] = in_reply_to
    if body:
        msg.set_content(body)
    return msg
