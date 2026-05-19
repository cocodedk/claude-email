"""Integration tests for the main orchestration loop — run_loop basic paths."""
import email.message
import pytest
from unittest.mock import MagicMock, patch, call


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

        mocker.patch("src.dispatch.ChatDB")
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

        mocker.patch("src.dispatch.ChatDB")
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

        mocker.patch("src.dispatch.ChatDB")
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

        mocker.patch("src.dispatch.ChatDB")
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
