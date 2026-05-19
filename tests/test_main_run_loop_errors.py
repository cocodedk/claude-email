"""Integration tests for the main orchestration loop — run_loop relay/reap error paths."""
import email.message
import pytest
from unittest.mock import MagicMock, patch, call


class TestRunLoop:
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

        mocker.patch("src.dispatch.ChatDB")
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

        mock_chat_db_cls = mocker.patch("src.dispatch.ChatDB")
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

        mock_chat_db_cls = mocker.patch("src.dispatch.ChatDB")
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
