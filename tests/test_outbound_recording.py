"""Recording coverage for non-relay outbound paths.

The relay (``chat_relay``) already had Message-ID persistence via
``messages.email_message_id``. The other outbound paths — CLI-fallback
``[Running]`` / ``[Result]``, @agent ACKs (``send_threaded_reply``),
JSON envelope replies (``_send_json_reply``) — each call ``send_reply``
directly. Before this work their Message-IDs were thrown away, so user
replies on those threads failed ``security.is_authorized``'s chat-
thread match. These tests pin down that every such path now writes
into ``outbound_emails`` so security can accept replies.
"""
import email.message

import pytest

from src.chat_db import ChatDB


def _config():
    return {
        "smtp_host": "smtp.example.com", "smtp_port": 465,
        "username": "agent@example.com", "password": "pw",
        "authorized_sender": "user@example.com",
        "email_domain": "example.com",
        "shared_secret": "s", "auth_prefix": "AUTH:s",
        "claude_cwd": "/tmp",
        "service_name_chat": "claude-chat.service",
        "service_name_email": "claude-email.service",
        "claude_bin": "claude", "claude_yolo": False,
        "chat_url": "http://127.0.0.1:8420/sse",
        "claude_timeout": 30,
        "universes": [],
    }


def _inbound(subject="cmd", msg_id="<inbound-1@example.com>"):
    m = email.message.EmailMessage()
    m["From"] = "user@example.com"
    m["Return-Path"] = "<user@example.com>"
    m["Message-ID"] = msg_id
    m["Subject"] = subject
    m.set_content("body")
    return m


@pytest.fixture
def cdb(tmp_path):
    return ChatDB(str(tmp_path / "rec.db"))


class TestSendThreadedReplyRecords:
    def test_records_when_chat_db_provided(self, cdb, mocker):
        from src.chat_handlers import send_threaded_reply
        mocker.patch(
            "src.chat_handlers.send_reply", return_value="<ack-1@example.com>",
        )
        send_threaded_reply(
            _config(), _inbound(), "ok", tag="Dispatched",
            chat_db=cdb, kind="ack", sender_agent="agent-foo",
        )
        row = cdb.find_outbound_email("<ack-1@example.com>")
        assert row is not None
        assert row["kind"] == "ack"
        assert row["sender_agent"] == "agent-foo"

    def test_no_record_when_chat_db_omitted(self, cdb, mocker):
        from src.chat_handlers import send_threaded_reply
        mocker.patch(
            "src.chat_handlers.send_reply", return_value="<ack-2@example.com>",
        )
        send_threaded_reply(_config(), _inbound(), "ok", tag="Dispatched")
        assert cdb.find_outbound_email("<ack-2@example.com>") is None


class TestJsonReplyRecords:
    def test_records_envelope_reply(self, cdb, mocker):
        from src.json_handler import _send_json_reply
        mocker.patch(
            "src.json_handler.send_reply", return_value="<env-1@example.com>",
        )
        _send_json_reply(_config(), _inbound(), '{"v":1}', chat_db=cdb)
        row = cdb.find_outbound_email("<env-1@example.com>")
        assert row is not None
        assert row["kind"] == "envelope_reply"


class TestCliFallbackRecords:
    """main.process_email's [Running] ack and [Result] body must persist
    their Message-IDs so a user reply on either thread auths via
    security thread-match. This was the actual cause of the 18:07
    rejection in the chrome-extension incident."""

    def test_running_ack_and_result_both_recorded(self, cdb, mocker, tmp_path):
        import main as main_mod
        # Stub the SMTP send to return distinct IDs for ack vs result.
        sent_ids = iter(["<run@x>", "<res@x>"])
        mocker.patch(
            "src.chat_handlers.send_reply",
            side_effect=lambda **_: next(sent_ids),
        )
        # Skip the actual claude CLI invocation.
        mocker.patch("main.execute_command", return_value="[output]")

        cfg = {
            **_config(),
            "shared_secret": "s",
            "gpg_fingerprint": "", "gpg_home": None,
            "authorized_senders": ["user@example.com"],
            "claude_extra_env": None, "claude_model": None,
            "claude_effort": None, "claude_max_budget_usd": None,
            "llm_router": False,
        }
        msg = _inbound(subject="AUTH:s do thing")

        main_mod.process_email(msg, cfg, chat_db=cdb)

        assert cdb.find_outbound_email("<run@x>") is not None
        assert cdb.find_outbound_email("<res@x>") is not None
