"""SMTP email sender — sends command results back to the requester."""
import email.message
import logging
import smtplib
import ssl

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
) -> str:
    """Send a plain-text reply via SMTP_SSL with verified TLS.

    Creates a fresh connection per send to avoid stale-connection issues in
    long-running service deployments.

    Returns the Message-ID of the sent email.
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
    msg.set_content(body)

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
