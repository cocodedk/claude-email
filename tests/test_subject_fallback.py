"""Subject fallback: phone-style subject-only mails must be acceptable commands."""
import email.message
from src.executor import extract_command


def _msg(subject: str, body: str = "") -> email.message.Message:
    m = email.message.EmailMessage()
    m["Subject"] = subject
    if body:
        m.set_content(body)
    else:
        m.set_content("")
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
