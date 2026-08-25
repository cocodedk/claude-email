"""Content-bound replay keys for inbound email.

Why the Message-ID store is not replay protection
-------------------------------------------------
``src/poller.py`` remembers the ``Message-ID`` of every message it has already
processed, which makes redelivery idempotent. It does not make a captured
message unusable, because **no credential this system accepts covers the
Message-ID header**: the GPG signature is computed over the ``multipart/signed``
MIME part alone, and the shared secret lives in the body. An interceptor who
holds one authorised command mail can rewrite its ``Message-ID`` (and its
``Date``, equally unsigned), hand the untouched signed payload back to the
mailbox, and the signature still verifies. Before this module, that executed the
command again.

The key
-------
:func:`replay_key` fingerprints the *credential* a message presents, so
re-presenting a captured credential is refused however the envelope around it is
rewritten. Today exactly one credential is cryptographically bound to content —
an OpenPGP signature — so that is what is digested, in both the PGP/MIME
detached form and the inline clearsigned form.

Three rules make that key stable, each written because a review found the
mutation that broke its absence: digest the signature and not its packaging;
digest the bytes ``src/gpg_verify.py`` would hand to gpg, which means being
neither stricter nor looser than it about *which* part and *which* block; and
never answer "no credential" when there is one, because an empty key reads as
"no opinion" in ``fetch_unseen`` and lets the message through. The individual
functions below say which rule they carry and what breaks without it.

``docs/e2e-replay.md`` has the full argument: the transformations this key is
verified stable under, why the unsigned bearer routes deliberately get no key
at all, and the residuals — including that this is a short reimplementation of
a forgiving format, and that keying on gpg's own ``signature_id`` is the
durable fix.

One bound worth stating here: an OpenPGP signature packet hashes its own
creation time at one-second resolution, so re-signing identical text within the
same second reproduces identical bytes and the second send is refused. A second
apart, it is not.
"""
from __future__ import annotations

import base64
import binascii
import email.message
import hashlib

_BEGIN = b"-----BEGIN PGP SIGNATURE-----"
_END = b"-----END PGP SIGNATURE-----"


def replay_key(message: email.message.Message) -> str:
    """Return a content-bound replay key, or ``""`` if the message has none.

    The empty string means "this message presents no content-bound credential",
    and callers must treat it as "no opinion" rather than as a key — otherwise
    every unsigned message would collide with every other.
    """
    signature = _signature_bytes(message)
    if not signature:
        return ""
    return "sig:" + hashlib.sha256(signature).hexdigest()


def _signature_bytes(message: email.message.Message) -> bytes:
    """The OpenPGP signature this message carries, PGP/MIME or inline."""
    if message.get_content_type() == "multipart/signed":
        return _detached_signature(message)
    return _inline_signature(message)


def _detached_signature(message: email.message.Message) -> bytes:
    """The PGP/MIME signature part — the *last* one, as ``gpg_verify`` uses.

    ``verify_gpg_signature`` assigns ``sig_bytes`` in a loop without breaking,
    so a message carrying two signature parts is verified against the second.
    Taking the first here would let an attacker prepend a junk part and mint a
    fresh key for a captured, still-valid signature.
    """
    found = b""
    for part in message.get_payload():
        if (isinstance(part, email.message.Message)
                and part.get_content_type() == "application/pgp-signature"):
            found = part.get_payload(decode=True) or b""
    if not found:
        return b""
    # The part is *declared* a signature, so anything in it is credential
    # material and gets a key. A binary detached signature is already the
    # packet, and is returned byte for byte: stripping it would mangle any
    # packet whose first or last byte happens to be an ASCII whitespace value,
    # so the same signature sent armoured and unarmoured would key differently
    # about 5% of the time. ``_last_block`` already tolerates whitespace around
    # an armour block, so nothing needs stripping here.
    block = _last_block(found)
    return _canonical(block) if block else found


def _inline_signature(message: email.message.Message) -> bytes:
    """The armoured block of a clearsigned message, canonicalised.

    Mirrors ``verify_gpg_signature``'s inline branch, which for a multipart
    message breaks on the first ``text/plain`` part with a payload and for a
    single-part message reads the body *whatever its Content-Type says*.
    Filtering on ``text/plain`` in the single-part case would be stricter than
    gpg, and stricter is not safer here: relabelling a captured clearsigned
    mail ``application/octet-stream`` leaves gpg verifying it and the command
    body unchanged, so an empty key would let the replay straight through.

    An empty result means "no complete armour block", which gpg's inline branch
    refuses too — so it is a truthful answer here rather than a fallthrough.
    """
    for payload in _inline_candidates(message):
        block = _last_block(payload)
        return _canonical(block) if block else b""
    return b""


def _inline_candidates(message: email.message.Message):
    """The payloads gpg's inline branch would read, in the order it reads them."""
    if not message.is_multipart():
        payload = message.get_payload(decode=True)
        if payload:
            yield payload
        return
    for part in message.walk():
        if part.is_multipart() or part.get_content_type() != "text/plain":
            continue
        if payload := part.get_payload(decode=True):
            yield payload


def _last_block(payload: bytes) -> bytes:
    """The last *complete* ``BEGIN``…``END`` armour block, or ``b""``.

    Anchored on the last ``END`` rather than the last ``BEGIN``, which matters
    twice over. A stray trailing ``BEGIN`` line — quoted into a reply, say —
    would otherwise win and leave no ``END`` after it, yielding no key at all;
    and anything appended *after* the real ``END`` is dropped here rather than
    folded into the base64. gpg ignores both, so the key must too.
    """
    end = payload.rfind(_END)
    if end == -1:
        return b""
    start = payload.rfind(_BEGIN, 0, end)
    return payload[start:end + len(_END)] if start != -1 else b""


def _canonical(block: bytes) -> bytes:
    """Canonical bytes for an armour block. Never empty for a non-empty block.

    Dearmoured where this parser can, and otherwise the block with all
    whitespace removed. The fallback is the point: gpg's radix-64 reader is
    more forgiving than any short reimplementation, and returning ``b""`` for
    armour gpg would accept reads as "no credential" upstream and hands the
    replay a clean pass. A key that is merely *different* from the ideal costs
    one replay per divergence; an absent key costs every replay.
    """
    return _dearmor(block) or b"".join(block.split())


def _dearmor(block: bytes) -> bytes:
    """The binary signature packet inside an armour block, or ``b""``.

    ``b""`` means "not armour this parser can decode"; :func:`_canonical`
    decides what to do about that.
    """
    # RFC 4880 armour: the BEGIN line, optional headers, a blank line, the
    # base64 data, an optional "=" CRC line, then END.
    _, blank, rest = block.replace(b"\r\n", b"\n").partition(b"\n\n")
    if not blank:
        return b""
    data = b"".join(
        line for line in rest.split(b"\n")
        if not line.strip().startswith((b"=", b"-----"))
    )
    # gpg's radix-64 reader ignores whitespace anywhere in the data; a decoder
    # that did not would let one space inside a line launder a replay.
    try:
        return base64.b64decode(b"".join(data.split()), validate=True)
    except (binascii.Error, ValueError):
        return b""
