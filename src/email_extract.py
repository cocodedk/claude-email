"""Pull a command string out of an inbound email body or Subject.

Split out of ``src/executor.py`` so each file stays under the 200-line
cap. ``executor.py`` now owns only the ``execute_command`` subprocess
glue; everything that turns an ``email.message.Message`` into the text
that becomes the claude prompt lives here.
"""
import email.header
import email.message
import re
from html.parser import HTMLParser

# Strip quoted-reply trailers so multi-turn email threads don't balloon the
# CLI prompt or chat_db bodies. Each pattern matches the separator that
# introduces the quote and everything after it.
_QUOTE_PATTERNS = (
    # Gmail / most Unix clients: "On <date>, <sender> wrote:"
    re.compile(r"\n\s*On .+? wrote:\n.*", re.DOTALL),
    # Outlook desktop/web: "________________________________\nFrom: ..."
    re.compile(r"\n\s*_{20,}\s*\n\s*From:.*", re.DOTALL),
    # Various clients: "----- Original Message -----"
    re.compile(r"\n\s*-{3,}\s*Original Message\s*-{3,}.*", re.DOTALL | re.IGNORECASE),
)
# Reply/forward subject prefixes — stripped before subject becomes a command.
SUBJECT_PREFIX_RE = re.compile(r"^\s*(?:Re|Fwd|Fw)\s*:\s*", re.IGNORECASE)


def decode_subject(value: str) -> str:
    """Decode RFC 2047 encoded-word Subjects (`=?utf-8?B?...?=`).

    Messages parsed by ``email.message_from_bytes`` (the IMAP poller's
    path) hand encoded-word headers back undecoded.
    """
    if not value:
        return ""
    try:
        return str(email.header.make_header(email.header.decode_header(value)))
    except Exception:  # pragma: no cover — defensive against malformed headers
        return value


def strip_subject_prefixes(subject: str) -> str:
    """Strip nested ``Re:`` / ``Fwd:`` / ``Fw:`` prefixes."""
    prev = None
    while subject != prev:
        prev = subject
        subject = SUBJECT_PREFIX_RE.sub("", subject)
    return subject


def _clean_subject(subject: str, strip_secret: str = "") -> str:
    subject = strip_subject_prefixes(decode_subject(subject))
    if strip_secret:
        subject = subject.replace(f"AUTH:{strip_secret}", "")
    return subject.strip()


def _is_gpg_signed(message: email.message.Message) -> bool:
    """A multipart/signed RFC 3156 envelope means the body+signature pair
    are GPG-protected — but the Subject header lives outside that envelope.
    """
    if message.get_content_type() != "multipart/signed":
        return False
    proto = (message.get_param("protocol") or "").lower()
    return "pgp-signature" in proto


class _HTMLTextExtractor(HTMLParser):
    def __init__(self):
        super().__init__()
        self._parts: list[str] = []

    def handle_data(self, data: str) -> None:
        self._parts.append(data)

    def get_text(self) -> str:
        return "".join(self._parts)


def _extract_text_from_html(html: str) -> str:
    extractor = _HTMLTextExtractor()
    extractor.feed(html)
    return extractor.get_text()


def extract_command(
    message: email.message.Message,
    strip_secret: str = "",
    allow_subject_fallback: bool = True,
) -> str:
    """Extract the command text from an email message body.

    Prefers plain-text, falls back to HTML, strips quoted-reply trailers,
    and redacts every ``AUTH:<secret>`` occurrence.

    When the body is empty, falls back to the Subject — except for
    OpenPGP-signed messages (the signature covers only the body) or
    when ``allow_subject_fallback=False`` (caller already has the
    subject in hand).
    """
    body = ""

    if message.is_multipart():
        for part in message.walk():
            ct = part.get_content_type()
            if ct == "text/plain":
                payload = part.get_payload(decode=True)
                if payload:
                    charset = part.get_content_charset() or "utf-8"
                    body = payload.decode(charset, errors="replace")
                    break
        if not body:
            for part in message.walk():
                ct = part.get_content_type()
                if ct == "text/html":
                    payload = part.get_payload(decode=True)
                    if payload:
                        charset = part.get_content_charset() or "utf-8"
                        html = payload.decode(charset, errors="replace")
                        body = _extract_text_from_html(html)
                        break
    else:
        payload = message.get_payload(decode=True)
        if payload:
            charset = message.get_content_charset() or "utf-8"
            raw = payload.decode(charset, errors="replace")
            ct = message.get_content_type()
            if ct == "text/html":
                body = _extract_text_from_html(raw)
            else:
                body = raw

    # Strip quoted-reply trailers so the prompt / chat_db body stays small.
    for pattern in _QUOTE_PATTERNS:
        body = pattern.sub("", body)
    if strip_secret:
        body = body.replace(f"AUTH:{strip_secret}", "")
    body = body.strip()
    if body:
        return body
    if not allow_subject_fallback or _is_gpg_signed(message):
        return ""
    return _clean_subject(message.get("Subject", "") or "", strip_secret)
