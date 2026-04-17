"""Integration tests for the main orchestration loop."""
import email.message
import os
import signal
import pytest
from unittest.mock import MagicMock, patch, call


def _make_authorized_msg(secret: str = "testsecret") -> email.message.Message:
    msg = email.message.EmailMessage()
    msg["From"] = "Babak <bb@cocode.dk>"
    msg["Return-Path"] = "<bb@cocode.dk>"
    msg["Subject"] = f"AUTH:{secret} list files"
    msg["Message-ID"] = "<test001@mail>"
    msg.set_content("list files in /tmp")
    return msg


def _make_unauthorized_msg() -> email.message.Message:
    msg = email.message.EmailMessage()
    msg["From"] = "hacker@evil.com"
    msg["Return-Path"] = "<hacker@evil.com>"
    msg["Subject"] = "run rm -rf /"
    msg["Message-ID"] = "<evil001@mail>"
    msg.set_content("rm -rf /")
    return msg


class TestOrchestration:
    def test_authorized_email_triggers_execution(self, mocker):
        from main import process_email
        mock_execute = mocker.patch("main.execute_command", return_value="file list output")
        mock_reply = mocker.patch("main.send_threaded_reply")

        msg = _make_authorized_msg("testsecret")
        config = {
            "authorized_sender": "bb@cocode.dk",
            "shared_secret": "testsecret",
            "gpg_fingerprint": "",
            "gpg_home": None,
            "smtp_host": "send.one.com",
            "smtp_port": 465,
            "username": "claude@cocode.dk",
            "password": "pw",
            "claude_timeout": 30,
            "claude_bin": "claude",
        }
        process_email(msg, config)
        mock_execute.assert_called_once()
        # Two replies: progress ack, then final output
        assert mock_reply.call_count == 2

    def test_authorized_email_sends_progress_ack_before_execute(self, mocker):
        """The progress ack must be sent before execute_command runs, not after."""
        from main import process_email
        call_order = []
        mocker.patch(
            "main.execute_command",
            side_effect=lambda *a, **kw: (call_order.append("execute"), "output")[1],
        )
        mocker.patch(
            "main.send_threaded_reply",
            side_effect=lambda *a, **kw: call_order.append(f"reply:{a[2][:80]}"),
        )

        msg = _make_authorized_msg("testsecret")
        config = {
            "authorized_sender": "bb@cocode.dk",
            "shared_secret": "testsecret",
            "gpg_fingerprint": "", "gpg_home": None,
            "smtp_host": "h", "smtp_port": 465,
            "username": "u", "password": "pw",
            "claude_timeout": 42, "claude_bin": "claude",
        }
        process_email(msg, config)

        assert call_order[0].startswith("reply:")
        assert "Command received" in call_order[0]
        assert "42" in call_order[0]  # timeout should appear in the ack
        assert call_order[1] == "execute"
        assert call_order[2].startswith("reply:")

    def test_progress_ack_failure_does_not_abort_execution(self, mocker):
        """If the ack send fails, we should still run the command."""
        import smtplib
        from main import process_email
        mock_execute = mocker.patch("main.execute_command", return_value="output")
        mock_reply = mocker.patch(
            "main.send_threaded_reply",
            side_effect=[smtplib.SMTPException("ack failed"), None],
        )

        msg = _make_authorized_msg("testsecret")
        config = {
            "authorized_sender": "bb@cocode.dk",
            "shared_secret": "testsecret",
            "gpg_fingerprint": "", "gpg_home": None,
            "smtp_host": "h", "smtp_port": 465,
            "username": "u", "password": "pw",
            "claude_timeout": 30, "claude_bin": "claude",
        }
        process_email(msg, config)

        # Execute ran despite ack failure
        mock_execute.assert_called_once()
        # Both sends attempted
        assert mock_reply.call_count == 2

    def test_unauthorized_email_ignored(self, mocker):
        from main import process_email
        mock_execute = mocker.patch("main.execute_command")
        mock_reply = mocker.patch("main.send_threaded_reply")

        msg = _make_unauthorized_msg()
        config = {
            "authorized_sender": "bb@cocode.dk",
            "shared_secret": "testsecret",
            "gpg_fingerprint": "",
            "gpg_home": None,
            "smtp_host": "send.one.com",
            "smtp_port": 465,
            "username": "claude@cocode.dk",
            "password": "pw",
            "claude_timeout": 30,
            "claude_bin": "claude",
        }
        process_email(msg, config)
        mock_execute.assert_not_called()
        mock_reply.assert_not_called()

    def test_empty_command_body_skipped(self, mocker):
        from main import process_email
        mock_execute = mocker.patch("main.execute_command")
        mock_reply = mocker.patch("main.send_threaded_reply")
        mocker.patch("main.is_authorized", return_value=True)

        msg = email.message.EmailMessage()
        msg["From"] = "bb@cocode.dk"
        msg["Return-Path"] = "<bb@cocode.dk>"
        msg["Subject"] = "AUTH:testsecret cmd"
        msg["Message-ID"] = "<test002@mail>"
        msg.set_content("")  # empty body

        config = {
            "authorized_sender": "bb@cocode.dk",
            "shared_secret": "testsecret",
            "gpg_fingerprint": "", "gpg_home": None,
            "smtp_host": "h", "smtp_port": 465,
            "username": "u", "password": "p",
            "claude_timeout": 30, "claude_bin": "claude",
        }
        process_email(msg, config)
        mock_execute.assert_not_called()
        mock_reply.assert_not_called()


