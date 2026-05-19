"""Tests for command extraction from email messages."""
import email.message
import pytest
from src.email_extract import extract_command
from tests._executor_helpers import _text_msg, _multipart_msg


class TestExtractCommand:
    def test_simple_text_body(self):
        msg = _text_msg("list files in /tmp")
        assert extract_command(msg) == "list files in /tmp"

    def test_strips_quoted_reply(self):
        msg = _text_msg("do the thing\n\nOn Mon, Apr 14 2025 wrote:\n> old content")
        cmd = extract_command(msg)
        assert "do the thing" in cmd
        assert "> old content" not in cmd

    def test_multipart_uses_plain_text(self):
        msg = _multipart_msg("plain command", "<b>html command</b>")
        assert extract_command(msg) == "plain command"

    def test_strips_leading_trailing_whitespace(self):
        msg = _text_msg("  run tests  \n")
        assert extract_command(msg) == "run tests"

    def test_html_only_falls_back_gracefully(self):
        msg = email.message.EmailMessage()
        msg["Subject"] = "test"
        msg.add_alternative("<html><body><p>run tests</p></body></html>", subtype="html")
        result = extract_command(msg)
        assert isinstance(result, str)
        assert len(result) >= 0  # does not crash

    def test_strips_outlook_quote_block(self):
        """Outlook replies include a _____ separator + From:/Sent:/... header
        block + the full quoted message. All of that must be stripped so
        thread length doesn't balloon the CLI prompt or chat_db bodies.
        """
        msg = _text_msg(
            "Fix the bug please\n"
            "\n"
            "\n"
            "________________________________\n"
            "From: agent@example.com <agent@example.com>\n"
            "Sent: Saturday, April 18, 2026 5:52:14 PM\n"
            "To: Babak Bandpey <user@example.com>\n"
            "Subject: Re: [master-fixer] message\n"
            "\n"
            "This is the prior long email chain that shouldn't be in the "
            "command prompt — " + "x " * 200
        )
        result = extract_command(msg)
        assert result == "Fix the bug please"
        assert "From:" not in result
        assert "x x x" not in result

    def test_strips_original_message_separator(self):
        """Some clients use '----- Original Message -----' instead of Outlook's underscores."""
        msg = _text_msg(
            "My new reply\n"
            "\n"
            "----- Original Message -----\n"
            "From: someone@example.com\n"
            "the old message body"
        )
        result = extract_command(msg)
        assert result == "My new reply"

    def test_keeps_non_quote_underscores(self):
        """A normal paragraph with underscores must not be mistaken for an Outlook quote.

        Short rules of thumb matter: the underscore line must be long (>=20)
        AND immediately followed by 'From:' for it to count as a quote.
        """
        msg = _text_msg("my command __ with __ underscores __ in text")
        assert extract_command(msg) == "my command __ with __ underscores __ in text"


class TestExtractCommandHtmlOnly:
    def test_single_part_html(self):
        msg = email.message.EmailMessage()
        msg["Subject"] = "test"
        msg.set_content("<p>run tests</p>", subtype="html")
        result = extract_command(msg)
        assert "run tests" in result
