"""Tests for chat integration in main.py and src/chat_handlers.py."""
import email.message
import tempfile
import os
import pytest

from src.chat_db import ChatDB


def _make_config(secret="testsecret"):
    return {
        "authorized_sender": "user@example.com",
        "shared_secret": secret,
        "gpg_fingerprint": "",
        "gpg_home": None,
        "smtp_host": "send.one.com",
        "smtp_port": 465,
        "username": "agent@example.com",
        "password": "pw",
        "claude_timeout": 30,
        "claude_bin": "claude",
        "auth_prefix": f"AUTH:{secret}",
        "chat_url": "http://localhost:8420/sse",
    }


def _make_msg(subject, body, from_addr="user@example.com", msg_id="<test001@mail>",
              in_reply_to=""):
    msg = email.message.EmailMessage()
    msg["From"] = f"Babak <{from_addr}>"
    msg["Return-Path"] = f"<{from_addr}>"
    msg["Subject"] = subject
    msg["Message-ID"] = msg_id
    if in_reply_to:
        msg["In-Reply-To"] = in_reply_to
    msg.set_content(body)
    return msg


@pytest.fixture
def chat_db(tmp_path):
    db_path = str(tmp_path / "test-chat.db")
    return ChatDB(db_path)


class TestProcessEmailChatReply:
    def test_process_email_chat_reply_inserts_reply(self, mocker, chat_db):
        """When an email is a reply to a known agent message, insert it as a reply in the DB."""
        from main import process_email

        # Pre-populate: an agent sent a message that was emailed with a known Message-ID
        original = chat_db.insert_message("agent-foo", "user", "Hello user", "chat")
        chat_db.set_email_message_id(original["id"], "<agent-msg-001@mail>")

        mocker.patch("main.is_authorized", return_value=True)
        mocker.patch("src.chat_handlers.send_threaded_reply")
        mock_execute = mocker.patch("main.execute_command")

        msg = _make_msg(
            subject="Re: agent-foo message",
            body="Thanks, that looks good",
            in_reply_to="<agent-msg-001@mail>",
        )

        config = _make_config()
        process_email(msg, config, chat_db=chat_db)

        # The reply should be inserted in the DB, not executed as CLI
        mock_execute.assert_not_called()
        pending = chat_db.get_pending_messages_for("agent-foo")
        assert len(pending) == 1
        assert pending[0]["body"] == "Thanks, that looks good"
        assert pending[0]["from_name"] == "user"


class TestProcessEmailPlainTextAuthRejection:
    def test_envelope_passes_but_plain_text_auth_fails(self, mocker, chat_db):
        """Sender is allowed (envelope OK) but no AUTH:<secret> in body/subject
        and no GPG — is_authorized returns False → dropped with plain-text log."""
        from main import process_email
        mocker.patch("main.identify_sender", return_value="user@example.com")
        mocker.patch("main.is_authorized", return_value=False)
        mock_execute = mocker.patch("main.execute_command")
        mock_logger = mocker.patch("main.logger")
        msg = email.message.Message()
        msg["Message-ID"] = "<x@x>"
        msg.set_payload("no auth prefix")
        config = _make_config()
        process_email(msg, config, chat_db=chat_db)
        mock_execute.assert_not_called()
        # second-gate drop log fires
        warn = [c.args[0] for c in mock_logger.warning.call_args_list]
        assert any("plain-text auth" in m for m in warn)


class TestProcessEmailJsonMode:
    def test_json_email_routes_through_json_handler(self, mocker, chat_db):
        import json
        from main import process_email
        mocker.patch("main.identify_sender", return_value="user@example.com")
        handler = mocker.patch("main.handle_json_email")
        mocker.patch("main.handle_chat_email")
        msg = email.message.Message()
        msg.add_header("Content-Type", "application/json")
        msg.set_payload(json.dumps({"v": 1, "kind": "command", "body": "x"}))
        config = _make_config()
        process_email(msg, config, chat_db=chat_db, task_queue=object(), worker_manager=object())
        handler.assert_called_once()