class TestConfig:
    def test_config_reads_all_required_vars(self, monkeypatch):
        env = {
            "IMAP_HOST": "imap.x.com", "IMAP_PORT": "993",
            "SMTP_HOST": "smtp.x.com", "SMTP_PORT": "465",
            "EMAIL_ADDRESS": "a@x.com", "EMAIL_PASSWORD": "pw",
            "AUTHORIZED_SENDER": "b@x.com", "SHARED_SECRET": "sec",
            "POLL_INTERVAL": "15", "CLAUDE_TIMEOUT": "60",
            "CLAUDE_BIN": "/usr/bin/claude", "CLAUDE_CWD": "/tmp",
            "STATE_FILE": "ids.json", "EMAIL_DOMAIN": "x.com",
            "CHAT_DB_PATH": "chat.db", "CHAT_URL": "http://localhost:8420/sse",
            "SERVICE_NAME_EMAIL": "email.service",
            "SERVICE_NAME_CHAT": "chat.service",
        }
        for k, v in env.items():
            monkeypatch.setenv(k, v)

        from main import _config
        cfg = _config()
        assert cfg["imap_host"] == "imap.x.com"
        assert cfg["imap_port"] == 993
        assert cfg["claude_cwd"] == "/tmp"
        assert cfg["auth_prefix"] == "AUTH:sec"
        assert cfg["service_name_chat"] == "chat.service"

    def test_config_missing_var_raises(self, monkeypatch):
        # Clear all env vars that _config reads
        for key in ["IMAP_HOST", "IMAP_PORT", "SMTP_HOST", "SMTP_PORT",
                     "EMAIL_ADDRESS", "EMAIL_PASSWORD", "AUTHORIZED_SENDER"]:
            monkeypatch.delenv(key, raising=False)

        from main import _config
        with pytest.raises(KeyError):
            _config()


class TestSignalHandler:
    def test_handle_signal_sets_shutdown(self):
        import main
        original = main._shutdown
        try:
            main._shutdown = False
            main._handle_signal(signal.SIGTERM, None)
            assert main._shutdown is True
        finally:
            main._shutdown = original


