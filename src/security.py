"""Sender authorization for incoming email commands."""
import email.message
import email.utils
import hmac
import logging
import re as _re

from src.email_extract import decode_subject, strip_subject_prefixes
from src.gpg_verify import verify_gpg_signature  # noqa: F401 — re-export

logger = logging.getLogger(__name__)


def _ct_startswith(haystack: str, prefix: str) -> bool:
    """Constant-time prefix check on the secret-bearing portion.

    ``str.startswith`` short-circuits character-by-character which leaks
    a timing oracle on the secret. ``hmac.compare_digest`` runs in time
    proportional to the prefix length only — fine to use for a known-
    length comparison since attackers already know how long the secret
    is from any leaked email.
    """
    if len(haystack) < len(prefix):
        return False
    return hmac.compare_digest(haystack[: len(prefix)], prefix)


def _extract_body_text(message: email.message.Message) -> str:
    """Return the message body as plain text, concatenating all text parts.

    Used to scan for the AUTH:<secret> token in the body — covers quoted
    replies (where the secret lives in the quoted block) and manual
    inclusions. HTML parts are included with tags crudely stripped.
    """
    parts = []
    if message.is_multipart():
        for part in message.walk():
            ct = part.get_content_type()
            if ct not in ("text/plain", "text/html"):
                continue
            payload = part.get_payload(decode=True)
            if not payload:
                continue
            charset = part.get_content_charset() or "utf-8"
            text = payload.decode(charset, errors="replace")
            if ct == "text/html":
                text = _re.sub(r"<[^>]+>", " ", text)
            parts.append(text)
    else:
        payload = message.get_payload(decode=True)
        if payload:
            charset = message.get_content_charset() or "utf-8"
            text = payload.decode(charset, errors="replace")
            if message.get_content_type() == "text/html":
                text = _re.sub(r"<[^>]+>", " ", text)
            parts.append(text)
    return "\n".join(parts)


def _extract_address(header_value: str) -> str:
    """Parse an email address from a header value like 'Name <addr@domain>' or '<addr@domain>'."""
    _, addr = email.utils.parseaddr(header_value)
    return addr.strip().lower()


def _extract_return_path(header_value: str) -> str:
    """Strip angle brackets from Return-Path like '<addr@domain>'."""
    return header_value.strip().strip("<>").lower()


def identify_sender(message: email.message.Message, allowed_senders) -> str | None:
    """Return the allowed sender whose envelope matches this message, or None.

    Accepts a str (backcompat single sender) or any iterable of addresses.
    Checks both From and Return-Path; both must equal one of the allowed
    addresses. Case-insensitive. Returns the canonical lowercased address.
    """
    if isinstance(allowed_senders, str):
        allowed_senders = [allowed_senders]
    allowed = {s.strip().lower() for s in allowed_senders if s}
    if not allowed:
        return None

    from_header = message.get("From", "")
    if not from_header:
        logger.warning("Rejected: missing From header")
        return None
    from_addr = _extract_address(from_header)
    if from_addr not in allowed:
        logger.warning("Rejected: From address %r not in allowed senders", from_addr)
        return None

    return_path = message.get("Return-Path", "")
    if not return_path:
        logger.warning("Rejected: missing Return-Path header")
        return None
    rp_addr = _extract_return_path(return_path)
    if rp_addr != from_addr:
        logger.warning(
            "Rejected: Return-Path %r does not match From %r", rp_addr, from_addr,
        )
        return None

    return from_addr






def is_authorized(
    message: email.message.Message,
    authorized_sender,
    shared_secret: str = "",
    gpg_fingerprint: str = "",
    gpg_home: str | None = None,
    chat_db=None,
) -> bool:
    """Return True only if the message passes envelope checks AND auth check.

    Auth modes (applied in order, first match wins):
    1. Envelope check is mandatory (From + Return-Path == authorized_sender)
    2. Known chat reply: In-Reply-To matches a Message-ID we previously issued
       (chat_db.find_message_by_email_id). Message-IDs we generate are per-
       message secrets, so a match proves possession of a genuine outbound
       email and is enough with the envelope check.
    3. GPG mode: if gpg_fingerprint is set, verify GPG signature.
    4. Secret mode: Subject starts with AUTH:<shared_secret> (after stripping
       "Re:" prefixes), OR the body contains AUTH:<shared_secret> anywhere
       (covers quoted-reply propagation and manual body inclusion).
    """
    if identify_sender(message, authorized_sender) is None:
        return False

    if chat_db is not None:
        in_reply_to = message.get("In-Reply-To", "").strip()
        if in_reply_to:
            if chat_db.find_message_by_email_id(in_reply_to) is not None:
                return True
            # Fallback path — covers replies to non-relay outbounds
            # (CLI-fallback [Running]/[Result], @agent ACKs, JSON
            # envelope responses) whose Message-IDs land in
            # outbound_emails rather than messages.
            find_outbound = getattr(chat_db, "find_outbound_email", None)
            if find_outbound and find_outbound(in_reply_to) is not None:
                return True

    if gpg_fingerprint:
        return verify_gpg_signature(message, gpg_fingerprint, gpg_home)

    # Decode encoded-words and strip Re/Fwd/Fw before the AUTH prefix check —
    # forwarded subject-only mails on the website-advertised path would
    # otherwise be rejected, and an RFC 2047 forwarded Subject would defeat
    # AUTH detection entirely.
    subject = strip_subject_prefixes(decode_subject(message.get("Subject", ""))).strip()
    expected_prefix = f"AUTH:{shared_secret}"
    if shared_secret and _ct_startswith(subject, expected_prefix):
        return True

    if shared_secret and expected_prefix in _extract_body_text(message):
        return True

    logger.warning("Rejected: no AUTH:<secret> in subject or body, no chat-thread match")
    return False
