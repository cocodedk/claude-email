"""Header-hygiene tests for the SMTP mailer.

Covers the Phase 6 mailer nits (Appendix A, L4): case-insensitive ``Re:``
detection, an RFC 5322 References chain rather than a bare parent id, and
header-injection sanitisation of the ``To`` header.
"""
from unittest.mock import MagicMock

from src.mailer import send_reply


def _send(mocker, **overrides):
    """Run send_reply against a mocked SMTP_SSL and return the sent message."""
    mocker.patch("ssl.create_default_context", return_value=MagicMock())
    mock_smtp_class = mocker.patch("smtplib.SMTP_SSL")
    mock_smtp = MagicMock()
    mock_smtp_class.return_value.__enter__ = MagicMock(return_value=mock_smtp)
    mock_smtp_class.return_value.__exit__ = MagicMock(return_value=False)

    kwargs = {
        "smtp_host": "send.example.com",
        "smtp_port": 465,
        "username": "agent@example.com",
        "password": "pw",
        "to": "user@example.com",
        "subject": "status",
        "body": "done",
    }
    kwargs.update(overrides)
    send_reply(**kwargs)
    return mock_smtp.send_message.call_args[0][0]


def _header_names(msg):
    """Header names actually emitted on the wire (column-0 lines only).

    Reading msg["Bcc"] is not enough: the question is whether a crafted value
    broke out of its own header and started a new one in the serialized form.
    """
    head = msg.as_string().split("\n\n", 1)[0]
    return [
        line.split(":", 1)[0].lower()
        for line in head.split("\n")
        if line[:1] not in (" ", "\t") and ":" in line
    ]


class TestSubjectPrefix:
    def test_uppercase_re_is_not_double_prefixed(self, mocker):
        msg = _send(mocker, subject="RE: build status")
        assert msg["Subject"] == "RE: build status"

    def test_lowercase_re_is_not_double_prefixed(self, mocker):
        msg = _send(mocker, subject="re: build status")
        assert msg["Subject"] == "re: build status"

    def test_mixed_case_re_with_leading_space_is_not_double_prefixed(self, mocker):
        msg = _send(mocker, subject="  rE: build status")
        assert msg["Subject"] == "rE: build status"

    def test_bare_subject_gets_prefixed(self, mocker):
        msg = _send(mocker, subject="build status")
        assert msg["Subject"] == "Re: build status"

    def test_word_starting_with_re_is_still_prefixed(self, mocker):
        msg = _send(mocker, subject="Report ready")
        assert msg["Subject"] == "Re: Report ready"


class TestReferencesChain:
    def test_parent_chain_is_extended_with_in_reply_to(self, mocker):
        msg = _send(
            mocker,
            in_reply_to="<c@mail>",
            references="<a@mail> <b@mail>",
        )
        assert msg["References"] == "<a@mail> <b@mail> <c@mail>"

    def test_in_reply_to_already_in_chain_is_not_duplicated(self, mocker):
        msg = _send(
            mocker,
            in_reply_to="<b@mail>",
            references="<a@mail> <b@mail>",
        )
        assert msg["References"] == "<a@mail> <b@mail>"

    def test_legacy_callers_passing_only_the_parent_are_unchanged(self, mocker):
        msg = _send(mocker, in_reply_to="<p@mail>", references="<p@mail>")
        assert msg["References"] == "<p@mail>"
        assert msg["In-Reply-To"] == "<p@mail>"

    def test_references_derived_from_in_reply_to_when_absent(self, mocker):
        msg = _send(mocker, in_reply_to="<p@mail>")
        assert msg["References"] == "<p@mail>"

    def test_no_threading_headers_when_nothing_to_thread(self, mocker):
        msg = _send(mocker)
        assert msg["References"] is None
        assert msg["In-Reply-To"] is None


class TestHeaderInjection:
    def test_newlines_in_to_header_are_collapsed(self, mocker):
        msg = _send(mocker, to="user@example.com\r\nBcc: attacker@evil.example")
        assert "\n" not in str(msg["To"])
        assert "bcc" not in _header_names(msg)

    def test_newlines_in_in_reply_to_are_collapsed(self, mocker):
        msg = _send(mocker, in_reply_to="<a@mail>\r\nBcc: attacker@evil.example")
        assert "\n" not in msg["In-Reply-To"]
        assert "bcc" not in _header_names(msg)

    def test_newlines_in_references_are_collapsed(self, mocker):
        msg = _send(
            mocker,
            in_reply_to="<b@mail>",
            references="<a@mail>\r\nBcc: attacker@evil.example",
        )
        assert "\n" not in msg["References"]
        assert "bcc" not in _header_names(msg)

    def test_newlines_in_subject_are_collapsed(self, mocker):
        msg = _send(mocker, subject="status\r\nBcc: attacker@evil.example")
        assert msg["Subject"] == "Re: status Bcc: attacker@evil.example"
        assert "bcc" not in _header_names(msg)