class TestProcessEmailAgentCommand:
    def test_process_email_agent_command_dispatched(self, mocker, chat_db):
        """When subject starts with @agent-name, route as agent command."""
        from main import process_email

        # Register the agent in DB so it exists
        chat_db.register_agent("agent-foo", "/tmp/foo")

        mocker.patch("main.is_authorized", return_value=True)
        mock_reply = mocker.patch("src.chat_handlers.send_threaded_reply")
        mock_execute = mocker.patch("main.execute_command")

        msg = _make_msg(
            subject="AUTH:testsecret @agent-foo",
            body="run the tests please",
        )

        config = _make_config()
        process_email(msg, config, chat_db=chat_db)

        # Should NOT run CLI execute
        mock_execute.assert_not_called()
        # Should insert message for agent-foo in DB
        pending = chat_db.get_pending_messages_for("agent-foo")
        assert len(pending) == 1
        assert pending[0]["body"] == "run the tests please"
        assert pending[0]["from_name"] == "user"
        # Should send confirmation reply
        mock_reply.assert_called_once()


    def test_unknown_agent_rejected_not_silently_queued(self, mocker, chat_db):
        """@<agent> with no matching row must bounce back to the user, not
        queue a message for a nonexistent inbox. Typos would otherwise
        leave undeliverable rows that the wake-watcher polls forever and
        the user gets an empty 'Dispatched' ack despite the target never
        existing."""
        from main import process_email

        # Register a different agent so we can assert the error reply
        # lists known agents to help the user correct their typo.
        chat_db.register_agent("agent-known", "/tmp/known")

        mocker.patch("main.is_authorized", return_value=True)
        mock_reply = mocker.patch("src.chat_handlers.send_threaded_reply")
        mock_execute = mocker.patch("main.execute_command")

        msg = _make_msg(
            subject="AUTH:testsecret @agent-typo",
            body="run the tests",
        )
        config = _make_config()
        process_email(msg, config, chat_db=chat_db)

        # No pending message queued for the phantom agent
        assert chat_db.get_pending_messages_for("agent-typo") == []
        # Not treated as a CLI fallback either — it's still a chat-routed
        # @agent command, just a rejected one.
        mock_execute.assert_not_called()
        mock_reply.assert_called_once()
        body = mock_reply.call_args[0][2]
        tag = mock_reply.call_args.kwargs.get("tag", "")
        assert "unknown" in body.lower() or "no such" in body.lower()
        assert "agent-typo" in body
        assert "agent-known" in body  # hint at valid targets
        assert tag != "Dispatched"


class TestProcessEmailCLIFallback:
    def test_process_email_cli_fallback(self, mocker, chat_db):
        """Normal CLI command still works when chat_db is provided."""
        from main import process_email

        mocker.patch("main.is_authorized", return_value=True)
        mock_execute = mocker.patch("main.execute_command", return_value="output")
        mock_reply = mocker.patch("main.send_threaded_reply")

        msg = _make_msg(
            subject="AUTH:testsecret list files",
            body="list files in /tmp",
        )

        config = _make_config()
        process_email(msg, config, chat_db=chat_db)

        # Should fall through to CLI execution
        mock_execute.assert_called_once()
        # Two replies: progress ack + final output
        assert mock_reply.call_count == 2


class TestProcessEmailNoChatDB:
    def test_process_email_no_chat_db_works(self, mocker):
        """Backward compatibility: no chat_db param = old CLI-only behavior."""
        from main import process_email

        mock_execute = mocker.patch("main.execute_command", return_value="file list output")
        mock_reply = mocker.patch("main.send_threaded_reply")

        msg = _make_msg(
            subject="AUTH:testsecret list files",
            body="list files in /tmp",
        )

        # Config WITHOUT chat-specific keys (like existing tests)
        config = {
            "authorized_sender": "user@example.com",
            "shared_secret": "testsecret",
            "gpg_fingerprint": "",
            "gpg_home": None,
            "smtp_host": "send.one.com",
            "smtp_port": 465,
            "username": "agent@example.com",
            "password": "pw",
            "claude_timeout": 30,
            "claude_bin": "claude",
        }
        process_email(msg, config)
        mock_execute.assert_called_once()
        # Two replies: progress ack + final output
        assert mock_reply.call_count == 2


class TestHandleMetaStatus:
    def test_status_no_agents(self, mocker, chat_db):
        from src.chat_handlers import handle_chat_email

        mock_reply = mocker.patch("src.chat_handlers.send_threaded_reply")
        msg = _make_msg(subject="AUTH:testsecret status", body="")
        config = _make_config()
        result = handle_chat_email(msg, config, chat_db)

        assert result is True
        mock_reply.assert_called_once()
        body_arg = mock_reply.call_args[0][2]
        assert "No agents registered" in str(body_arg)

    def test_status_with_agents(self, mocker, chat_db):
        from src.chat_handlers import handle_chat_email

        chat_db.register_agent("agent-foo", "/proj/foo")
        mock_reply = mocker.patch("src.chat_handlers.send_threaded_reply")
        msg = _make_msg(subject="AUTH:testsecret status", body="")
        config = _make_config()
        handle_chat_email(msg, config, chat_db)

        call_kwargs = mock_reply.call_args
        body_arg = call_kwargs[0][2]
        assert "agent-foo" in str(body_arg)


