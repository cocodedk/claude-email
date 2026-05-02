"""Subject fallback: phone-style subject-only mails must be acceptable commands."""
import email
import email.message
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from src.email_extract import extract_command


def _msg(subject: str, body: str = "") -> email.message.Message:
    m = email.message.EmailMessage()
    m["Subject"] = subject
    if body:
        m.set_content(body)
    else:
        m.set_content("")
    return m


def _raw_msg(subject: str, body: str = "") -> email.message.Message:
    """Mirror the IMAP poller's `email.message_from_bytes(raw)` path —
    no explicit policy, so RFC 2047 encoded-word Subjects come through
    undecoded the way they do in production."""
    raw = (
        f"Subject: {subject}\r\n"
        "Content-Type: text/plain; charset=utf-8\r\n"
        "\r\n"
        f"{body}\r\n"
    ).encode("utf-8")
    return email.message_from_bytes(raw)


def _signed_msg(subject: str, body: str = "") -> email.message.Message:
    """Build a multipart/signed message simulating an OpenPGP-signed email."""
    m = MIMEMultipart("signed", protocol="application/pgp-signature")
    m["Subject"] = subject
    m.attach(MIMEText(body, "plain", "utf-8"))
    m.attach(MIMEText("---FAKE PGP SIGNATURE---", "pgp-signature", "us-ascii"))
    return m


class TestSubjectFallback:
    def test_subject_used_when_body_empty(self):
        assert extract_command(_msg("list files in /tmp")) == "list files in /tmp"

    def test_subject_used_when_body_only_whitespace(self):
        assert extract_command(_msg("run the build", "   \n  \n")) == "run the build"

    def test_body_wins_when_non_empty(self):
        assert extract_command(_msg("ignored subject", "real command")) == "real command"

    def test_strips_re_prefix(self):
        assert extract_command(_msg("Re: deploy now")) == "deploy now"

    def test_strips_re_prefix_case_insensitive(self):
        assert extract_command(_msg("RE: deploy")) == "deploy"
        assert extract_command(_msg("re: deploy")) == "deploy"

    def test_strips_fwd_prefix(self):
        assert extract_command(_msg("Fwd: check this")) == "check this"
        assert extract_command(_msg("FW: check this")) == "check this"
        assert extract_command(_msg("Fw: check this")) == "check this"

    def test_strips_repeated_prefixes(self):
        assert extract_command(_msg("Re: Re: Fwd: ping")) == "ping"

    def test_both_empty_returns_empty(self):
        assert extract_command(_msg("")) == ""
        assert extract_command(_msg("   ")) == ""

    def test_subject_strips_auth_secret(self):
        msg = _msg("AUTH:s3cret run tests")
        assert extract_command(msg, strip_secret="s3cret") == "run tests"

    def test_subject_after_quoted_body_strip(self):
        """Quoted-reply trailer eats the entire body — subject must rescue."""
        body = "\nOn Fri, May 1 2026, claude wrote:\n> long quoted reply"
        msg = _msg("rerun the migration", body)
        assert extract_command(msg) == "rerun the migration"

    def test_missing_subject_header(self):
        m = email.message.EmailMessage()
        m.set_content("")
        assert extract_command(m) == ""


class TestGpgSignedSubjectRefused:
    """An OpenPGP signature covers only the body, so a header-tampering hop
    could substitute the Subject without invalidating the signature."""

    def test_signed_empty_body_returns_empty(self):
        msg = _signed_msg("rm -rf /etc", body="")
        assert extract_command(msg) == ""

    def test_signed_with_body_uses_body(self):
        """Signed messages with a real body still use the body — only the
        empty-body fallback is suppressed."""
        msg = _signed_msg("ignored", body="run the migration")
        assert extract_command(msg) == "run the migration"


class TestExplicitFallbackDisabled:
    """Callers (e.g. chat_router for @agent commands) need to suppress the
    subject fallback so they can supply a parsed remainder instead of the
    raw subject."""

    def test_disable_fallback_returns_empty_for_empty_body(self):
        msg = _msg("would-be-subject", body="")
        assert extract_command(msg, allow_subject_fallback=False) == ""

    def test_disable_fallback_still_returns_body(self):
        msg = _msg("ignored", body="real command")
        assert extract_command(msg, allow_subject_fallback=False) == "real command"


class TestRfc2047SubjectDecoded:
    """Phone clients send non-ASCII Subjects RFC 2047-encoded; the fallback
    must decode them rather than pipe ``=?utf-8?...?=`` to the CLI."""

    def test_base64_encoded_subject_decoded(self):
        # base64-encoded "hello fødsel" in utf-8 — uses the IMAP-like
        # message_from_bytes path so the encoded word survives to the helper.
        msg = _raw_msg("=?utf-8?B?aGVsbG8gZsO4ZHNlbA==?=")
        assert extract_command(msg) == "hello fødsel"

    def test_quoted_printable_subject_decoded(self):
        msg = _raw_msg("=?utf-8?Q?caf=C3=A9?=")
        assert extract_command(msg) == "café"

    def test_persian_subject_decoded(self):
        # base64-encoded Persian "سلام" in utf-8
        msg = _raw_msg("=?utf-8?B?2LPZhNin2YU=?=")
        assert extract_command(msg) == "سلام"
