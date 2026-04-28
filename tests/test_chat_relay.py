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
        "username": "claude@cocode.dk", "password": "pw",
        "authorized_sender": "bb@cocode.dk",
        "email_domain": "cocode.dk",
        "universes": [],
    }


@pytest.fixture
def db_path(tmp_path):
    return str(tmp_path / "relay.db")


@pytest.fixture
def cdb(db_path):
    return ChatDB(db_path)


class TestAskAlwaysRelays:
    def test_ask_from_cli_only_agent_is_relayed(self, mocker, cdb):
        mock_send = mocker.patch("src.chat_relay.send_reply", return_value="<r@x>")
        cdb.register_agent("agent-cli", "/proj/cli")
        cdb.insert_message("agent-cli", "user", "approve plan?", "ask")

        relay_outbound_messages(_config(), cdb)

        mock_send.assert_called_once()

    def test_ask_from_task_without_origin_id_is_relayed(self, mocker, cdb, db_path):
        """The exact x-cleaner regression: chat_enqueue_task created a
        task without origin_message_id, the worker called chat_ask, and
        before the fix the question was drained without SMTP."""
        mock_send = mocker.patch("src.chat_relay.send_reply", return_value="<r@x>")
        tq = TaskQueue(db_path)
        tid = tq.enqueue("/proj/p", "do it")
        cdb.insert_message("agent-p", "user", "approve plan?", "ask", task_id=tid)

        relay_outbound_messages(_config(), cdb)

        mock_send.assert_called_once()


class TestNotifyGate:
    """chat_notify still respects email-origin so CLI agents don't spam."""

    def test_cli_only_notify_is_dropped(self, mocker, cdb):
        mock_send = mocker.patch("src.chat_relay.send_reply", return_value="<r@x>")
        cdb.register_agent("agent-cli", "/proj/cli")
        cdb.insert_message("agent-cli", "user", "fyi", "notify")

        relay_outbound_messages(_config(), cdb)

        mock_send.assert_not_called()
        assert cdb.get_pending_messages_for("user") == []

    def test_notify_after_user_to_agent_command_relays(self, mocker, cdb):
        mock_send = mocker.patch("src.chat_relay.send_reply", return_value="<r@x>")
        cdb.register_agent("agent-foo", "/proj/foo")
        cdb.insert_message("user", "agent-foo", "go", "command")
        cdb.insert_message("agent-foo", "user", "ok", "notify")

        relay_outbound_messages(_config(), cdb)

        mock_send.assert_called_once()

    def test_notify_with_task_origin_message_id_relays(self, mocker, cdb, db_path):
        mock_send = mocker.patch("src.chat_relay.send_reply", return_value="<r@x>")
        tq = TaskQueue(db_path)
        tid = tq.enqueue(
            "/proj/p", "do it",
            origin_message_id="<inbound@mail>",
            origin_subject="[task-1] do it",
        )
        cdb.insert_message("agent-p", "user", "done", "notify", task_id=tid)

        relay_outbound_messages(_config(), cdb)

        mock_send.assert_called_once()

    def test_notify_with_task_no_origin_id_dropped(self, mocker, cdb, db_path):
        mock_send = mocker.patch("src.chat_relay.send_reply", return_value="<r@x>")
        tq = TaskQueue(db_path)
        tid = tq.enqueue("/proj/p", "do it")  # no origin_message_id
        cdb.insert_message("agent-p", "user", "done", "notify", task_id=tid)

        relay_outbound_messages(_config(), cdb)

        mock_send.assert_not_called()
        assert cdb.get_pending_messages_for("user") == []


class TestThreadMatchPlumbing:
    """Every relayed mail must populate both lookup paths so the user's
    reply auths via security.is_authorized's chat-thread match."""

    def test_relayed_ask_records_in_messages_and_outbound_emails(self, mocker, cdb):
        mocker.patch("src.chat_relay.send_reply", return_value="<sent@cocode.dk>")
        cdb.register_agent("agent-cli", "/proj/cli")
        msg = cdb.insert_message("agent-cli", "user", "?", "ask")

        relay_outbound_messages(_config(), cdb)

        # Legacy lookup — messages.email_message_id
        row = cdb._conn.execute(
            "SELECT email_message_id FROM messages WHERE id=?", (msg["id"],),
        ).fetchone()
        assert row["email_message_id"] == "<sent@cocode.dk>"
        # New lookup — outbound_emails
        out = cdb.find_outbound_email("<sent@cocode.dk>")
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
