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


class TestRelayStampsTaskId:
    """An agent's chat_notify carries msg.task_id through the bus; the
    relay must persist it on outbound_emails so a user reply on this
    thread can be walked back to the originating task (Phase F)."""

    def test_relay_passes_task_id_to_outbound_table(
        self, tmp_path, mocker,
    ):
        from src.task_queue import TaskQueue

        cdb = ChatDB(str(tmp_path / "db"))
        cdb.register_agent("agent-p", str(tmp_path))
        # Seed a task so _should_relay treats the message as email-origin.
        tq = TaskQueue(str(tmp_path / "db"))
        tid = tq.enqueue(
            str(tmp_path), "x",
            origin_message_id="<orig@x>",
        )
        cdb.insert_message(
            "agent-p", "user", "result body", "notify", task_id=tid,
        )
        mocker.patch(
            "src.chat_relay.send_reply", return_value="<sent-id@x>",
        )
        relay_outbound_messages(_config(), cdb)

        row = cdb.find_outbound_email("<sent-id@x>")
        assert row is not None
        assert row["task_id"] == tid

    def test_relay_without_task_id_records_null(
        self, tmp_path, mocker,
    ):
        """ask messages from a CLI-only agent (no task) still relay (ask
        always relays) but their outbound row has task_id=NULL."""
        cdb = ChatDB(str(tmp_path / "db2"))
        cdb.register_agent("agent-q", str(tmp_path))
        cdb.insert_message(
            "agent-q", "user", "should I continue?", "ask",
        )  # no task_id
        mocker.patch(
            "src.chat_relay.send_reply", return_value="<sent-q@x>",
        )
        relay_outbound_messages(_config(), cdb)

        row = cdb.find_outbound_email("<sent-q@x>")
        assert row is not None
        assert row["task_id"] is None
