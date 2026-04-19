"""Tests for src/email_format.py."""
from src.email_format import prepend_tag, with_footer, tag_for_message_type, FOOTER


class TestPrependTag:
    def test_adds_tag(self):
        assert prepend_tag("hello", "Queued") == "[Queued] hello"

    def test_none_is_noop(self):
        assert prepend_tag("hello", None) == "hello"

    def test_existing_tag_not_duplicated(self):
        assert prepend_tag("[Queued] hello", "Queued") == "[Queued] hello"

    def test_empty_subject_gets_tag_only(self):
        assert prepend_tag("", "Question") == "[Question]"


class TestFooter:
    def test_appends_footer(self):
        body = with_footer("hi")
        assert body.startswith("hi")
        assert body.endswith(FOOTER.rstrip()) or body.endswith(FOOTER)

    def test_disabled_is_noop(self):
        assert with_footer("hi", enabled=False) == "hi"


class TestTagForType:
    def test_ask_is_question(self):
        assert tag_for_message_type("ask") == "Question"

    def test_notify_is_update(self):
        assert tag_for_message_type("notify") == "Update"

    def test_reply_is_update(self):
        assert tag_for_message_type("reply") == "Update"

    def test_command_is_dispatch(self):
        assert tag_for_message_type("command") == "Dispatch"

    def test_unknown_is_none(self):
        assert tag_for_message_type("mystery") is None
