"""Unit tests for the content-bound replay key.

These pin the *shape* of the key — which credential it digests, and what it
refuses to have an opinion about. The proof that it actually stops a replay is
the docker-gated `tests/e2e/test_replay.py`, which replays a real captured
message through the real stack.
"""
import base64
import email
import email.message

from src.replay_guard import replay_key

SIGNATURE = (
    "-----BEGIN PGP SIGNATURE-----\n"
    "\n"
    "aGVsbG8gd29ybGQgc2lnbmF0dXJlIGJ5dGVz\n"
    "-----END PGP SIGNATURE-----"
)


def _pgp_mime(signature: str = SIGNATURE, *, message_id: str = "<a@x>",
              body: str = "run the thing", sig_type: str = "application/pgp-signature",
              extra_signature: str = "") -> email.message.Message:
    prefix = (f"--B\r\nContent-Type: application/pgp-signature\r\n\r\n"
              f"{extra_signature}\r\n") if extra_signature else ""
    raw = (
        f"From: s@x\r\nTo: r@x\r\nSubject: cmd\r\nMessage-ID: {message_id}\r\n"
        "MIME-Version: 1.0\r\n"
        'Content-Type: multipart/signed; protocol="application/pgp-signature";'
        ' micalg=pgp-sha256; boundary="B"\r\n'
        "\r\n"
        "--B\r\nContent-Type: text/plain; charset=utf-8\r\n\r\n"
        f"{body}\r\n" + prefix +
        f"--B\r\nContent-Type: {sig_type}\r\n\r\n"
        f"{signature}\r\n"
        "--B--\r\n"
    )
    return email.message_from_string(raw)


def _inline(body: str, content_type: str = "text/plain") -> email.message.Message:
    raw = (
        f"From: s@x\r\nTo: r@x\r\nSubject: cmd\r\nMessage-ID: <b@x>\r\n"
        f"MIME-Version: 1.0\r\nContent-Type: {content_type}; charset=utf-8\r\n"
        f"\r\n{body}"
    )
    return email.message_from_string(raw)


