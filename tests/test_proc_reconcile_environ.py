"""Tests for CLAUDE_AGENT_NAME attribution in proc_reconcile.

Split from test_proc_reconcile.py so that file stays under the 200-line
cap. Covers _read_agent_name_from_environ and the reconcile_live_agents
integration that reads CLAUDE_AGENT_NAME from /proc/<pid>/environ.
"""
import pytest

from src.chat_db import ChatDB


@pytest.fixture
def db(tmp_path):
    return ChatDB(str(tmp_path / "bus.db"))


class TestReadAgentNameFromEnviron:
    """The environ-parsing helper handles real /proc data shape."""

    def test_parses_env_var(self, tmp_path, monkeypatch):
        from src.proc_reconcile import _read_agent_name_from_environ

        environ_data = b"PATH=/usr/bin\x00CLAUDE_AGENT_NAME=agent-foo\x00HOME=/h\x00"
        env_path = tmp_path / "environ"
        env_path.write_bytes(environ_data)

        real_open = open

        def fake_open(p, mode="r"):
            return real_open(env_path, mode) if "environ" in str(p) else real_open(p, mode)

        monkeypatch.setattr("builtins.open", fake_open)
        assert _read_agent_name_from_environ(4242) == "agent-foo"

    def test_returns_none_when_var_missing(self, tmp_path, monkeypatch):
        from src.proc_reconcile import _read_agent_name_from_environ

        environ_data = b"PATH=/usr/bin\x00HOME=/h\x00"
        env_path = tmp_path / "environ"
        env_path.write_bytes(environ_data)
        real_open = open

        def fake_open(p, mode="r"):
            return real_open(env_path, mode) if "environ" in str(p) else real_open(p, mode)

        monkeypatch.setattr("builtins.open", fake_open)
        assert _read_agent_name_from_environ(4242) is None

    def test_returns_none_when_proc_missing(self):
        from src.proc_reconcile import _read_agent_name_from_environ
        # PID that won't have a /proc entry — exercises the FileNotFoundError path.
        assert _read_agent_name_from_environ(99_999_999) is None

    def test_returns_none_on_non_utf8_value(self, tmp_path, monkeypatch):
        from src.proc_reconcile import _read_agent_name_from_environ

        environ_data = b"CLAUDE_AGENT_NAME=\xff\xfe\x00OTHER=ok\x00"
        env_path = tmp_path / "environ"
        env_path.write_bytes(environ_data)
        real_open = open

        def fake_open(p, mode="r"):
            return real_open(env_path, mode) if "environ" in str(p) else real_open(p, mode)

        monkeypatch.setattr("builtins.open", fake_open)
        assert _read_agent_name_from_environ(4242) is None


class TestEnvironNameAttribution:
    """reconcile_live_agents reads CLAUDE_AGENT_NAME from /proc/<pid>/environ."""

    def test_environ_name_overrides_basename(self, db, tmp_path, monkeypatch):
        from src import proc_reconcile
        from src.proc_reconcile import reconcile_live_agents

        monkeypatch.setattr(
            proc_reconcile, "_iter_claude_pids", lambda marker=None: [4242],
        )
        monkeypatch.setattr(
            proc_reconcile, "_cwd_of", lambda pid: str(tmp_path),
        )
        monkeypatch.setattr(
            proc_reconcile, "_read_agent_name_from_environ",
            lambda pid: "agent-custom",
        )

        touched = reconcile_live_agents(db)
        assert touched == ["agent-custom"]
        row = db.get_agent("agent-custom")
        assert row is not None
        assert row["pid"] == 4242

    def test_missing_environ_falls_back_to_basename(self, db, tmp_path, monkeypatch):
        from src import proc_reconcile
        from src.proc_reconcile import reconcile_live_agents

        monkeypatch.setattr(
            proc_reconcile, "_iter_claude_pids", lambda marker=None: [4242],
        )
        monkeypatch.setattr(
            proc_reconcile, "_cwd_of", lambda pid: str(tmp_path),
        )
        monkeypatch.setattr(
            proc_reconcile, "_read_agent_name_from_environ", lambda pid: None,
        )

        touched = reconcile_live_agents(db)
        expected = f"agent-{tmp_path.name}"
        assert touched == [expected]
        assert db.get_agent(expected)["pid"] == 4242

    def test_invalid_environ_value_falls_back_to_basename(
        self, db, tmp_path, monkeypatch,
    ):
        from src import proc_reconcile
        from src.proc_reconcile import reconcile_live_agents

        monkeypatch.setattr(
            proc_reconcile, "_iter_claude_pids", lambda marker=None: [4242],
        )
        monkeypatch.setattr(
            proc_reconcile, "_cwd_of", lambda pid: str(tmp_path),
        )
        monkeypatch.setattr(
            proc_reconcile, "_read_agent_name_from_environ",
            lambda pid: "Not Valid",
        )

        touched = reconcile_live_agents(db)
        expected = f"agent-{tmp_path.name}"
        assert touched == [expected]
