"""Tests for src.process_liveness /proc readers and PPID walker depth guard."""
import os


class TestProcReaders:
    """Thin /proc readers — contract test against the real filesystem
    using our own process."""

    def test_get_ppid_returns_int_for_self(self):
        from src.process_liveness import _get_ppid
        assert _get_ppid(os.getpid()) == os.getppid()

    def test_get_ppid_returns_none_for_missing_pid(self):
        from src.process_liveness import _get_ppid
        assert _get_ppid(99_999_999) is None

    def test_read_cmdline_returns_text_for_self(self):
        from src.process_liveness import _read_cmdline
        out = _read_cmdline(os.getpid())
        assert out  # non-empty for a live process

    def test_read_cmdline_returns_empty_for_missing_pid(self):
        from src.process_liveness import _read_cmdline
        assert _read_cmdline(99_999_999) == ""

    def test_get_ppid_returns_none_when_status_lacks_ppid_line(self, monkeypatch):
        """Defensive fallthrough — a /proc/<pid>/status without a PPid:
        line (shouldn't happen in practice, but we don't trust /proc
        blindly)."""
        import builtins
        import src.process_liveness as pl
        from io import StringIO
        original = builtins.open
        def fake_open(path, *a, **k):
            if "/proc/" in str(path) and str(path).endswith("/status"):
                return StringIO("Name: foo\nState: S\n")
            return original(path, *a, **k)
        monkeypatch.setattr(builtins, "open", fake_open)
        assert pl._get_ppid(12345) is None


class TestPpidWalkerMaxDepth:
    """Defensive guard: the PPID walkers stop after a bounded number of
    hops even if /proc feeds a cyclic chain (paranoid /proc corruption)."""

    def test_is_ancestor_or_self_stops_after_max_depth(self, monkeypatch):
        import src.process_liveness as pl
        monkeypatch.setattr(pl.os, "getpid", lambda: 100)
        monkeypatch.setattr(pl, "_get_ppid", lambda pid: pid + 1)
        assert pl.is_ancestor_or_self(9_999_999) is False

    def test_find_ancestor_pid_matching_stops_after_max_depth(self, monkeypatch):
        import src.process_liveness as pl
        monkeypatch.setattr(pl.os, "getpid", lambda: 100)
        monkeypatch.setattr(pl, "_get_ppid", lambda pid: pid + 1)
        monkeypatch.setattr(pl, "_read_cmdline", lambda pid: "/bin/bash")
        assert pl.find_ancestor_pid_matching("claude") is None
