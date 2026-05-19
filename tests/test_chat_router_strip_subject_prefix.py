"""Tests for email classification / routing logic (chat_router)."""
import pytest
from src.chat_db import ChatDB
from src.chat_router import Route, classify_email, _strip_subject_prefix
from tests._chat_router_helpers import AUTH_PREFIX, _make_msg  # noqa: F401


@pytest.fixture
def db(tmp_path):
    return ChatDB(str(tmp_path / "test.db"))


class TestStripSubjectPrefix:
    def test_strips_single_re_prefix(self):
        result = _strip_subject_prefix("Re: AUTH:mysecret status", AUTH_PREFIX)
        assert result == "status"

    def test_strips_multiple_re_prefixes(self):
        result = _strip_subject_prefix("Re: Re: AUTH:mysecret status", AUTH_PREFIX)
        assert result == "status"

    def test_strips_case_insensitive_re(self):
        result = _strip_subject_prefix("re: RE: AUTH:mysecret status", AUTH_PREFIX)
        assert result == "status"

    def test_strips_fwd_and_fw_prefixes(self):
        assert _strip_subject_prefix("Fwd: AUTH:mysecret @x go", AUTH_PREFIX) == "@x go"
        assert _strip_subject_prefix("FW: AUTH:mysecret status", AUTH_PREFIX) == "status"
        assert _strip_subject_prefix("Re: Fwd: AUTH:mysecret status", AUTH_PREFIX) == "status"

    def test_decodes_rfc2047_encoded_word(self):
        # base64-encoded "AUTH:mysecret status" — Subject parsed from raw
        # bytes by IMAP poller would otherwise show up encoded.
        encoded = "=?utf-8?B?QVVUSDpteXNlY3JldCBzdGF0dXM=?="
        assert _strip_subject_prefix(encoded, AUTH_PREFIX) == "status"

    def test_strips_auth_prefix_without_re(self):
        result = _strip_subject_prefix("AUTH:mysecret do something", AUTH_PREFIX)
        assert result == "do something"

    def test_no_auth_prefix_present(self):
        result = _strip_subject_prefix("Re: status", AUTH_PREFIX)
        assert result == "status"

    def test_empty_subject(self):
        result = _strip_subject_prefix("", AUTH_PREFIX)
        assert result == ""

    def test_only_re_prefixes(self):
        result = _strip_subject_prefix("Re: Re: ", AUTH_PREFIX)
        assert result == ""

    def test_auth_prefix_with_extra_whitespace(self):
        result = _strip_subject_prefix("AUTH:mysecret   @agent-fits do it", AUTH_PREFIX)
        assert result == "@agent-fits do it"
