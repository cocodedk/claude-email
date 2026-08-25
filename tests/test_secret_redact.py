"""Unit tests for the outbound shared-secret scrub.

The property these back up is asserted end-to-end over real SMTP in
``tests/e2e/test_invariants.py``; these pin the individual branches so the
module keeps its coverage without needing docker.
"""
import email.message
import json

from src.secret_redact import (
    REDACTED,
    configured_secrets,
    scrub_header_value,
    scrub_message,
    scrub_text,
)

SECRET = "s3cr3t-value"


class _Universe:
    def __init__(self, shared_secret):
        self.shared_secret = shared_secret


class TestConfiguredSecrets:
    def test_returns_the_top_level_secret(self):
        assert configured_secrets({"shared_secret": SECRET}) == (SECRET,)

    def test_empty_config_yields_nothing(self):
        assert configured_secrets({}) == ()

    def test_unions_every_universe_secret_and_dedupes(self):
        config = {
            "shared_secret": SECRET,
            "universes": [_Universe(SECRET), _Universe("other"), _Universe("")],
        }
        assert configured_secrets(config) == (SECRET, "other")


class TestScrubText:
    def test_replaces_every_occurrence(self):
        assert scrub_text(f"a {SECRET} b {SECRET}", [SECRET]) == \
            f"a {REDACTED} b {REDACTED}"

    def test_empty_secret_is_ignored(self):
        """``"".replace`` would otherwise splice the marker between letters."""
        assert scrub_text("untouched", [""]) == "untouched"

    def test_no_secrets_at_all_is_a_no_op(self):
        assert scrub_text("untouched", ()) == "untouched"

    def test_json_escaped_rendering_is_scrubbed_too(self):
        """A JSON envelope reply arrives already serialised and escaped.

        ``ensure_ascii`` turns a non-ASCII secret into escape sequences, so
        the literal form never appears in the body a caller hands to
        ``send_reply``.
        """
        secret = 'pa\u00dfwort"x'
        payload = json.dumps({"echo": f"the secret is {secret}"})
        assert secret not in payload
        scrubbed = scrub_text(payload, [secret])
        assert secret not in json.loads(scrubbed)["echo"]
        assert REDACTED in json.loads(scrubbed)["echo"]

    def test_ascii_only_secret_needs_no_second_form(self):
        assert scrub_text(f"x {SECRET} y", [SECRET]) == f"x {REDACTED} y"


class TestScrubHeaderValue:
    def test_plain_occurrence(self):
        assert scrub_header_value(f"AUTH:{SECRET} do it", [SECRET]) == \
            f"AUTH:{REDACTED} do it"

    def test_encoded_word_is_decoded_then_scrubbed(self):
        """An RFC 2047 Subject authenticates, so it can also leak.

        The raw header holds no readable copy of the secret, so a plain
        substring scrub passes it through untouched.
        """
        wrapped = "=?utf-8?B?QVVUSDpzM2NyM3QtdmFsdWUgw6bDuMOl?="
        assert SECRET not in wrapped
        assert scrub_header_value(wrapped, [SECRET]) == f"AUTH:{REDACTED} \u00e6\u00f8\u00e5"

    def test_clean_header_is_returned_unchanged(self):
        assert scrub_header_value("Re: hello", [SECRET]) == "Re: hello"


class TestScrubMessage:
    def _message(self, subject, in_reply_to):
        msg = email.message.EmailMessage()
        msg["Subject"] = subject
        msg["In-Reply-To"] = in_reply_to
        return msg

    def test_scrubs_subject_and_other_headers(self):
        msg = self._message(f"[Result] AUTH:{SECRET} go", f"<{SECRET}@host>")
        scrub_message(msg, [SECRET])
        assert msg["Subject"] == f"[Result] AUTH:{REDACTED} go"
        assert msg["In-Reply-To"] == f"<{REDACTED}@host>"

    def test_no_live_secret_leaves_the_message_alone(self):
        msg = self._message("[Result] go", "<id@host>")
        scrub_message(msg, ["", ""])
        assert msg["Subject"] == "[Result] go"

    def test_clean_headers_are_not_rewritten(self):
        msg = self._message("[Result] go", "<id@host>")
        scrub_message(msg, [SECRET])
        assert msg.keys() == ["Subject", "In-Reply-To"]

    def test_repeated_header_names_are_all_scrubbed(self):
        msg = email.message.EmailMessage()
        msg["Received"] = f"from a ({SECRET})"
        msg["Received"] = f"from b ({SECRET})"
        scrub_message(msg, [SECRET])
        assert msg.get_all("Received") == [
            f"from a ({REDACTED})", f"from b ({REDACTED})"]


def test_send_reply_scrubs_body_and_subject(mocker):
    """The choke point itself: nothing reaches SMTP holding the secret."""
    from unittest.mock import MagicMock

    from src.mailer import send_reply

    mocker.patch("ssl.create_default_context", return_value=MagicMock())
    smtp_class = mocker.patch("smtplib.SMTP_SSL")
    smtp = MagicMock()
    smtp_class.return_value.__enter__ = MagicMock(return_value=smtp)
    smtp_class.return_value.__exit__ = MagicMock(return_value=False)

    send_reply(
        smtp_host="mail.example.com", smtp_port=465,
        username="agent@example.com", password="pw", to="user@example.com",
        subject=f"AUTH:{SECRET} run it", body=f"echoed {SECRET} back",
        in_reply_to=f"<{SECRET}@host>", references=f"<{SECRET}@host>",
        email_domain="example.com", secrets=(SECRET,),
    )

    sent = smtp.send_message.call_args[0][0]
    assert SECRET not in sent.as_string()
    assert REDACTED in sent["Subject"]
    assert REDACTED in sent.get_content()
