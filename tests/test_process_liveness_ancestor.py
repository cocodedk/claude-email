"""Tests for src.process_liveness.is_ancestor_or_self."""
import os


class TestIsAncestorOrSelf:
    """PPID-chain walker used by hook scripts for ownership checks."""

    def test_self_pid_is_match(self):
        from src.process_liveness import is_ancestor_or_self
        assert is_ancestor_or_self(os.getpid()) is True

    def test_rejects_non_positive(self):
        from src.process_liveness import is_ancestor_or_self
        assert is_ancestor_or_self(0) is False
        assert is_ancestor_or_self(-1) is False

    def test_matches_on_first_ancestor(self, monkeypatch):
        import src.process_liveness as pl
        monkeypatch.setattr(pl.os, "getpid", lambda: 100)
        chain = {100: 200, 200: 300, 300: 1}
        monkeypatch.setattr(pl, "_get_ppid", lambda pid: chain.get(pid))
        assert pl.is_ancestor_or_self(300) is True

    def test_returns_false_when_target_outside_chain(self, monkeypatch):
        import src.process_liveness as pl
        monkeypatch.setattr(pl.os, "getpid", lambda: 100)
        chain = {100: 200, 200: 1}
        monkeypatch.setattr(pl, "_get_ppid", lambda pid: chain.get(pid))
        # Sibling session PID (say 999) is live but not in our ancestry.
        assert pl.is_ancestor_or_self(999) is False

    def test_stops_at_init(self, monkeypatch):
        import src.process_liveness as pl
        monkeypatch.setattr(pl.os, "getpid", lambda: 100)
        monkeypatch.setattr(pl, "_get_ppid", lambda pid: 1)
        assert pl.is_ancestor_or_self(42) is False

    def test_missing_proc_entry_returns_false(self, monkeypatch):
        import src.process_liveness as pl
        monkeypatch.setattr(pl.os, "getpid", lambda: 100)
        monkeypatch.setattr(pl, "_get_ppid", lambda pid: None)
        assert pl.is_ancestor_or_self(42) is False
