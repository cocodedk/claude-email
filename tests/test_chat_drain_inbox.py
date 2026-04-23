"""Tests for scripts/chat-drain-inbox.py — hook that drains agent inbox.

Mirrors the loader pattern of test_chat_register_self.py because the script
lives under scripts/ and is invoked directly by Claude Code hooks.
"""
import importlib.util
import io
import json
import os
import sys
from pathlib import Path

import pytest

from src.chat_db import ChatDB

_REPO_ROOT = Path(__file__).resolve().parent.parent
_SCRIPT_PATH = _REPO_ROOT / "scripts" / "chat-drain-inbox.py"


@pytest.fixture
def drain_mod(monkeypatch):
    for key in ("CHAT_DB_PATH",):
        monkeypatch.delenv(key, raising=False)
    spec = importlib.util.spec_from_file_location("chat_drain_inbox", _SCRIPT_PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class TestReadHookEvent:
    def test_isatty_defaults_to_user_prompt(self, drain_mod, monkeypatch):
        class FakeStdin:
            def isatty(self):
                return True
        monkeypatch.setattr(sys, "stdin", FakeStdin())
        assert drain_mod._read_hook_event() == "UserPromptSubmit"

    def test_empty_stdin_defaults(self, drain_mod, monkeypatch):
        buf = io.StringIO("")
        monkeypatch.setattr(sys, "stdin", buf)
        assert drain_mod._read_hook_event() == "UserPromptSubmit"

    def test_valid_json_uses_event_name(self, drain_mod, monkeypatch):
        buf = io.StringIO(json.dumps({"hook_event_name": "SessionStart"}))
        monkeypatch.setattr(sys, "stdin", buf)
        assert drain_mod._read_hook_event() == "SessionStart"

    def test_malformed_json_defaults(self, drain_mod, monkeypatch):
        buf = io.StringIO("{not json")
        monkeypatch.setattr(sys, "stdin", buf)
        assert drain_mod._read_hook_event() == "UserPromptSubmit"

    def test_json_without_event_name_defaults(self, drain_mod, monkeypatch):
        buf = io.StringIO("{}")
        monkeypatch.setattr(sys, "stdin", buf)
        assert drain_mod._read_hook_event() == "UserPromptSubmit"

    def test_read_hook_payload_swallows_stdin_errors(
        self, drain_mod, monkeypatch,
    ):
        """Broken stdin (OSError) must not crash — return {}."""
        class _Broken:
            def isatty(self):
                raise OSError("stdin gone")
            def read(self):
                return ""
        monkeypatch.setattr(sys, "stdin", _Broken())
        assert drain_mod._read_hook_payload() == {}


class TestResolvedDbPath:
    def test_relative_resolves_against_repo_root(self, drain_mod, monkeypatch):
        monkeypatch.setenv("CHAT_DB_PATH", "claude-chat.db")
        assert drain_mod._resolved_db_path() == _REPO_ROOT / "claude-chat.db"

    def test_absolute_returned_as_is(self, drain_mod, monkeypatch, tmp_path):
        abs_db = tmp_path / "chat.db"
        monkeypatch.setenv("CHAT_DB_PATH", str(abs_db))
        assert drain_mod._resolved_db_path() == abs_db

    def test_missing_env_raises(self, drain_mod, monkeypatch):
        monkeypatch.delenv("CHAT_DB_PATH", raising=False)
        with pytest.raises(RuntimeError, match="CHAT_DB_PATH"):
            drain_mod._resolved_db_path()


class TestFormatContext:
    def test_single_message(self, drain_mod):
        ctx = drain_mod._format_context("agent-foo", [
            {"id": 1, "from_name": "user", "created_at": "2026-04-19T08:00:00+00:00", "body": "hello"},
        ])
        assert "INBOX" in ctx
        assert "do NOT call" in ctx
        assert "[msg #1]" in ctx
        assert "from=user" in ctx
        assert "hello" in ctx
        assert 'agent-foo' in ctx

    def test_multiple_messages(self, drain_mod):
        msgs = [
            {"id": 1, "from_name": "user", "created_at": "t1", "body": "a"},
            {"id": 2, "from_name": "agent-bar", "created_at": "t2", "body": "b"},
        ]
        ctx = drain_mod._format_context("agent-foo", msgs)
        assert "[msg #1]" in ctx and "[msg #2]" in ctx
        assert "from=user" in ctx and "from=agent-bar" in ctx


class TestMain:
    @pytest.fixture(autouse=True)
    def _isolate(self, monkeypatch):
        # Standalone (no stdin) — defaults to UserPromptSubmit event
        class FakeStdin:
            def isatty(self):
                return True
            def read(self):
                return ""
        monkeypatch.setattr(sys, "stdin", FakeStdin())

    def test_empty_inbox_no_stdout(self, drain_mod, tmp_path, monkeypatch, capsys):
        db_file = tmp_path / "bus.db"
        ChatDB(str(db_file))
        project = tmp_path / "proj"
        project.mkdir()
        monkeypatch.chdir(project)
        monkeypatch.setenv("CHAT_DB_PATH", str(db_file))
        rc = drain_mod.main()
        assert rc == 0
        out = capsys.readouterr()
        assert out.out == ""
        assert out.err == ""

    def test_skips_drain_when_subagent_indicated_by_agent_id(
        self, drain_mod, tmp_path, monkeypatch, capsys,
    ):
        """SessionStart/UserPromptSubmit hook input includes agent_id only
        inside a subagent — if present, drain must skip."""
        db_file = tmp_path / "bus.db"
        db = ChatDB(str(db_file))
        project = tmp_path / "sub"
        project.mkdir()
        db.insert_message("user", "agent-sub", "hello", "command")
        monkeypatch.chdir(project)
        monkeypatch.setenv("CHAT_DB_PATH", str(db_file))
        buf = io.StringIO(json.dumps({
            "hook_event_name": "UserPromptSubmit",
            "agent_id": "sub-123",
        }))
        monkeypatch.setattr(sys, "stdin", buf)
        rc = drain_mod.main()
        assert rc == 0
        assert capsys.readouterr().out == ""
        assert len(db.get_pending_messages_for("agent-sub")) == 1

    def test_skips_drain_when_another_live_pid_owns_name(
        self, drain_mod, tmp_path, monkeypatch, capsys,
    ):
        """A sub-agent or sibling session with the same caller name must
        not steal messages from the registered master process."""
        db_file = tmp_path / "bus.db"
        db = ChatDB(str(db_file))
        project = tmp_path / "alpha"
        project.mkdir()
        master_pid = os.getpid()
        db.register_agent("agent-alpha", str(project), pid=master_pid)
        db.insert_message("user", "agent-alpha", "hi there", "command")
        monkeypatch.chdir(project)
        monkeypatch.setenv("CHAT_DB_PATH", str(db_file))
        # Pretend to be a subagent with a different pid than the master
        monkeypatch.setattr(drain_mod.os, "getpid", lambda: master_pid + 1)
        rc = drain_mod.main()
        assert rc == 0
        assert capsys.readouterr().out == ""
        assert len(db.get_pending_messages_for("agent-alpha")) == 1

    def test_drains_when_we_own_the_name(
        self, drain_mod, tmp_path, monkeypatch, capsys,
    ):
        """If the registered pid matches ours, we are the master — drain."""
        db_file = tmp_path / "bus.db"
        db = ChatDB(str(db_file))
        project = tmp_path / "alpha"
        project.mkdir()
        db.register_agent("agent-alpha", str(project), pid=os.getpid())
        db.insert_message("user", "agent-alpha", "hi there", "command")
        monkeypatch.chdir(project)
        monkeypatch.setenv("CHAT_DB_PATH", str(db_file))
        rc = drain_mod.main()
        assert rc == 0
        out = capsys.readouterr().out
        assert "hi there" in out

    def test_drains_when_stored_pid_is_our_ancestor(
        self, drain_mod, tmp_path, monkeypatch, capsys,
    ):
        """Spawned-agent case: agents.pid stores the long-lived Claude
        session PID (written by src/spawner.py), and this hook is its
        descendant. Must drain — an earlier version skipped when
        os.getpid() != stored_pid, breaking hook-based delivery for
        every spawned agent (caught by codex review)."""
        import src.process_liveness as pl
        db_file = tmp_path / "bus.db"
        db = ChatDB(str(db_file))
        project = tmp_path / "spawned"
        project.mkdir()
        fake_hook_pid = 100
        claude_session_pid = 555
        db.register_agent("agent-spawned", str(project), pid=claude_session_pid)
        db.insert_message("user", "agent-spawned", "welcome back", "command")
        monkeypatch.chdir(project)
        monkeypatch.setenv("CHAT_DB_PATH", str(db_file))
        chain = {fake_hook_pid: 200, 200: claude_session_pid, claude_session_pid: 1}
        monkeypatch.setattr(pl, "_get_ppid", lambda pid: chain.get(pid))
        monkeypatch.setattr(pl.os, "getpid", lambda: fake_hook_pid)
        # Stored PID is "alive" — this is the real Claude session.
        monkeypatch.setattr(drain_mod, "is_alive", lambda pid: True)
        rc = drain_mod.main()
        assert rc == 0
        out = capsys.readouterr().out
        assert "welcome back" in out

    def test_drains_pending_and_emits_json(self, drain_mod, tmp_path, monkeypatch, capsys):
        db_file = tmp_path / "bus.db"
        db = ChatDB(str(db_file))
        project = tmp_path / "alpha"
        project.mkdir()
        db.insert_message("user", "agent-alpha", "hi there", "command")
        monkeypatch.chdir(project)
        monkeypatch.setenv("CHAT_DB_PATH", str(db_file))
        rc = drain_mod.main()
        assert rc == 0
        out = capsys.readouterr().out
        payload = json.loads(out)
        assert payload["hookSpecificOutput"]["hookEventName"] == "UserPromptSubmit"
        ctx = payload["hookSpecificOutput"]["additionalContext"]
        assert "hi there" in ctx
        assert "agent-alpha" in ctx
        # Message is now marked delivered in the DB
        remaining = db.get_pending_messages_for("agent-alpha")
        assert remaining == []

    def test_uses_event_name_from_stdin(self, drain_mod, tmp_path, monkeypatch, capsys):
        db_file = tmp_path / "bus.db"
        db = ChatDB(str(db_file))
        project = tmp_path / "beta"
        project.mkdir()
        db.insert_message("user", "agent-beta", "ping", "command")
        monkeypatch.chdir(project)
        monkeypatch.setenv("CHAT_DB_PATH", str(db_file))

        buf = io.StringIO(json.dumps({"hook_event_name": "SessionStart"}))
        monkeypatch.setattr(sys, "stdin", buf)
        rc = drain_mod.main()
        assert rc == 0
        payload = json.loads(capsys.readouterr().out)
        assert payload["hookSpecificOutput"]["hookEventName"] == "SessionStart"

    def test_missing_env_returns_zero_no_stdout(self, drain_mod, tmp_path, monkeypatch, capsys):
        project = tmp_path / "x"
        project.mkdir()
        monkeypatch.chdir(project)
        monkeypatch.delenv("CHAT_DB_PATH", raising=False)
        rc = drain_mod.main()
        assert rc == 0
        out = capsys.readouterr()
        assert out.out == ""
        assert "CHAT_DB_PATH" in out.err

    def test_missing_db_returns_zero_no_stdout(self, drain_mod, tmp_path, monkeypatch, capsys):
        project = tmp_path / "y"
        project.mkdir()
        monkeypatch.chdir(project)
        monkeypatch.setenv("CHAT_DB_PATH", str(tmp_path / "nope.db"))
        rc = drain_mod.main()
        assert rc == 0
        assert capsys.readouterr().out == ""

    def test_corrupt_db_returns_zero_no_stdout(self, drain_mod, tmp_path, monkeypatch, capsys):
        bad = tmp_path / "corrupt.db"
        bad.write_bytes(b"garbage")
        project = tmp_path / "z"
        project.mkdir()
        monkeypatch.chdir(project)
        monkeypatch.setenv("CHAT_DB_PATH", str(bad))
        rc = drain_mod.main()
        assert rc == 0
        assert capsys.readouterr().out == ""

    def test_caller_derived_from_cwd_basename(self, drain_mod, tmp_path, monkeypatch, capsys):
        db_file = tmp_path / "bus.db"
        db = ChatDB(str(db_file))
        project = tmp_path / "my-project"
        project.mkdir()
        db.insert_message("user", "agent-my-project", "ok", "reply")
        monkeypatch.chdir(project)
        monkeypatch.setenv("CHAT_DB_PATH", str(db_file))
        drain_mod.main()
        payload = json.loads(capsys.readouterr().out)
        assert 'agent-my-project' in payload["hookSpecificOutput"]["additionalContext"]

    def test_import_does_not_crash_when_dotenv_missing(self, monkeypatch):
        real_dotenv = sys.modules.pop("dotenv", None)
        monkeypatch.setitem(sys.modules, "dotenv", None)
        try:
            spec = importlib.util.spec_from_file_location("chat_drain_inbox_nodotenv", _SCRIPT_PATH)
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
            assert hasattr(mod, "main")
        finally:
            if real_dotenv is not None:
                sys.modules["dotenv"] = real_dotenv
            else:
                sys.modules.pop("dotenv", None)

    def test_query_failure_returns_zero_no_stdout(self, drain_mod, tmp_path, monkeypatch, mocker, capsys):
        db_file = tmp_path / "bus.db"
        ChatDB(str(db_file))
        project = tmp_path / "q"
        project.mkdir()
        monkeypatch.chdir(project)
        monkeypatch.setenv("CHAT_DB_PATH", str(db_file))
        mocker.patch(
            "src.chat_db.ChatDB.claim_pending_messages_for",
            side_effect=RuntimeError("boom"),
        )
        rc = drain_mod.main()
        assert rc == 0
        err = capsys.readouterr().err
        assert "query failed" in err


class TestStopEvent:
    """Stop hook must emit {decision:block, reason:...} so pending peer
    messages are reinjected before the session idles. Responsiveness is
    the whole point — stop_hook_active is ignored because
    mark_message_delivered is the real loop guard.
    """

    def _run_with_stdin(self, drain_mod, stdin_payload: dict, capsys):
        buf = io.StringIO(json.dumps(stdin_payload))
        import sys as _sys
        # monkeypatch via direct attribute set inside this helper
        orig = _sys.stdin
        _sys.stdin = buf
        try:
            rc = drain_mod.main()
        finally:
            _sys.stdin = orig
        return rc, capsys.readouterr()

    def test_stop_with_pending_emits_decision_block(
        self, drain_mod, tmp_path, monkeypatch, capsys,
    ):
        db_file = tmp_path / "bus.db"
        db = ChatDB(str(db_file))
        project = tmp_path / "gamma"
        project.mkdir()
        db.insert_message("agent-peer", "agent-gamma", "peer ping", "notify")
        monkeypatch.chdir(project)
        monkeypatch.setenv("CHAT_DB_PATH", str(db_file))

        rc, out = self._run_with_stdin(
            drain_mod, {"hook_event_name": "Stop"}, capsys,
        )
        assert rc == 0
        payload = json.loads(out.out)
        assert payload["decision"] == "block"
        assert "peer ping" in payload["reason"]
        assert "agent-peer" in payload["reason"]
        # And messages are marked delivered
        assert db.get_pending_messages_for("agent-gamma") == []

    def test_stop_empty_inbox_is_silent(
        self, drain_mod, tmp_path, monkeypatch, capsys,
    ):
        db_file = tmp_path / "bus.db"
        ChatDB(str(db_file))
        project = tmp_path / "delta"
        project.mkdir()
        monkeypatch.chdir(project)
        monkeypatch.setenv("CHAT_DB_PATH", str(db_file))

        rc, out = self._run_with_stdin(
            drain_mod, {"hook_event_name": "Stop"}, capsys,
        )
        assert rc == 0
        assert out.out == ""

    def test_stop_blocks_even_when_stop_hook_active(
        self, drain_mod, tmp_path, monkeypatch, capsys,
    ):
        # Design decision: peer responsiveness matters more than the
        # default stop_hook_active guard. The loop terminates naturally
        # once mark_message_delivered drains the inbox, so we re-block
        # to keep the agent conversant.
        db_file = tmp_path / "bus.db"
        db = ChatDB(str(db_file))
        project = tmp_path / "epsilon"
        project.mkdir()
        db.insert_message("agent-peer", "agent-epsilon", "still here", "notify")
        monkeypatch.chdir(project)
        monkeypatch.setenv("CHAT_DB_PATH", str(db_file))

        rc, out = self._run_with_stdin(
            drain_mod,
            {"hook_event_name": "Stop", "stop_hook_active": True},
            capsys,
        )
        assert rc == 0
        payload = json.loads(out.out)
        assert payload["decision"] == "block"
        assert "still here" in payload["reason"]


class TestFlowEventEmission:
    """The dashboard's flow panel depends on hook_drain_stop /
    hook_drain_session being written to the events table whenever the
    drain actually delivers something. Quiet turns must NOT emit an
    event — we don't want to animate nothing."""

    def _run_with_stdin(self, drain_mod, stdin_payload: dict):
        import sys as _sys
        buf = io.StringIO(json.dumps(stdin_payload))
        orig = _sys.stdin
        _sys.stdin = buf
        try:
            return drain_mod.main()
        finally:
            _sys.stdin = orig

    def test_stop_drain_emits_hook_drain_stop(
        self, drain_mod, tmp_path, monkeypatch, capsys,
    ):
        db_file = tmp_path / "bus.db"
        db = ChatDB(str(db_file))
        project = tmp_path / "kappa"
        project.mkdir()
        db.insert_message("peer", "agent-kappa", "fire", "notify")
        monkeypatch.chdir(project)
        monkeypatch.setenv("CHAT_DB_PATH", str(db_file))
        self._run_with_stdin(drain_mod, {"hook_event_name": "Stop"})
        capsys.readouterr()
        types = [r["event_type"] for r in db.get_flow_events_since(0)]
        assert types == ["hook_drain_stop"]

    def test_session_drain_emits_hook_drain_session(
        self, drain_mod, tmp_path, monkeypatch, capsys,
    ):
        db_file = tmp_path / "bus.db"
        db = ChatDB(str(db_file))
        project = tmp_path / "lambda"
        project.mkdir()
        db.insert_message("peer", "agent-lambda", "fire", "notify")
        monkeypatch.chdir(project)
        monkeypatch.setenv("CHAT_DB_PATH", str(db_file))
        self._run_with_stdin(
            drain_mod, {"hook_event_name": "SessionStart"},
        )
        capsys.readouterr()
        types = [r["event_type"] for r in db.get_flow_events_since(0)]
        assert types == ["hook_drain_session"]

    def test_empty_inbox_does_not_emit(
        self, drain_mod, tmp_path, monkeypatch, capsys,
    ):
        db_file = tmp_path / "bus.db"
        db = ChatDB(str(db_file))
        project = tmp_path / "mu"
        project.mkdir()
        monkeypatch.chdir(project)
        monkeypatch.setenv("CHAT_DB_PATH", str(db_file))
        self._run_with_stdin(drain_mod, {"hook_event_name": "Stop"})
        capsys.readouterr()
        assert db.get_flow_events_since(0) == []

    def test_telemetry_failure_does_not_break_drain(
        self, drain_mod, tmp_path, monkeypatch, capsys, mocker,
    ):
        """Never block the session on telemetry — if _log_event raises,
        the drained payload still lands on stdout."""
        db_file = tmp_path / "bus.db"
        db = ChatDB(str(db_file))
        project = tmp_path / "nu"
        project.mkdir()
        db.insert_message("peer", "agent-nu", "fire", "notify")
        monkeypatch.chdir(project)
        monkeypatch.setenv("CHAT_DB_PATH", str(db_file))
        mocker.patch(
            "src.chat_db.ChatDB._log_event",
            side_effect=RuntimeError("events table is sulking"),
        )
        self._run_with_stdin(drain_mod, {"hook_event_name": "Stop"})
        payload = json.loads(capsys.readouterr().out)
        assert payload["decision"] == "block"
        assert "fire" in payload["reason"]
