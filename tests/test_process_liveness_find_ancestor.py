"""Tests for src.process_liveness.find_ancestor_pid_matching."""


class TestFindAncestorPidMatching:
    """Matching uses argv[0]'s basename (exact equality), not substring
    search across the whole cmdline — otherwise a shell wrapper whose
    script path happens to contain the marker (e.g. a hook wrapper under
    .../claude-email/scripts/) false-matches before the walker reaches
    the real CLI."""

    def test_finds_first_matching_ancestor(self, monkeypatch):
        import src.process_liveness as pl
        monkeypatch.setattr(pl.os, "getpid", lambda: 100)
        chain = {100: 200, 200: 300, 300: 1}
        monkeypatch.setattr(pl, "_get_ppid", lambda pid: chain.get(pid))
        cmdlines = {
            200: "/bin/sh\x00hook.sh",
            300: "/usr/local/bin/claude\x00--print",
        }
        monkeypatch.setattr(pl, "_read_cmdline", lambda pid: cmdlines.get(pid, ""))
        assert pl.find_ancestor_pid_matching("claude") == 300

    def test_ignores_substring_match_in_ancestor_path(self, monkeypatch):
        """Regression: the shell SessionStart hook wrapper lives under
        .../claude-email/scripts/chat-session-start-hook.sh — its cmdline
        contains 'claude' as part of the path. A substring matcher would
        return the wrapper's short-lived PID and leave the dashboard
        blank once the hook exits. Basename-eq walks past it to the real
        CLI ancestor."""
        import src.process_liveness as pl
        monkeypatch.setattr(pl.os, "getpid", lambda: 100)
        chain = {100: 200, 200: 300, 300: 1}
        monkeypatch.setattr(pl, "_get_ppid", lambda pid: chain.get(pid))
        cmdlines = {
            200: "/bin/sh\x00/home/me/claude-email/scripts/chat-session-start-hook.sh",
            300: "/usr/local/bin/claude\x00--dangerously-skip-permissions",
        }
        monkeypatch.setattr(pl, "_read_cmdline", lambda pid: cmdlines.get(pid, ""))
        assert pl.find_ancestor_pid_matching("claude") == 300

    def test_matches_bare_argv0_without_path_prefix(self, monkeypatch):
        """When claude is invoked via PATH, argv[0] is just 'claude' with
        no directory component — basename-eq must still match."""
        import src.process_liveness as pl
        monkeypatch.setattr(pl.os, "getpid", lambda: 100)
        chain = {100: 200, 200: 1}
        monkeypatch.setattr(pl, "_get_ppid", lambda pid: chain.get(pid))
        monkeypatch.setattr(
            pl, "_read_cmdline",
            lambda pid: "claude\x00--dangerously-skip-permissions\x00--continue",
        )
        assert pl.find_ancestor_pid_matching("claude") == 200

    def test_none_when_no_ancestor_matches(self, monkeypatch):
        import src.process_liveness as pl
        monkeypatch.setattr(pl.os, "getpid", lambda: 100)
        chain = {100: 200, 200: 1}
        monkeypatch.setattr(pl, "_get_ppid", lambda pid: chain.get(pid))
        monkeypatch.setattr(pl, "_read_cmdline", lambda pid: "/bin/bash")
        assert pl.find_ancestor_pid_matching("claude") is None

    def test_none_when_proc_unreadable(self, monkeypatch):
        import src.process_liveness as pl
        monkeypatch.setattr(pl.os, "getpid", lambda: 100)
        monkeypatch.setattr(pl, "_get_ppid", lambda pid: None)
        assert pl.find_ancestor_pid_matching("claude") is None

    def test_skips_ancestors_with_empty_cmdline(self, monkeypatch):
        """Kernel threads and early-boot processes return an empty
        /proc/<pid>/cmdline. The walker must step past them without
        crashing and keep looking for a real ancestor."""
        import src.process_liveness as pl
        monkeypatch.setattr(pl.os, "getpid", lambda: 100)
        chain = {100: 200, 200: 300, 300: 1}
        monkeypatch.setattr(pl, "_get_ppid", lambda pid: chain.get(pid))
        cmdlines = {200: "", 300: "claude\x00--print"}
        monkeypatch.setattr(pl, "_read_cmdline", lambda pid: cmdlines.get(pid, ""))
        assert pl.find_ancestor_pid_matching("claude") == 300