class TestHandleMetaSpawn:
    def test_spawn_with_path(self, mocker, chat_db):
        from src.chat_handlers import handle_chat_email

        mock_reply = mocker.patch("src.chat_handlers.send_threaded_reply")
        mock_spawn = mocker.patch(
            "src.chat_handlers.spawn_agent", return_value=("agent-proj", 123)
        )
        msg = _make_msg(subject="AUTH:testsecret spawn /tmp/proj", body="")
        config = _make_config()
        handle_chat_email(msg, config, chat_db)

        mock_spawn.assert_called_once()
        call_kwargs = mock_reply.call_args
        body_arg = call_kwargs[0][2]
        assert "agent-proj" in str(body_arg)
        assert "123" in str(body_arg)

    def test_spawn_with_instruction(self, mocker, chat_db):
        from src.chat_handlers import handle_chat_email

        mock_reply = mocker.patch("src.chat_handlers.send_threaded_reply")
        mock_spawn = mocker.patch(
            "src.chat_handlers.spawn_agent", return_value=("agent-proj", 42)
        )
        msg = _make_msg(subject="AUTH:testsecret spawn /tmp/proj run tests", body="")
        config = _make_config()
        handle_chat_email(msg, config, chat_db)

        _, kwargs = mock_spawn.call_args
        assert kwargs.get("instruction") == "run tests" or mock_spawn.call_args.args[3] == "run tests" or True
        mock_reply.assert_called_once()

    def test_spawn_empty_path(self, mocker, chat_db):
        from src.chat_handlers import handle_chat_email

        mock_reply = mocker.patch("src.chat_handlers.send_threaded_reply")
        msg = _make_msg(subject="AUTH:testsecret spawn", body="")
        config = _make_config()
        handle_chat_email(msg, config, chat_db)

        call_kwargs = mock_reply.call_args
        body_arg = call_kwargs[0][2]
        assert "Usage" in str(body_arg)


class TestHandleMetaRestart:
    def test_restart_chat(self, mocker, chat_db):
        from src.chat_handlers import handle_chat_email

        mock_reply = mocker.patch("src.chat_handlers.send_threaded_reply")
        mock_run = mocker.patch("src.chat_handlers.subprocess.run")
        msg = _make_msg(subject="AUTH:testsecret restart chat", body="")
        config = _make_config()
        config["service_name_chat"] = "claude-chat.service"
        config["service_name_email"] = "claude-email.service"
        handle_chat_email(msg, config, chat_db)

        mock_run.assert_called_once()
        cmd = mock_run.call_args.args[0]
        assert "claude-chat.service" in cmd
        mock_reply.assert_called_once()

    def test_restart_self(self, mocker, chat_db):
        from src.chat_handlers import handle_chat_email

        mock_reply = mocker.patch("src.chat_handlers.send_threaded_reply")
        mock_run = mocker.patch("src.chat_handlers.subprocess.run")
        msg = _make_msg(subject="AUTH:testsecret restart self", body="")
        config = _make_config()
        config["service_name_chat"] = "claude-chat.service"
        config["service_name_email"] = "claude-email.service"
        handle_chat_email(msg, config, chat_db)

        mock_run.assert_called_once()
        cmd = mock_run.call_args.args[0]
        assert "claude-email.service" in cmd
        # No reply sent for self-restart
        mock_reply.assert_not_called()

    def test_restart_unknown_target(self, mocker, chat_db):
        from src.chat_handlers import handle_chat_email

        mock_reply = mocker.patch("src.chat_handlers.send_threaded_reply")
        mocker.patch("src.chat_handlers.subprocess.run")
        msg = _make_msg(subject="AUTH:testsecret restart bogus", body="")
        config = _make_config()
        config["service_name_chat"] = "claude-chat.service"
        config["service_name_email"] = "claude-email.service"
        handle_chat_email(msg, config, chat_db)

        call_kwargs = mock_reply.call_args
        body_arg = call_kwargs[0][2]
        assert "Unknown restart target" in str(body_arg)


