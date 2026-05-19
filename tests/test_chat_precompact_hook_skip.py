"""Skip-path tests for scripts/chat-precompact-hook.py.

Sub-agents and live siblings must not log heartbeat events; mirrors the
discriminators in chat-drain-inbox.py.
"""
import os

from src.chat_db import ChatDB

from tests._chat_precompact_hook_helpers import _set_stdin, precompact_mod  # noqa: F401


class TestSkipPaths:
    """Sub-agents and live siblings must NOT log events for this session.

    Mirrors the discriminators in chat-drain-inbox.py — the same logic
    applies here because both hooks consume from the same caller identity."""

    def test_skips_when_subagent_id_present(
        self, precompact_mod, tmp_path, monkeypatch, capsys,
    ):
        db_file = tmp_path / "bus.db"
        db = ChatDB(str(db_file))
        project = tmp_path / "delta"
        project.mkdir()
        monkeypatch.chdir(project)
        monkeypatch.setenv("CHAT_DB_PATH", str(db_file))
        _set_stdin(monkeypatch, {
            "hook_event_name": "PreCompact",
            "trigger": "manual",
            "agent_id": "sub-1",
        })
        rc = precompact_mod.main()
        capsys.readouterr()
        assert rc == 0
        assert db.get_flow_events_since(0) == []

    def test_skips_when_live_sibling_owns_name(
        self, precompact_mod, tmp_path, monkeypatch, capsys,
    ):
        db_file = tmp_path / "bus.db"
        db = ChatDB(str(db_file))
        project = tmp_path / "epsilon"
        project.mkdir()
        master_pid = os.getpid()
        db.register_agent("agent-epsilon", str(project), pid=master_pid)
        monkeypatch.chdir(project)
        monkeypatch.setenv("CHAT_DB_PATH", str(db_file))
        # Hook helper pretends to be a sibling — different pid than master.
        monkeypatch.setattr(
            precompact_mod.os, "getpid", lambda: master_pid + 1,
        )
        _set_stdin(monkeypatch, {
            "hook_event_name": "PreCompact",
            "trigger": "manual",
        })
        rc = precompact_mod.main()
        capsys.readouterr()
        assert rc == 0
        assert db.get_flow_events_since(0) == []
