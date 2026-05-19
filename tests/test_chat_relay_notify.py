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
