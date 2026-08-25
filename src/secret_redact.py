"""Strip shared secrets out of anything about to leave the system over SMTP.

The shared secret is a *bearer* credential: ``src/security.py`` accepts a
message whose Subject starts with ``AUTH:<secret>`` or whose body contains that
token anywhere. Both of those surfaces are echoed back to the user —
``send_threaded_reply`` builds the reply Subject from the inbound Subject, and
the command text becomes the CLI prompt whose output becomes the reply body —
so without a scrub the credential ships out again in every ``[Running]`` and
``[Result]`` mail, where it sits in the sender's mailbox and in every mail
server along the way.

This module is the scrub, and ``src/mailer.send_reply`` is the single place it
is applied: every outbound mail in the product goes through that function, so a
choke point there covers all three of its callers (``chat_handlers``,
``chat_relay``, ``json_handler``) and any future one, on every header and every
body alike.

Three things it deliberately does that a naive ``body.replace`` would not:

* it redacts the **bare secret**, not only the ``AUTH:<secret>`` token, because
  the invariant is that the secret never leaves — a copy pasted into the middle
  of a command has to go too, and only the ``AUTH:`` form is stripped inbound;
* it looks through **RFC 2047 encoded-words**, because a Subject arriving as
  ``=?utf-8?B?...?=`` authenticates (``is_authorized`` decodes before checking)
  and would otherwise be echoed back with the secret intact but base64-wrapped;
* it scrubs the **JSON-escaped rendering** of a secret as well as the literal
  one, because ``src/envelope_builder.py`` serialises envelope replies with
  ``json.dumps`` at its ``ensure_ascii`` default — a secret holding a
  non-ASCII character, a quote or a backslash arrives here already escaped;
* it scrubs **every header**, not just the Subject — ``In-Reply-To`` and
  ``References`` are copied verbatim from the inbound ``Message-ID``, so a
  Message-ID containing the secret would carry it straight back out. Threading
  degrades only for a thread whose own Message-ID held the credential.
"""
import email.header
import email.message
import json
from collections.abc import Iterable, Sequence

#: What a redacted occurrence is replaced with. Plain text, so it stays valid
#: inside a JSON envelope's string values as well as inside a Subject.
REDACTED = "[secret redacted]"


def configured_secrets(config: dict) -> tuple[str, ...]:
    """Every shared secret this reply could need scrubbing for.

    ``relay_outbound_messages`` runs from ``main``'s housekeeping with the
    *top-level* config rather than a per-universe one, so scrubbing only
    ``config["shared_secret"]`` would miss a second universe's credential.
    Taking the union costs nothing and cannot under-cover a call site.
    """
    found = [config.get("shared_secret", "")]
    found += [getattr(u, "shared_secret", "")
              for u in (config.get("universes") or [])]
    return tuple(dict.fromkeys(s for s in found if s))


def _decode_words(value: str) -> str:
    """RFC 2047 encoded-words resolved to the text they stand for."""
    try:
        return str(email.header.make_header(email.header.decode_header(value)))
    except Exception:  # pragma: no cover — defensive against malformed headers
        return value


def _forms(secrets: Iterable[str]) -> tuple[str, ...]:
    """Every rendering a secret can arrive in, empties dropped.

    ``json.dumps`` at its ``ensure_ascii`` default is what builds the JSON
    envelope replies, so a secret containing a non-ASCII character, a quote or
    a backslash reaches this module escaped and a literal replace would walk
    straight past it.
    """
    forms = []
    for secret in secrets:
        if not secret:
            continue
        forms.append(secret)
        escaped = json.dumps(secret)[1:-1]
        if escaped != secret:
            forms.append(escaped)
    return tuple(dict.fromkeys(forms))


def scrub_text(text: str, secrets: Iterable[str]) -> str:
    """Replace every occurrence of every secret. Empty secrets are ignored."""
    for form in _forms(secrets):
        text = text.replace(form, REDACTED)
    return text


def _contains(text: str, secrets: Sequence[str]) -> bool:
    return any(form in text for form in _forms(secrets))


def scrub_header_value(value: str, secrets: Sequence[str]) -> str:
    """Scrub a header, decoding encoded-words only if that is where it hides."""
    cleaned = scrub_text(value, secrets)
    decoded = _decode_words(cleaned)
    if _contains(decoded, secrets):
        return scrub_text(decoded, secrets)
    return cleaned


def scrub_message(msg: email.message.Message, secrets: Sequence[str]) -> None:
    """Scrub every header value of ``msg`` in place."""
    live = [secret for secret in secrets if secret]
    if not live:
        return
    for name in dict.fromkeys(msg.keys()):
        values = [str(value) for value in (msg.get_all(name) or [])]
        cleaned = [scrub_header_value(value, live) for value in values]
        if cleaned == values:
            continue
        del msg[name]
        for value in cleaned:
            msg[name] = value
