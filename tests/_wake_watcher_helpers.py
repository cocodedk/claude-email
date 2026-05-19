"""Shared fixtures/helpers for wake_watcher test split.

Underscore prefix makes pytest skip this file for collection.
"""
import os
import tempfile

import pytest

from src.chat_db import ChatDB
from src.wake_watcher import WakeWatcherConfig


@pytest.fixture
def live_db():
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    db = ChatDB(path)
    try:
        yield db
    finally:
        os.unlink(path)


def _cfg(**over):
    base = dict(
        interval_secs=0.05, timeout_secs=5,
        idle_expiry_secs=900, max_failures=3, rate_limit_secs=3600,
        claude_bin="claude", prompt="drain", user_avatar="user",
    )
    base.update(over)
    return WakeWatcherConfig(**base)
