"""Tests for ``src/chat_relay.relay_outbound_messages``.

The relay's contract:
  - ``type='ask'`` is always sent (the user has to receive it to reply).
  - Other types (``notify``/``chat``/...) only send when the message is
    part of an email-driven thread: the message's task has
    ``origin_message_id`` set, OR a prior ``user→from_name`` row exists
    (the @agent-command fallback). CLI-only chat_notify is dropped so
    the user isn't surprised by unsolicited mail.
  - Every successful send writes the SMTP Message-ID into BOTH
    ``messages.email_message_id`` (legacy) and ``outbound_emails`` (new
    unified lookup) so security thread-match accepts the user's reply.
"""
import pytest

from src.chat_db import ChatDB
from src.chat_relay import relay_outbound_messages
from src.task_queue import TaskQueue


def _config():
    return {
        "smtp_host": "smtp.example.com", "smtp_port": 465,
        "username": "agent@example.com", "password": "pw",
        "authorized_sender": "user@example.com",
        "email_domain": "example.com",
        "universes": [],
    }


@pytest.fixture
def db_path(tmp_path):
    return str(tmp_path / "relay.db")


@pytest.fixture
def cdb(db_path):
    return ChatDB(db_path)


class TestThreadMatchPlumbing:
    """Every relayed mail must populate both lookup paths so the user's
    reply auths via security.is_authorized's chat-thread match."""

    def test_relayed_ask_records_in_messages_and_outbound_emails(self, mocker, cdb):
        mocker.patch("src.chat_relay.send_reply", return_value="<sent@example.com>")
        cdb.register_agent("agent-cli", "/proj/cli")
        msg = cdb.insert_message("agent-cli", "user", "?", "ask")

        relay_outbound_messages(_config(), cdb)

        # Legacy lookup — messages.email_message_id
        row = cdb._conn.execute(
            "SELECT email_message_id FROM messages WHERE id=?", (msg["id"],),
        ).fetchone()
        assert row["email_message_id"] == "<sent@example.com>"
        # New lookup — outbound_emails
        out = cdb.find_outbound_email("<sent@example.com>")
        assert out is not None
        assert out["sender_agent"] == "agent-cli"
        assert out["kind"] == "ask"

    def test_dropped_messages_do_not_record_outbound(self, mocker, cdb):
        """A dropped CLI-only notify must NOT leak a row into
        outbound_emails — security would otherwise auth a reply on a
        thread the user never even received."""
        mocker.patch("src.chat_relay.send_reply", return_value="<should-not@x>")
        cdb.register_agent("agent-cli", "/proj/cli")
        cdb.insert_message("agent-cli", "user", "fyi", "notify")

        relay_outbound_messages(_config(), cdb)

        assert cdb.find_outbound_email("<should-not@x>") is None

    def test_blank_message_id_does_not_overwrite(self, mocker, cdb):
        """If send_reply returns "" (defensive — make_msgid always returns
        a value, but be safe), the column stays NULL on the row."""
        mocker.patch("src.chat_relay.send_reply", return_value="")
        cdb.register_agent("agent-foo", "/proj/foo")
        cdb.insert_message("user", "agent-foo", "go", "command")
        msg = cdb.insert_message("agent-foo", "user", "ok", "notify")

        relay_outbound_messages(_config(), cdb)

        row = cdb._conn.execute(
            "SELECT email_message_id FROM messages WHERE id=?", (msg["id"],),
        ).fetchone()
        assert row["email_message_id"] is None
