"""SMTP email sender — sends command results back to the requester."""
import email.message
import email.utils
import logging
import smtplib
import ssl

logger = logging.getLogger(__name__)


def _one_line(value: str) -> str:
    """Collapse a header value onto one line — the header-injection defence."""
    return " ".join(value.splitlines()).strip()


def _reply_subject(subject: str) -> str:
    """Prefix ``Re: `` unless the subject already carries one, in any case."""
    clean = _one_line(subject)
    return clean if clean[:3].lower() == "re:" else f"Re: {clean}"


def _references_chain(references: str, in_reply_to: str) -> str:
    """Build an RFC 5322 References chain: parent's chain, then the parent id."""
    chain = _one_line(references).split()
    parent = _one_line(in_reply_to)
    if parent and parent not in chain:
        chain.append(parent)
    return " ".join(chain)


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
    msg["To"] = _one_line(to)
    msg["Subject"] = _reply_subject(subject)
    if in_reply_to:
        msg["In-Reply-To"] = _one_line(in_reply_to)
    chain = _references_chain(references, in_reply_to)
    if chain:
        msg["References"] = chain
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
