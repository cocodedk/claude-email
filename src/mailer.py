"""SMTP email sender — sends command results back to the requester."""
import email.message
import email.utils
import logging
import smtplib
import ssl
from typing import Any

logger = logging.getLogger(__name__)


def send_reply(
    smtp_host: str,
    smtp_port: int,
    username: str,
    password: str,
    to: str,
    subject: str,
    body: str,
    in_reply_to: str = "",
    references: str = "",
    email_domain: str = "",
    content_type: str = "text/plain",
) -> str:
    """Send a reply via SMTP_SSL with verified TLS.

    content_type defaults to text/plain. Pass "application/json" to send
    structured-client envelopes; body must already be the serialized
    payload in that case.

    Creates a fresh connection per send to avoid stale-connection issues in
    long-running service deployments. Returns the Message-ID of the sent email.
    """
    msg = email.message.EmailMessage()
    msg["From"] = username
    msg["To"] = to
    clean_subject = " ".join(subject.splitlines()).strip()
    msg["Subject"] = clean_subject if clean_subject.startswith("Re:") else f"Re: {clean_subject}"
    if in_reply_to:
        msg["In-Reply-To"] = " ".join(in_reply_to.splitlines()).strip()
    if references:
        msg["References"] = " ".join(references.splitlines()).strip()
    maintype, _, subtype = content_type.partition("/")
    if maintype == "text" or not maintype:
        msg.set_content(body, subtype=subtype or "plain")
    else:
        msg.set_content(
            body.encode("utf-8"), maintype=maintype, subtype=subtype or "octet-stream",
        )
        msg.replace_header("Content-Type", f"{content_type}; charset=utf-8")
    msg["Message-ID"] = email.utils.make_msgid(domain=email_domain) if email_domain else email.utils.make_msgid()

    ctx = ssl.create_default_context()
    try:
        with smtplib.SMTP_SSL(smtp_host, smtp_port, context=ctx) as smtp:
            smtp.login(username, password)
            smtp.send_message(msg)
            logger.info("Reply sent to %s (subject: %r)", to, msg["Subject"])
    except smtplib.SMTPException as exc:
        logger.error("Failed to send reply: %s", exc)
        raise
    return msg["Message-ID"] or ""


def send_and_record(
    chat_db: Any, *, kind: str, sender_agent: str = "", **send_kwargs: Any,
) -> str:
    """Send a reply via send_reply and persist its Message-ID.

    Every outbound mail we want to be reply-able routes through here so
    security.is_authorized can thread-match the user's reply via the
    outbound_emails table. Recording happens *after* a successful SMTP
    handshake — if send_reply raises, no row is written.

    ``kind`` tags the row for audit/debug ("ack", "result", "ask",
    "notify", "envelope_reply", ...). ``sender_agent`` is the agent name
    when known, empty for service-level mails (CLI fallback, JSON
    handler errors).
    """
    msg_id = send_reply(**send_kwargs)
    if msg_id:
        chat_db.record_outbound_email(
            msg_id, kind=kind, sender_agent=sender_agent,
        )
    return msg_id
