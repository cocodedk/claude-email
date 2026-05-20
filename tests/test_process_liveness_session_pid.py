"""Tests for src.process_liveness.find_session_pid_for_cwd."""
import json
import subprocess

import pytest


class TestFindSessionPidForCwd:

    def _run(self, monkeypatch, sessions: list, cwd: str, ancestor_pids: set | None = None):
        import src.process_liveness as pl
        monkeypatch.setattr(
            pl.subprocess, "run",
            lambda cmd, **kw: type(
                "R", (), {"stdout": json.dumps(sessions), "returncode": 0}
            )(),
        )
        if ancestor_pids is None:
            monkeypatch.setattr(pl, "is_ancestor_or_self", lambda pid: True)
        else:
            monkeypatch.setattr(pl, "is_ancestor_or_self", lambda pid: pid in ancestor_pids)
        return pl.find_session_pid_for_cwd(cwd)

    def test_matches_exact_cwd(self, monkeypatch, tmp_path):
        sessions = [{"pid": 42, "cwd": str(tmp_path), "startedAt": 1000}]
        assert self._run(monkeypatch, sessions, str(tmp_path)) == 42

    def test_returns_none_when_no_cwd_match(self, monkeypatch, tmp_path):
        sessions = [{"pid": 99, "cwd": str(tmp_path / "other"), "startedAt": 1000}]
        assert self._run(monkeypatch, sessions, str(tmp_path)) is None

    def test_picks_highest_started_at_on_multiple_matches(self, monkeypatch, tmp_path):
        sessions = [
            {"pid": 10, "cwd": str(tmp_path), "startedAt": 500},
            {"pid": 20, "cwd": str(tmp_path), "startedAt": 1500},
            {"pid": 15, "cwd": str(tmp_path), "startedAt": 1000},
        ]
        assert self._run(monkeypatch, sessions, str(tmp_path), ancestor_pids={10, 20, 15}) == 20

    def test_returns_none_when_no_ancestor_match(self, monkeypatch, tmp_path):
        sessions = [{"pid": 42, "cwd": str(tmp_path), "startedAt": 1000}]
        assert self._run(monkeypatch, sessions, str(tmp_path), ancestor_pids=set()) is None

    def test_returns_none_on_non_positive_pid(self, monkeypatch, tmp_path):
        sessions = [{"pid": 0, "cwd": str(tmp_path), "startedAt": 1000}]
        assert self._run(monkeypatch, sessions, str(tmp_path)) is None

    def test_returns_none_when_cwd_field_missing(self, monkeypatch, tmp_path):
        sessions = [{"pid": 42, "startedAt": 1000}]
        assert self._run(monkeypatch, sessions, str(tmp_path)) is None

    def test_returns_none_when_cwd_field_empty(self, monkeypatch, tmp_path):
        sessions = [{"pid": 42, "cwd": "", "startedAt": 1000}]
        assert self._run(monkeypatch, sessions, str(tmp_path)) is None

    def test_returns_none_on_subprocess_exception(self, monkeypatch, tmp_path):
        import src.process_liveness as pl
        monkeypatch.setattr(
            pl.subprocess, "run",
            lambda *a, **kw: (_ for _ in ()).throw(FileNotFoundError("no claude")),
        )
        assert pl.find_session_pid_for_cwd(str(tmp_path)) is None

    def test_returns_none_on_json_decode_error(self, monkeypatch, tmp_path):
        import src.process_liveness as pl
        monkeypatch.setattr(
            pl.subprocess, "run",
            lambda *a, **kw: type("R", (), {"stdout": "not-json", "returncode": 0})(),
        )
        assert pl.find_session_pid_for_cwd(str(tmp_path)) is None

    def test_uses_custom_claude_bin(self, monkeypatch, tmp_path):
        import src.process_liveness as pl
        captured = {}
        sessions = [{"pid": 77, "cwd": str(tmp_path), "startedAt": 1000}]
        def fake_run(cmd, **kw):
            captured["cmd"] = cmd
            return type("R", (), {"stdout": json.dumps(sessions)})()
        monkeypatch.setattr(pl.subprocess, "run", fake_run)
        monkeypatch.setattr(pl, "is_ancestor_or_self", lambda pid: True)
        pl.find_session_pid_for_cwd(str(tmp_path), claude_bin="/opt/bin/claude")
        assert captured["cmd"][0] == "/opt/bin/claude"

    def test_resolves_cwd_symlinks(self, monkeypatch, tmp_path):
        real = tmp_path / "real"
        real.mkdir()
        link = tmp_path / "link"
        link.symlink_to(real)
        sessions = [{"pid": 55, "cwd": str(real), "startedAt": 1000}]
        assert self._run(monkeypatch, sessions, str(link)) == 55
