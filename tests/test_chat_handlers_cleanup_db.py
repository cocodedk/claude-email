"""Tests for src/chat_handlers.py — maybe_cleanup_db exception branch."""
import pytest
from unittest.mock import MagicMock, patch


class TestMaybeCleanupDbExceptionBranch:
    """maybe_cleanup_db (now in src.chat_relay) swallows cleanup_old errors."""

    def test_exception_is_caught_and_logged(self, mocker):
        """cleanup_old raising must be caught; logger.exception must be called."""
        import src.chat_relay as module

        # Reset the cleanup timer so the cleanup actually runs
        original_ts = module._last_cleanup_ts
        module._last_cleanup_ts = 0.0

        try:
            mock_db = MagicMock()
            mock_db.cleanup_old.side_effect = RuntimeError("db error")
            mock_log = mocker.patch("src.chat_relay.logger")

            from src.chat_handlers import maybe_cleanup_db  # still re-exported
            maybe_cleanup_db(mock_db)  # must not raise

            mock_log.exception.assert_called_once()
        finally:
            module._last_cleanup_ts = original_ts
