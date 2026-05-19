"""Tests for scripts/chat-register-self.py — the SessionStart pre-registrar.

We load the script by path since it lives under scripts/ and is invoked
directly by the shell hook, not imported by any package.
"""
from src.chat_db import ChatDB
from tests._chat_register_self_helpers import reg_mod  # noqa: F401


class TestMain:
    def test_read_hook_payload_isatty_returns_empty(self, reg_mod, monkeypatch):
        """A TTY stdin (ad-hoc CLI invocation) means no hook payload —
        return {} rather than blocking on read()."""
        class _Tty:
            def isatty(self):
                return True
            def read(self):
                raise AssertionError("should not read when stdin is a tty")
        monkeypatch.setattr(reg_mod.sys, "stdin", _Tty())
        assert reg_mod._read_hook_payload() == {}

    def test_read_hook_payload_swallows_stdin_errors(self, reg_mod, monkeypatch):
        """Broken stdin (OSError/ValueError) must not crash — return {}."""
        class _Broken:
            def isatty(self):
                raise OSError("stdin gone")
            def read(self):
                return ""
        monkeypatch.setattr(reg_mod.sys, "stdin", _Broken())
        assert reg_mod._read_hook_payload() == {}

    def test_read_hook_payload_empty_returns_empty(self, reg_mod, monkeypatch):
        import io as _io
        monkeypatch.setattr(reg_mod.sys, "stdin", _io.StringIO("   \n"))
        assert reg_mod._read_hook_payload() == {}

    def test_read_hook_payload_invalid_json_returns_empty(
        self, reg_mod, monkeypatch,
    ):
        import io as _io
        monkeypatch.setattr(reg_mod.sys, "stdin", _io.StringIO("{not json"))
        assert reg_mod._read_hook_payload() == {}

    def test_main_swallows_agent_name_taken_race(
        self, reg_mod, tmp_path, monkeypatch,
    ):
        """If another process registered between the pre-check and our INSERT,
        we quietly concede (rc=0) rather than crashing the hook."""
        db_file = tmp_path / "bus.db"
        ChatDB(str(db_file))
        project = tmp_path / "proj-race"
        project.mkdir()
        monkeypatch.chdir(project)
        monkeypatch.setenv("CHAT_DB_PATH", str(db_file))
        from src.chat_db import AgentNameTaken as _Taken

        class _FakeDB:
            def __init__(self, *_a, **_kw): pass
            def get_agent(self, _): return None
            def find_live_owner(self, *_a, **_kw): return None
            def register_agent(self, *_a, **_kw):
                raise _Taken("agent-proj-race", 12345)

        monkeypatch.setattr(reg_mod, "ChatDB", _FakeDB)
        rc = reg_mod.main()
        assert rc == 0

    def test_main_reports_unexpected_exception(
        self, reg_mod, tmp_path, monkeypatch, capsys,
    ):
        """A generic exception from register_agent must be logged and
        returned as rc=1, so systemd / the hook runner sees a failure."""
        db_file = tmp_path / "bus.db"
        ChatDB(str(db_file))
        project = tmp_path / "proj-boom"
        project.mkdir()
        monkeypatch.chdir(project)
        monkeypatch.setenv("CHAT_DB_PATH", str(db_file))

        class _FakeDB:
            def __init__(self, *_a, **_kw): pass
            def get_agent(self, _): return None
            def find_live_owner(self, *_a, **_kw): return None
            def register_agent(self, *_a, **_kw):
                raise RuntimeError("synthetic explosion")

        monkeypatch.setattr(reg_mod, "ChatDB", _FakeDB)
        rc = reg_mod.main()
        assert rc == 1
        err = capsys.readouterr().err
        assert "synthetic explosion" in err