class TestRunLoop:
    def test_run_loop_single_iteration(self, mocker, tmp_path):
        import main

        # Make loop exit after one iteration
        original = main._shutdown
        call_count = 0
        def fake_sleep(n):
            nonlocal call_count
            call_count += 1
            if call_count >= 1:
                main._shutdown = True
        mocker.patch("main.time.sleep", side_effect=fake_sleep)

        mock_poller_cls = mocker.patch("main.EmailPoller")
        mock_poller = MagicMock()
        mock_poller.fetch_unseen.return_value = []
        mock_poller_cls.return_value = mock_poller

        mocker.patch("main.ChatDB")
        mocker.patch("main.relay_outbound_messages")

        config = {
            "imap_host": "h", "imap_port": 993,
            "username": "u", "password": "p",
            "state_file": str(tmp_path / "ids.json"),
            "chat_db_path": str(tmp_path / "chat.db"),
            "poll_interval": 1,
            "authorized_sender": "bb@x.com",
        }
        try:
            main._shutdown = False
            main.run_loop(config)
        finally:
            main._shutdown = original

        mock_poller.connect.assert_called()
        mock_poller.disconnect.assert_called()

    def test_run_loop_processes_email(self, mocker, tmp_path):
        import main

        msg = email.message.EmailMessage()
        msg["Message-ID"] = "<loop-test@mail>"
        msg.set_content("test")

        call_count = 0
        def fake_sleep(n):
            nonlocal call_count
            call_count += 1
            if call_count >= 1:
                main._shutdown = True
        mocker.patch("main.time.sleep", side_effect=fake_sleep)

        mock_poller_cls = mocker.patch("main.EmailPoller")
        mock_poller = MagicMock()
        mock_poller.fetch_unseen.return_value = [("1", msg)]
        mock_poller_cls.return_value = mock_poller

        mocker.patch("main.ChatDB")
        mocker.patch("main.relay_outbound_messages")
        mock_process = mocker.patch("main.process_email")

        config = {
            "imap_host": "h", "imap_port": 993,
            "username": "u", "password": "p",
            "state_file": str(tmp_path / "ids.json"),
            "chat_db_path": str(tmp_path / "chat.db"),
            "poll_interval": 1,
            "authorized_sender": "bb@x.com",
        }
        original = main._shutdown
        try:
            main._shutdown = False
            main.run_loop(config)
        finally:
            main._shutdown = original

        mock_process.assert_called_once()
        mock_poller.mark_processed.assert_called_once()

    def test_run_loop_handles_imap_error(self, mocker, tmp_path):
        import main

        call_count = 0
        def fake_sleep(n):
            nonlocal call_count
            call_count += 1
            if call_count >= 1:
                main._shutdown = True
        mocker.patch("main.time.sleep", side_effect=fake_sleep)

        mock_poller_cls = mocker.patch("main.EmailPoller")
        mock_poller = MagicMock()
        mock_poller.connect.side_effect = Exception("connection refused")
        mock_poller_cls.return_value = mock_poller

        mocker.patch("main.ChatDB")
        mocker.patch("main.relay_outbound_messages")

        config = {
            "imap_host": "h", "imap_port": 993,
            "username": "u", "password": "p",
            "state_file": str(tmp_path / "ids.json"),
            "chat_db_path": str(tmp_path / "chat.db"),
            "poll_interval": 1,
            "authorized_sender": "bb@x.com",
        }
        original = main._shutdown
        try:
            main._shutdown = False
            main.run_loop(config)  # should not raise
        finally:
            main._shutdown = original

    def test_run_loop_handles_process_email_error(self, mocker, tmp_path):
        import main

        msg = email.message.EmailMessage()
        msg["Message-ID"] = "<err@mail>"
        msg.set_content("boom")

        call_count = 0
        def fake_sleep(n):
            nonlocal call_count
            call_count += 1
            if call_count >= 1:
                main._shutdown = True
        mocker.patch("main.time.sleep", side_effect=fake_sleep)

        mock_poller_cls = mocker.patch("main.EmailPoller")
        mock_poller = MagicMock()
        mock_poller.fetch_unseen.return_value = [("1", msg)]
        mock_poller_cls.return_value = mock_poller

        mocker.patch("main.ChatDB")
        mocker.patch("main.relay_outbound_messages")
        mocker.patch("main.process_email", side_effect=RuntimeError("boom"))

        config = {
            "imap_host": "h", "imap_port": 993,
            "username": "u", "password": "p",
            "state_file": str(tmp_path / "ids.json"),
            "chat_db_path": str(tmp_path / "chat.db"),
            "poll_interval": 1,
            "authorized_sender": "bb@x.com",
        }
        original = main._shutdown
        try:
            main._shutdown = False
            main.run_loop(config)  # should not raise despite process_email error
        finally:
            main._shutdown = original

        # mark_processed still called in finally block
        mock_poller.mark_processed.assert_called_once()

    def test_run_loop_handles_relay_error(self, mocker, tmp_path):
        import main

        call_count = 0
        def fake_sleep(n):
            nonlocal call_count
            call_count += 1
            if call_count >= 1:
                main._shutdown = True
        mocker.patch("main.time.sleep", side_effect=fake_sleep)

        mock_poller_cls = mocker.patch("main.EmailPoller")
        mock_poller = MagicMock()
        mock_poller.fetch_unseen.return_value = []
        mock_poller_cls.return_value = mock_poller

        mocker.patch("main.ChatDB")
        mocker.patch("main.relay_outbound_messages", side_effect=RuntimeError("relay fail"))

        config = {
            "imap_host": "h", "imap_port": 993,
            "username": "u", "password": "p",
            "state_file": str(tmp_path / "ids.json"),
            "chat_db_path": str(tmp_path / "chat.db"),
            "poll_interval": 1,
            "authorized_sender": "bb@x.com",
        }
        original = main._shutdown
        try:
            main._shutdown = False
            main.run_loop(config)  # should not raise
        finally:
            main._shutdown = original
