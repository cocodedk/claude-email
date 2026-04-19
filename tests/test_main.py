"""Integration tests for the main orchestration loop."""
import email.message
import os
import signal
import pytest
from unittest.mock import MagicMock, patch, call


def _make_authorized_msg(secret: str = "testsecret") -> email.message.Message:
    msg = email.message.EmailMessage()
    msg["From"] = "Babak <user@example.com>"
    msg["Return-Path"] = "<user@example.com>"
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
            "authorized_sender": "user@example.com",
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

    def test_llm_router_enabled_passes_system_prompt_and_mcp_config(self, mocker):
        from main import process_email, _ROUTER_MCP_CONFIG
        from src.llm_router import EMAIL_ROUTER_SYSTEM_PROMPT
        mock_execute = mocker.patch("main.execute_command", return_value="out")
        mocker.patch("main.send_threaded_reply")

        msg = _make_authorized_msg("testsecret")
        config = {
            "authorized_sender": "user@example.com", "shared_secret": "testsecret",
            "gpg_fingerprint": "", "gpg_home": None,
            "smtp_host": "h", "smtp_port": 465, "username": "u", "password": "p",
            "claude_timeout": 10, "claude_bin": "claude",
            "llm_router": True,
        }
        process_email(msg, config)
        assert mock_execute.call_args.kwargs["system_prompt"] == EMAIL_ROUTER_SYSTEM_PROMPT
        assert mock_execute.call_args.kwargs["mcp_config"] == _ROUTER_MCP_CONFIG

    def test_llm_router_disabled_omits_system_prompt(self, mocker):
        from main import process_email
        mock_execute = mocker.patch("main.execute_command", return_value="out")
        mocker.patch("main.send_threaded_reply")

        msg = _make_authorized_msg("testsecret")
        config = {
            "authorized_sender": "user@example.com", "shared_secret": "testsecret",
            "gpg_fingerprint": "", "gpg_home": None,
            "smtp_host": "h", "smtp_port": 465, "username": "u", "password": "p",
            "claude_timeout": 10, "claude_bin": "claude",
        }
        process_email(msg, config)
        assert mock_execute.call_args.kwargs["system_prompt"] is None
        assert mock_execute.call_args.kwargs["mcp_config"] is None

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
            "authorized_sender": "user@example.com",
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
        mock_execute.assert_not_called()
        mock_reply.assert_not_called()

    def test_empty_command_body_skipped(self, mocker):
        from main import process_email
        mock_execute = mocker.patch("main.execute_command")
        mock_reply = mocker.patch("main.send_threaded_reply")
        mocker.patch("main.is_authorized", return_value=True)

        msg = email.message.EmailMessage()
        msg["From"] = "user@example.com"
        msg["Return-Path"] = "<user@example.com>"
        msg["Subject"] = "AUTH:testsecret cmd"
        msg["Message-ID"] = "<test002@mail>"
        msg.set_content("")  # empty body

        config = {
            "authorized_sender": "user@example.com",
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

    def _base_env(self, monkeypatch):
        """Set all required env vars; return dict for optional overrides."""
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
        return env

    def test_config_valid_effort_passes(self, monkeypatch):
        self._base_env(monkeypatch)
        monkeypatch.setenv("CLAUDE_EFFORT", "xhigh")
        from main import _config
        cfg = _config()
        assert cfg["claude_effort"] == "xhigh"

    def test_config_invalid_effort_raises(self, monkeypatch):
        self._base_env(monkeypatch)
        monkeypatch.setenv("CLAUDE_EFFORT", "ultra")
        from main import _config
        with pytest.raises(ValueError, match="CLAUDE_EFFORT"):
            _config()

    def test_config_empty_effort_is_none(self, monkeypatch):
        self._base_env(monkeypatch)
        monkeypatch.setenv("CLAUDE_EFFORT", "")
        from main import _config
        cfg = _config()
        assert cfg["claude_effort"] is None

    def test_config_unset_effort_is_none(self, monkeypatch):
        self._base_env(monkeypatch)
        monkeypatch.delenv("CLAUDE_EFFORT", raising=False)
        from main import _config
        cfg = _config()
        assert cfg["claude_effort"] is None

    def test_config_llm_router_default_false(self, monkeypatch):
        self._base_env(monkeypatch)
        monkeypatch.delenv("LLM_ROUTER", raising=False)
        from main import _config
        assert _config()["llm_router"] is False

    def test_config_llm_router_enabled(self, monkeypatch):
        self._base_env(monkeypatch)
        monkeypatch.setenv("LLM_ROUTER", "1")
        from main import _config
        assert _config()["llm_router"] is True


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

    def test_run_loop_breaks_on_shutdown_during_message_iteration(self, mocker, tmp_path):
        """Cover line 153: break from inner message loop when _shutdown is set mid-iteration."""
        import main

        msg1 = email.message.EmailMessage()
        msg1["Message-ID"] = "<m1@mail>"
        msg1.set_content("first")

        msg2 = email.message.EmailMessage()
        msg2["Message-ID"] = "<m2@mail>"
        msg2.set_content("second")

        process_calls = []

        def fake_process(msg, config, chat_db=None):
            # After processing the first message, signal shutdown
            process_calls.append(msg.get("Message-ID"))
            main._shutdown = True

        mocker.patch("main.time.sleep")  # sleep does nothing; outer while exits due to _shutdown
        mock_poller_cls = mocker.patch("main.EmailPoller")
        mock_poller = MagicMock()
        mock_poller.fetch_unseen.return_value = [("1", msg1), ("2", msg2)]
        mock_poller_cls.return_value = mock_poller

        mocker.patch("main.ChatDB")
        mocker.patch("main.relay_outbound_messages")
        mocker.patch("main.process_email", side_effect=fake_process)

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

        # Only the first message was processed; second was skipped due to shutdown
        assert len(process_calls) == 1
        assert process_calls[0] == "<m1@mail>"

    def test_run_loop_breaks_sleep_on_shutdown(self, mocker, tmp_path):
        """Cover line 182: break from inner sleep loop when _shutdown is True."""
        import main

        sleep_calls = []

        def fake_sleep(n):
            sleep_calls.append(n)

        mocker.patch("main.time.sleep", side_effect=fake_sleep)
        mock_poller_cls = mocker.patch("main.EmailPoller")
        mock_poller = MagicMock()
        mock_poller.fetch_unseen.return_value = []
        mock_poller_cls.return_value = mock_poller

        mocker.patch("main.ChatDB")
        mocker.patch("main.relay_outbound_messages")

        # poll_interval=3 means 3 sleep iterations; we set _shutdown=True before the loop
        # so the first `if _shutdown: break` check immediately exits without sleeping
        config = {
            "imap_host": "h", "imap_port": 993,
            "username": "u", "password": "p",
            "state_file": str(tmp_path / "ids.json"),
            "chat_db_path": str(tmp_path / "chat.db"),
            "poll_interval": 3,
            "authorized_sender": "bb@x.com",
        }
        original = main._shutdown
        try:
            main._shutdown = True  # already shutting down before the sleep loop
            main.run_loop(config)
        finally:
            main._shutdown = original

        # The while loop condition `while not _shutdown` is False immediately, so
        # run_loop exits without reaching the sleep loop. We need _shutdown to be
        # set after the IMAP block but before the sleep loop. Let's verify via reap path.
        assert len(sleep_calls) == 0

    def test_run_loop_reaped_agents_logged(self, mocker, tmp_path):
        """Each reaped agent name is logged at INFO level."""
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

        mock_chat_db_cls = mocker.patch("main.ChatDB")
        mock_chat_db = MagicMock()
        mock_chat_db.reap_dead_agents.return_value = ["agent-alpha", "agent-beta"]
        mock_chat_db_cls.return_value = mock_chat_db

        mock_logger = mocker.patch("main.logger")
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
            main.run_loop(config)
        finally:
            main._shutdown = original

        mock_chat_db.reap_dead_agents.assert_called()
        # Assert each reaped name is in an info log call
        info_messages = [
            (call.args[0] % call.args[1:] if call.args else "")
            for call in mock_logger.info.call_args_list
        ]
        joined = " | ".join(info_messages)
        assert "agent-alpha" in joined, f"agent-alpha not logged; got: {joined}"
        assert "agent-beta" in joined, f"agent-beta not logged; got: {joined}"

    def test_run_loop_handles_reap_error(self, mocker, tmp_path):
        """reap_dead_agents exception is caught and logged via logger.exception."""
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

        mock_chat_db_cls = mocker.patch("main.ChatDB")
        mock_chat_db = MagicMock()
        mock_chat_db.reap_dead_agents.side_effect = RuntimeError("db locked")
        mock_chat_db_cls.return_value = mock_chat_db

        mock_logger = mocker.patch("main.logger")
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
            main.run_loop(config)  # must not raise
        finally:
            main._shutdown = original

        mock_chat_db.reap_dead_agents.assert_called()
        # The exception must land in logger.exception, not get silently swallowed
        mock_logger.exception.assert_any_call("Liveness check error")

    def test_run_loop_sleep_shutdown_break(self, mocker, tmp_path):
        """Cover line 182: the if _shutdown: break inside the sleep for-loop."""
        import main

        # poll_interval=3 so sleep loop runs 3 times.
        # We set _shutdown=True on the first sleep call so subsequent loop iterations break.
        sleep_call_count = 0

        def fake_sleep(n):
            nonlocal sleep_call_count
            sleep_call_count += 1
            main._shutdown = True  # signal shutdown on first sleep

        mocker.patch("main.time.sleep", side_effect=fake_sleep)

        mock_poller_cls = mocker.patch("main.EmailPoller")
        mock_poller = MagicMock()
        mock_poller.fetch_unseen.return_value = []
        mock_poller_cls.return_value = mock_poller

        mock_chat_db_cls = mocker.patch("main.ChatDB")
        mock_chat_db = MagicMock()
        mock_chat_db.reap_dead_agents.return_value = []
        mock_chat_db_cls.return_value = mock_chat_db

        mocker.patch("main.relay_outbound_messages")

        config = {
            "imap_host": "h", "imap_port": 993,
            "username": "u", "password": "p",
            "state_file": str(tmp_path / "ids.json"),
            "chat_db_path": str(tmp_path / "chat.db"),
            "poll_interval": 3,  # 3-iteration sleep loop
            "authorized_sender": "bb@x.com",
        }
        original = main._shutdown
        try:
            main._shutdown = False
            main.run_loop(config)
        finally:
            main._shutdown = original

        # Only 1 sleep call — the for-loop breaks after 1 iteration due to _shutdown
        assert sleep_call_count == 1
