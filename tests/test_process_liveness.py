"""Tests for src.process_liveness.is_alive."""
import os
import subprocess
import time

import pytest
from src.process_liveness import is_alive


def test_is_alive_true_for_live_child():
    child = subprocess.Popen(["sleep", "2"])
    try:
        assert is_alive(child.pid) is True
    finally:
        child.kill()
        child.wait()


def test_is_alive_false_for_nonexistent_pid():
    # PIDs are 22-bit on Linux by default; 99_999_999 is safely unused.
    assert is_alive(99_999_999) is False


def test_is_alive_reaps_zombie_and_returns_false():
    child = subprocess.Popen(["true"])
    pid = child.pid
    for _ in range(100):
        time.sleep(0.01)
        try:
            state = open(f"/proc/{pid}/stat").read().split()[2]
        except FileNotFoundError:
            pytest.fail("child disappeared before we could observe zombie state")
        if state == "Z":
            break
    else:
        pytest.fail("child never became a zombie")
    assert is_alive(pid) is False
    assert not os.path.exists(f"/proc/{pid}"), "zombie not reaped"


@pytest.mark.parametrize("bad_pid", [0, -1, -99])
def test_is_alive_rejects_non_positive_pids(bad_pid):
    """Guard against waitpid/kill's special semantics for pid<=0.

    pid=0 means "any child in this process group"; negative pids address
    process groups. If the DB ever stored a corrupted 0 or negative pid,
    a naive probe could inadvertently reap or signal unrelated processes.
    """
    assert is_alive(bad_pid) is False


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


class TestFindAncestorPidMatching:
    def test_finds_first_matching_ancestor(self, monkeypatch):
        import src.process_liveness as pl
        monkeypatch.setattr(pl.os, "getpid", lambda: 100)
        chain = {100: 200, 200: 300, 300: 1}
        monkeypatch.setattr(pl, "_get_ppid", lambda pid: chain.get(pid))
        cmdlines = {200: "/bin/sh hook.sh", 300: "/usr/local/bin/claude --print"}
        monkeypatch.setattr(pl, "_read_cmdline", lambda pid: cmdlines.get(pid, ""))
        assert pl.find_ancestor_pid_matching("bin/claude") == 300

    def test_none_when_no_ancestor_matches(self, monkeypatch):
        import src.process_liveness as pl
        monkeypatch.setattr(pl.os, "getpid", lambda: 100)
        chain = {100: 200, 200: 1}
        monkeypatch.setattr(pl, "_get_ppid", lambda pid: chain.get(pid))
        monkeypatch.setattr(pl, "_read_cmdline", lambda pid: "/bin/bash")
        assert pl.find_ancestor_pid_matching("bin/claude") is None

    def test_none_when_proc_unreadable(self, monkeypatch):
        import src.process_liveness as pl
        monkeypatch.setattr(pl.os, "getpid", lambda: 100)
        monkeypatch.setattr(pl, "_get_ppid", lambda pid: None)
        assert pl.find_ancestor_pid_matching("bin/claude") is None


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
        assert pl.find_ancestor_pid_matching("bin/claude") is None