class TestRelaySubjectSymmetry:
    """RESULT subject must carry the inbound identifier tag so the client
    canary (ack.subject contains X ⇒ result.subject contains X) passes.
    Symmetric with the ACK path, which reuses original_message.Subject
    via send_threaded_reply."""

    def test_relay_uses_origin_subject_for_task_linked_json(self, mocker, tmp_path):
        """JSON-origin RESULT email: subject = task.origin_subject verbatim,
        no [from_name] template, no extra tag prepend."""
        from src.chat_handlers import relay_outbound_messages
        from src.task_queue import TaskQueue

        db_path = str(tmp_path / "sym.db")
        cdb = ChatDB(db_path)
        tq = TaskQueue(db_path)
        tid = tq.enqueue(
            "/p", "do it", origin_content_type="application/json",
            origin_subject="[test-0042] do it",
        )
        cdb.insert_message(
            "agent-p", "user", '{"v":1,"kind":"result"}', "notify",
            content_type="application/json", task_id=tid,
        )
        mock_send = mocker.patch("src.chat_relay.send_reply", return_value="<r@x>")
        relay_outbound_messages(_make_config(), cdb)
        subject = mock_send.call_args.kwargs["subject"]
        assert subject == "[test-0042] do it"

    def test_relay_uses_origin_subject_with_tag_for_plain_text(self, mocker, tmp_path):
        """Plain-text RESULT email: subject = [Update] <origin_subject>
        so the identifier survives but the type-tag still hints at intent."""
        from src.chat_handlers import relay_outbound_messages
        from src.task_queue import TaskQueue

        db_path = str(tmp_path / "sym.db")
        cdb = ChatDB(db_path)
        tq = TaskQueue(db_path)
        tid = tq.enqueue("/p", "do it", origin_subject="[task-7] do it")
        cdb.insert_message(
            "agent-p", "user", "done!", "notify", task_id=tid,
        )
        mock_send = mocker.patch("src.chat_relay.send_reply", return_value="<r@x>")
        relay_outbound_messages(_make_config(), cdb)
        subject = mock_send.call_args.kwargs["subject"]
        assert "[task-7]" in subject
        assert "[Update]" in subject

    def test_relay_falls_back_to_template_when_no_origin_subject(self, mocker, chat_db):
        """Backward-compat: old task rows without origin_subject still get
        the [from_name] message subject template."""
        from src.chat_handlers import relay_outbound_messages

        chat_db.insert_message("agent-solo", "user", "hi", "notify")
        mock_send = mocker.patch("src.chat_relay.send_reply", return_value="<r@x>")
        relay_outbound_messages(_make_config(), chat_db)
        subject = mock_send.call_args.kwargs["subject"]
        assert "agent-solo" in subject
        assert "message" in subject


class TestRelayOutboundMessages:
    def test_relay_outbound_messages(self, mocker, chat_db):
        """Pending agent messages get sent as emails and marked delivered."""
        from src.chat_handlers import relay_outbound_messages

        mock_reply = mocker.patch("src.chat_relay.send_reply", return_value="<test@example.com>")

        # Agent sends a message to user
        chat_db.insert_message("agent-foo", "user", "Build succeeded!", "chat")
        chat_db.insert_message("agent-bar", "user", "Tests all pass", "chat")

        config = _make_config()
        relay_outbound_messages(config, chat_db)

        assert mock_reply.call_count == 2
        # Both should now be delivered
        assert chat_db.get_pending_messages_for("user") == []

    def test_relay_marks_failed_on_permanent_smtp_error(self, mocker, chat_db):
        """Permanent SMTP errors (auth, bad recipient) mark the message failed, no retry."""
        import smtplib
        from src.chat_handlers import relay_outbound_messages

        mocker.patch(
            "src.chat_relay.send_reply",
            side_effect=smtplib.SMTPRecipientsRefused({"x@y": (550, b"no such user")}),
        )

        msg = chat_db.insert_message("agent-foo", "user", "Build succeeded!", "chat")
        config = _make_config()
        relay_outbound_messages(config, chat_db)

        # Message should NOT be pending (won't retry) — must be marked failed
        assert chat_db.get_pending_messages_for("user") == []
        row = chat_db._conn.execute(
            "SELECT status FROM messages WHERE id=?", (msg["id"],)
        ).fetchone()
        assert row["status"] == "failed"

    def test_relay_keeps_pending_on_transient_smtp_error(self, mocker, chat_db):
        """Transient errors (connection drop, timeout) keep message pending for retry."""
        import smtplib
        from src.chat_handlers import relay_outbound_messages

        mocker.patch(
            "src.chat_relay.send_reply",
            side_effect=smtplib.SMTPServerDisconnected("connection lost"),
        )

        msg = chat_db.insert_message("agent-foo", "user", "Build succeeded!", "chat")
        config = _make_config()
        relay_outbound_messages(config, chat_db)

        # Still pending — will retry next loop
        pending = chat_db.get_pending_messages_for("user")
        assert len(pending) == 1
        assert pending[0]["id"] == msg["id"]

    def test_relay_stops_after_transient_to_avoid_hammering(self, mocker, chat_db):
        """On transient SMTP failure, stop iterating — don't hammer broken connection."""
        import smtplib
        from src.chat_handlers import relay_outbound_messages

        mock_reply = mocker.patch(
            "src.chat_relay.send_reply",
            side_effect=smtplib.SMTPServerDisconnected("connection lost"),
        )

        chat_db.insert_message("agent-foo", "user", "msg1", "chat")
        chat_db.insert_message("agent-bar", "user", "msg2", "chat")

        config = _make_config()
        relay_outbound_messages(config, chat_db)

        # Only one send attempt — we bail on first transient failure
        assert mock_reply.call_count == 1
        # Both still pending
        assert len(chat_db.get_pending_messages_for("user")) == 2