class TestPgpMime:
    def test_key_is_stable_for_the_same_signature(self):
        assert replay_key(_pgp_mime()) == replay_key(_pgp_mime())

    def test_key_ignores_the_message_id(self):
        """The whole point: rewriting the unsigned envelope changes nothing."""
        assert replay_key(_pgp_mime(message_id="<one@x>")) == replay_key(
            _pgp_mime(message_id="<two@x>"))

    def test_key_changes_with_the_signature(self):
        other = SIGNATURE.replace("aGVsbG8", "b3RoZXIg")
        assert replay_key(_pgp_mime()) != replay_key(_pgp_mime(other))

    def test_key_is_prefixed_so_it_cannot_look_like_a_message_id(self):
        key = replay_key(_pgp_mime())
        assert key.startswith("sig:") and len(key) == len("sig:") + 64

    def test_a_prepended_junk_signature_part_cannot_mint_a_fresh_key(self):
        """The key must digest the part gpg verifies — the LAST one.

        `src/gpg_verify.py` assigns `sig_bytes` in a loop without breaking, so
        a captured mail with a junk signature part spliced in front still
        verifies against the genuine trailing one. Digesting the first part
        would hand that attacker a brand-new key and a free replay.
        """
        junk = SIGNATURE.replace("aGVsbG8", "anVua19w")
        assert replay_key(_pgp_mime(extra_signature=junk)) == replay_key(_pgp_mime())

    def test_rearmouring_the_same_signature_does_not_change_the_key(self):
        """Line endings, line width and armour headers are packaging, not credential.

        gpg accepts every one of these unchanged, so a key that moved with them
        would be defeated by re-wrapping a captured signature.
        """
        rewrapped = (
            "-----BEGIN PGP SIGNATURE-----\n"
            "Version: GnuPG v2\n"
            "Comment: rewrapped by an interceptor\n"
            "\n"
            "aGVsbG8gd29ybGQg\n"
            "c2lnbmF0dXJlIGJ5dGVz\n"
            "=abcd\n"
            "-----END PGP SIGNATURE-----"
        )
        assert replay_key(_pgp_mime(rewrapped)) == replay_key(_pgp_mime())

    def test_leading_whitespace_does_not_change_the_key(self):
        """gpg accepts it; a key that did not would be bypassed by one newline."""
        assert replay_key(_pgp_mime("\n " + SIGNATURE)) == replay_key(_pgp_mime())

    def test_a_binary_detached_signature_gives_the_same_key_as_its_armour(self):
        """Armour is packaging. Converting a captured signature to binary — which
        gpg verifies identically — must not mint a fresh key."""
        binary = base64.b64decode("aGVsbG8gd29ybGQgc2lnbmF0dXJlIGJ5dGVz")
        message = _pgp_mime()
        part = message.get_payload()[-1]
        part.set_payload(base64.b64encode(binary).decode())
        part["Content-Transfer-Encoding"] = "base64"
        assert replay_key(message) == replay_key(_pgp_mime())

    def test_interior_whitespace_in_the_base64_does_not_change_the_key(self):
        """gpg's radix-64 reader ignores whitespace anywhere in the data.

        A stricter decoder would let one space inside a line launder a replay:
        gpg verifies the mutated armour identically, but the key moves.
        """
        spaced = SIGNATURE.replace("aGVsbG8g", "aGVs bG8g")
        assert replay_key(_pgp_mime(spaced)) == replay_key(_pgp_mime())

    def test_bytes_appended_after_the_end_marker_do_not_change_the_key(self):
        """gpg stops at END; folding the trailing bytes into the base64 moved it."""
        assert replay_key(_pgp_mime(SIGNATURE + "\nXXXX")) == replay_key(_pgp_mime())

    def test_a_binary_packet_bounded_by_whitespace_bytes_keys_the_same(self):
        """The binary/armoured equivalence must not depend on the packet's bytes.

        A signature packet whose first or last byte is an ASCII whitespace
        value (0x20, 0x0a, 0x09 …) is perfectly ordinary — roughly one message
        in twenty. Stripping the binary form made those key differently from
        their own armour, which showed up as an *intermittently* successful
        replay: about 5% of captured messages could be laundered by dearmouring
        them, and the rest could not.
        """
        packet = b"\n hello world signature bytes \n"
        armoured = ("-----BEGIN PGP SIGNATURE-----\n\n"
                    + base64.b64encode(packet).decode() + "\n"
                    "-----END PGP SIGNATURE-----")
        binary = _pgp_mime()
        part = binary.get_payload()[-1]
        part.set_payload(base64.b64encode(packet).decode())
        part["Content-Transfer-Encoding"] = "base64"
        assert replay_key(binary) == replay_key(_pgp_mime(armoured))

    def test_unparseable_armour_still_yields_a_key(self):
        """Anything in a part *declared* a signature is credential material.

        Returning "" here would read as "no opinion" in `fetch_unseen` and let
        the message through — so a signature gpg accepts but this cannot parse
        must still get a key, not a pass.
        """
        for payload in ("-----BEGIN PGP SIGNATURE-----\nZm9v\n-----END PGP SIGNATURE-----",
                        "-----BEGIN PGP SIGNATURE-----\n\nnot!valid!base64!\n"
                        "-----END PGP SIGNATURE-----",
                        "just some text"):
            assert replay_key(_pgp_mime(payload)).startswith("sig:"), payload

    def test_multipart_signed_without_a_signature_part_has_no_key(self):
        assert replay_key(_pgp_mime(sig_type="text/plain")) == ""

    def test_signature_part_with_empty_payload_has_no_key(self):
        message = _pgp_mime()
        message.get_payload()[1].set_payload("")
        assert replay_key(message) == ""


