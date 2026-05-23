"""Tests for src.hook_utils — shared helpers for hook scripts."""
import io
import json
import os
import sys
from pathlib import Path

import pytest


class TestResolvedDbPath:
    def test_absolute_path_returned_as_is(self, monkeypatch, tmp_path):
        from src.hook_utils import resolved_db_path
        p = tmp_path / "bus.db"
        monkeypatch.setenv("CHAT_DB_PATH", str(p))
        assert resolved_db_path(tmp_path) == p

    def test_relative_path_resolved_against_root(self, monkeypatch, tmp_path):
        from src.hook_utils import resolved_db_path
        monkeypatch.setenv("CHAT_DB_PATH", "bus.db")
        assert resolved_db_path(tmp_path) == tmp_path / "bus.db"

    def test_raises_when_env_not_set(self, monkeypatch, tmp_path):
        from src.hook_utils import resolved_db_path
        monkeypatch.delenv("CHAT_DB_PATH", raising=False)
        with pytest.raises(RuntimeError, match="CHAT_DB_PATH"):
            resolved_db_path(tmp_path)


class TestCallerName:
    def test_uses_env_var_when_set(self, monkeypatch, tmp_path):
        from src.hook_utils import caller_name
        monkeypatch.setenv("CLAUDE_AGENT_NAME", "agent-foo")
        monkeypatch.chdir(tmp_path)
        assert caller_name() == "agent-foo"

    def test_falls_back_to_cwd_basename(self, monkeypatch, tmp_path):
        from src.hook_utils import caller_name
        monkeypatch.delenv("CLAUDE_AGENT_NAME", raising=False)
        project = tmp_path / "myproject"
        project.mkdir()
        monkeypatch.chdir(project)
        assert caller_name() == "agent-myproject"

    def test_reads_agent_name_file_when_env_unset(self, monkeypatch, tmp_path):
        from src.hook_utils import caller_name
        monkeypatch.delenv("CLAUDE_AGENT_NAME", raising=False)
        project = tmp_path / "earn-money-backend"
        project.mkdir()
        (project / ".claude").mkdir()
        (project / ".claude" / "agent-name").write_text("agent-em-backend\n")
        monkeypatch.chdir(project)
        assert caller_name() == "agent-em-backend"

    def test_env_var_wins_over_agent_name_file(self, monkeypatch, tmp_path):
        from src.hook_utils import caller_name
        monkeypatch.setenv("CLAUDE_AGENT_NAME", "agent-from-env")
        project = tmp_path / "proj"
        project.mkdir()
        (project / ".claude").mkdir()
        (project / ".claude" / "agent-name").write_text("agent-from-file\n")
        monkeypatch.chdir(project)
        assert caller_name() == "agent-from-env"

    def test_invalid_agent_name_file_falls_back_to_cwd(self, monkeypatch, tmp_path):
        from src.hook_utils import caller_name
        monkeypatch.delenv("CLAUDE_AGENT_NAME", raising=False)
        project = tmp_path / "fallback"
        project.mkdir()
        (project / ".claude").mkdir()
        (project / ".claude" / "agent-name").write_text("Not Valid\n")
        monkeypatch.chdir(project)
        assert caller_name() == "agent-fallback"

    def test_empty_agent_name_file_falls_back_to_cwd(self, monkeypatch, tmp_path):
        from src.hook_utils import caller_name
        monkeypatch.delenv("CLAUDE_AGENT_NAME", raising=False)
        project = tmp_path / "fallback2"
        project.mkdir()
        (project / ".claude").mkdir()
        (project / ".claude" / "agent-name").write_text("")
        monkeypatch.chdir(project)
        assert caller_name() == "agent-fallback2"


class TestReadHookPayload:
    def test_parses_json_from_stdin(self, monkeypatch):
        from src.hook_utils import read_hook_payload
        monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps({"key": "val"})))
        assert read_hook_payload() == {"key": "val"}

    def test_returns_empty_on_tty(self, monkeypatch):
        from src.hook_utils import read_hook_payload
        class FakeTty:
            def isatty(self): return True
            def read(self): return ""
        monkeypatch.setattr(sys, "stdin", FakeTty())
        assert read_hook_payload() == {}

    def test_returns_empty_on_invalid_json(self, monkeypatch):
        from src.hook_utils import read_hook_payload
        monkeypatch.setattr(sys, "stdin", io.StringIO("not-json"))
        assert read_hook_payload() == {}

    def test_returns_empty_on_empty_stdin(self, monkeypatch):
        from src.hook_utils import read_hook_payload
        monkeypatch.setattr(sys, "stdin", io.StringIO(""))
        assert read_hook_payload() == {}
