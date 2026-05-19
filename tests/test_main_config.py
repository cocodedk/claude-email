"""Integration tests for the main orchestration loop — config + signal handler."""
import email.message
import os
import signal
import pytest
from unittest.mock import MagicMock, patch, call


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

    def test_tick_housekeeping_logs_ghost_reaped(self, mocker):
        """When sweep_ghosts returns >0, main logs a warning."""
        import main
        mocker.patch("main.relay_outbound_messages")
        mocker.patch("main.sweep_ghosts", return_value=3)
        cdb = MagicMock()
        cdb.reap_dead_agents.return_value = []
        tq = MagicMock()
        mock_logger = mocker.patch("main.logger")
        mocker.patch("main.maybe_cleanup_db")
        main._tick_housekeeping({}, cdb, tq)
        warn_msgs = [c.args[0] for c in mock_logger.warning.call_args_list]
        assert any("Ghost reaper" in m for m in warn_msgs)

    def test_run_loop_dedupes_alias_bundles_in_housekeeping(
        self, mocker, tmp_path,
    ):
        """When a universe has aliases, build_universe_resources registers
        every address against the same (universe, cdb, tq, wm) tuple.
        run_loop must call _tick_housekeeping once per unique bundle, not
        once per alias — otherwise relay/reap/cleanup runs twice on a
        dual-sender primary."""
        import main
        shared_cdb = MagicMock(name="cdb")
        shared_tq = MagicMock(name="tq")
        shared_wm = MagicMock(name="wm")
        universe = MagicMock(name="universe")
        duplicated = (universe, shared_cdb, shared_tq, shared_wm)
        resources = {"bb@x": duplicated, "babak@x": duplicated}
        mocker.patch(
            "main.build_universe_resources", return_value=resources,
        )
        mocker.patch("main.universes_from_config", return_value=[universe])
        tick = mocker.patch("main._tick_housekeeping")
        mock_poller_cls = mocker.patch("main.EmailPoller")
        mock_poller = MagicMock()
        mock_poller.fetch_unseen.return_value = []
        mock_poller_cls.return_value = mock_poller

        # One iteration then exit.
        def fake_sleep(_n):
            main._shutdown = True
        mocker.patch("main.time.sleep", side_effect=fake_sleep)

        config = {
            "imap_host": "h", "imap_port": 993,
            "username": "u", "password": "p",
            "state_file": str(tmp_path / "ids.json"),
            "poll_interval": 1,
            "authorized_sender": "bb@x",
            "authorized_senders": ["bb@x", "babak@x"],
        }
        original = main._shutdown
        try:
            main._shutdown = False
            main.run_loop(config)
        finally:
            main._shutdown = original

        # Housekeeping fired once for the shared bundle, not twice.
        assert tick.call_count == 1
        (_, cdb_arg, tq_arg) = tick.call_args.args
        assert cdb_arg is shared_cdb
        assert tq_arg is shared_tq

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
