"""Tests for .claude/agent-name and .codex/agent-name in proc_reconcile.

Split from test_proc_reconcile_environ.py to stay under the 200-line cap.
"""
import pytest

from src.chat_db import ChatDB


@pytest.fixture
def db(tmp_path):
    return ChatDB(str(tmp_path / "bus.db"))


class TestAgentNameFileAttribution:
    def test_claude_agent_name_file_used_when_environ_missing(
        self, db, tmp_path, monkeypatch,
    ):
        from src import proc_reconcile
        from src.proc_reconcile import reconcile_live_agents

        (tmp_path / ".claude").mkdir()
        (tmp_path / ".claude" / "agent-name").write_text("agent-em-backend\n")

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
        assert touched == ["agent-em-backend"]
        assert db.get_agent("agent-em-backend")["pid"] == 4242

    def test_codex_agent_name_file_used_when_no_claude_file(
        self, db, tmp_path, monkeypatch,
    ):
        from src import proc_reconcile
        from src.proc_reconcile import reconcile_live_agents

        (tmp_path / ".codex").mkdir()
        (tmp_path / ".codex" / "agent-name").write_text("agent-codex-backend\n")

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
        assert touched == ["agent-codex-backend"]
        assert db.get_agent("agent-codex-backend")["pid"] == 4242

    def test_claude_agent_name_wins_over_codex(
        self, db, tmp_path, monkeypatch,
    ):
        from src import proc_reconcile
        from src.proc_reconcile import reconcile_live_agents

        (tmp_path / ".claude").mkdir()
        (tmp_path / ".claude" / "agent-name").write_text("agent-em-backend\n")
        (tmp_path / ".codex").mkdir()
        (tmp_path / ".codex" / "agent-name").write_text("agent-codex-backend\n")

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
        assert touched == ["agent-em-backend"]