class TestInline:
    def test_clearsigned_body_yields_a_key(self):
        body = f"-----BEGIN PGP SIGNED MESSAGE-----\n\ndo it\n{SIGNATURE}\n"
        assert replay_key(_inline(body)).startswith("sig:")

    def test_the_last_armour_block_wins(self):
        """A quoted block from an earlier mail must not shadow the real signature.

        In a clearsigned message the signature trails the text. Taking the
        first block would give two genuinely different commands that quote the
        same earlier armour one shared key — and silently refuse the second.
        """
        quoted = SIGNATURE.replace("aGVsbG8", "cXVvdGVk")
        first = f"> {quoted}\n\n-----BEGIN PGP SIGNED MESSAGE-----\n\ndo A\n{SIGNATURE}\n"
        second = f"> {quoted}\n\n-----BEGIN PGP SIGNED MESSAGE-----\n\ndo B\n{SIGNATURE.replace('aGVsbG8', 'c2Vjb25k')}\n"
        assert replay_key(_inline(first)) != replay_key(_inline(second))

    def test_a_trailing_lone_begin_line_does_not_empty_the_key(self):
        """The last *complete* block wins, not the last ``BEGIN``.

        Appending a bare (or quoted) BEGIN line leaves gpg verifying the mail
        unchanged. Anchoring on the last BEGIN found no END after it and
        returned no key — which `fetch_unseen` reads as "no opinion" and lets
        the replay through. Anchoring on the last END closes that whole class.
        """
        body = f"-----BEGIN PGP SIGNED MESSAGE-----\n\ndo it\n{SIGNATURE}\n"
        for junk in ("\n-----BEGIN PGP SIGNATURE-----\n",
                     "\n> -----BEGIN PGP SIGNATURE-----\n"):
            assert replay_key(_inline(body + junk)) == replay_key(_inline(body)), junk

    def test_line_endings_do_not_change_the_key(self):
        """A relay that rewrites CRLF to LF must not launder a replay."""
        lf = f"-----BEGIN PGP SIGNED MESSAGE-----\n\ndo it\n{SIGNATURE}\n"
        assert replay_key(_inline(lf)) == replay_key(_inline(lf.replace("\n", "\r\n")))

    def test_multipart_alternative_is_walked_for_the_block(self):
        raw = (
            "From: s@x\r\nTo: r@x\r\nMessage-ID: <c@x>\r\nMIME-Version: 1.0\r\n"
            'Content-Type: multipart/alternative; boundary="B"\r\n\r\n'
            "--B\r\nContent-Type: text/html\r\n\r\n<p>hi</p>\r\n"
            "--B\r\nContent-Type: text/plain\r\n\r\n"
            f"-----BEGIN PGP SIGNED MESSAGE-----\r\n\r\ndo it\r\n{SIGNATURE}\r\n"
            "--B--\r\n"
        )
        assert replay_key(email.message_from_string(raw)).startswith("sig:")

    def test_unsigned_plain_text_has_no_key(self):
        assert replay_key(_inline("just a note")) == ""

    def test_empty_text_part_has_no_key(self):
        assert replay_key(_inline("")) == ""

    def test_the_content_type_header_cannot_hide_an_inline_signature(self):
        """`verify_gpg_signature`'s single-part branch ignores Content-Type.

        So relabelling a captured clearsigned mail leaves gpg verifying it and
        `email_extract` returning the same body — an empty key would be a clean
        replay bypass. The unsigned header must not move the key.
        """
        body = f"-----BEGIN PGP SIGNED MESSAGE-----\n\ndo it\n{SIGNATURE}\n"
        assert replay_key(_inline(body, "application/octet-stream")) == replay_key(
            _inline(body))

    def test_an_unsigned_single_part_still_has_no_key(self):
        """No raw fallback on the inline path: gpg requires the armour too."""
        assert replay_key(_inline("just a note", "application/json")) == ""

    def test_end_marker_before_begin_is_not_a_signature(self):
        """A truncated or reordered armour block must not produce a key."""
        body = "-----END PGP SIGNATURE-----\nnoise\n-----BEGIN PGP SIGNATURE-----\n"
        assert replay_key(_inline(body)) == ""
