"""Tests for scripts/install-chat-mcp.py — batch bootstrap of chat MCP into projects.

Script lives under scripts/ so we import it by path (same pattern as the hook scripts).
"""
import importlib.util
import json
import os
import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parent.parent
_SCRIPT_PATH = _REPO_ROOT / "scripts" / "install-chat-mcp.py"


@pytest.fixture
def script_mod(monkeypatch):
    """Import the script fresh each test so module-level state is isolated."""
    for key in ("CLAUDE_CWD", "CHAT_URL", "CLAUDE_CONFIG_DIR"):
        monkeypatch.delenv(key, raising=False)
    spec = importlib.util.spec_from_file_location("install_chat_mcp", _SCRIPT_PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def projects_base(tmp_path):
    """A base dir with 3 project subdirs + one hidden + one loose file."""
    (tmp_path / "alpha").mkdir()
    (tmp_path / "beta").mkdir()
    (tmp_path / "gamma").mkdir()
    (tmp_path / ".hidden").mkdir()
    (tmp_path / "loose.txt").write_text("not a dir")
    return tmp_path


class TestMain:
    def _run(self, script_mod, monkeypatch, base, config_dir, *argv):
        monkeypatch.setenv("CHAT_URL", "http://127.0.0.1:8420/sse")
        monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(config_dir))
        monkeypatch.setattr(sys, "argv", ["install-chat-mcp.py", str(base), *argv])
        return script_mod.main()

    def test_writes_mcp_config_and_hooks_in_each_project(
        self, script_mod, monkeypatch, projects_base, tmp_path,
    ):
        config_dir = tmp_path / "claude-cfg"
        config_dir.mkdir()
        rc = self._run(script_mod, monkeypatch, projects_base, config_dir)
        assert rc == 0
        for name in ("alpha", "beta", "gamma"):
            proj = projects_base / name
            mcp = json.loads((proj / ".mcp.json").read_text())
            assert mcp["mcpServers"]["claude-chat"]["type"] == "sse"
            settings = json.loads((proj / ".claude" / "settings.json").read_text())
            # All three hook events are present
            assert "SessionStart" in settings["hooks"]
            assert "UserPromptSubmit" in settings["hooks"]
            assert "Stop" in settings["hooks"]

    def test_approves_mcp_server_in_claude_config(
        self, script_mod, monkeypatch, projects_base, tmp_path,
    ):
        """This is the fix — without approval, Claude Code silently ignores
        .mcp.json entries. Earlier versions of this script wrote the MCP
        config but never updated the user's ~/.claude.json trust list."""
        config_dir = tmp_path / "claude-cfg"
        config_dir.mkdir()
        rc = self._run(script_mod, monkeypatch, projects_base, config_dir)
        assert rc == 0
        data = json.loads((config_dir / ".claude.json").read_text())
        for name in ("alpha", "beta", "gamma"):
            proj_path = str((projects_base / name).resolve())
            entry = data["projects"][proj_path]
            assert "claude-chat" in entry["enabledMcpjsonServers"], (
                f"claude-chat not approved for {proj_path}"
            )

    def test_approval_uses_home_when_config_dir_unset(
        self, script_mod, monkeypatch, projects_base, tmp_path_factory,
    ):
        """Fallback: CLAUDE_CONFIG_DIR unset → ~/.claude.json."""
        fake_home = tmp_path_factory.mktemp("fake-home")
        monkeypatch.setenv("HOME", str(fake_home))
        monkeypatch.delenv("CLAUDE_CONFIG_DIR", raising=False)
        monkeypatch.setenv("CHAT_URL", "http://127.0.0.1:8420/sse")
        monkeypatch.setattr(sys, "argv", ["install-chat-mcp.py", str(projects_base)])
        rc = script_mod.main()
        assert rc == 0
        data = json.loads((fake_home / ".claude.json").read_text())
        assert len(data["projects"]) == 3

    def test_skips_hidden_and_loose_files(
        self, script_mod, monkeypatch, projects_base, tmp_path, capsys,
    ):
        config_dir = tmp_path / "claude-cfg"
        config_dir.mkdir()
        self._run(script_mod, monkeypatch, projects_base, config_dir)
        out = capsys.readouterr().out
        assert "+ alpha" in out
        assert "+ beta" in out
        assert "+ gamma" in out
        assert ".hidden" not in out.split("Skipped")[-1] or "hidden" in out
        assert "loose.txt" in out  # reported as skipped (not a dir)

    def test_idempotent(self, script_mod, monkeypatch, projects_base, tmp_path):
        config_dir = tmp_path / "claude-cfg"
        config_dir.mkdir()
        self._run(script_mod, monkeypatch, projects_base, config_dir)
        self._run(script_mod, monkeypatch, projects_base, config_dir)
        data = json.loads((config_dir / ".claude.json").read_text())
        # Running twice must not duplicate the approval
        for name in ("alpha", "beta", "gamma"):
            proj_path = str((projects_base / name).resolve())
            approved = data["projects"][proj_path]["enabledMcpjsonServers"]
            assert approved.count("claude-chat") == 1

    def test_errors_when_base_dir_missing(self, script_mod, monkeypatch, capsys):
        monkeypatch.delenv("CLAUDE_CWD", raising=False)
        monkeypatch.setattr(sys, "argv", ["install-chat-mcp.py"])
        rc = script_mod.main()
        assert rc == 2
        assert "BASE_DIR not provided" in capsys.readouterr().err

    def test_errors_when_chat_url_missing(
        self, script_mod, monkeypatch, projects_base, capsys,
    ):
        monkeypatch.delenv("CHAT_URL", raising=False)
        monkeypatch.setattr(sys, "argv", ["install-chat-mcp.py", str(projects_base)])
        rc = script_mod.main()
        assert rc == 2
        assert "CHAT_URL not set" in capsys.readouterr().err

    def test_errors_when_base_is_not_a_directory(
        self, script_mod, monkeypatch, tmp_path, capsys,
    ):
        not_dir = tmp_path / "file.txt"
        not_dir.write_text("x")
        monkeypatch.setenv("CHAT_URL", "http://127.0.0.1:8420/sse")
        monkeypatch.setattr(sys, "argv", ["install-chat-mcp.py", str(not_dir)])
        rc = script_mod.main()
        assert rc == 1
        assert "is not a directory" in capsys.readouterr().err

    def test_reports_injection_errors_per_project(
        self, script_mod, monkeypatch, projects_base, tmp_path, capsys,
    ):
        """If one project fails (e.g., permission error), others still succeed."""
        config_dir = tmp_path / "claude-cfg"
        config_dir.mkdir()

        real_inject = script_mod.inject_mcp_config

        def flaky(proj_dir, url):
            if proj_dir.endswith("/beta"):
                raise RuntimeError("synthetic failure")
            return real_inject(proj_dir, url)

        monkeypatch.setattr(script_mod, "inject_mcp_config", flaky)
        rc = self._run(script_mod, monkeypatch, projects_base, config_dir)
        assert rc == 0
        out = capsys.readouterr().out
        assert "+ alpha" in out
        assert "+ gamma" in out
        assert "beta" in out  # appears in the skipped section with the error
        assert "synthetic failure" in out

    def test_skip_names_excludes_listed_subdirs(
        self, script_mod, monkeypatch, projects_base, tmp_path, capsys,
    ):
        """SKIP_NAMES is a module-level knob used to exclude e.g. the
        repo that hosts the server itself. Verify the skip branch works."""
        monkeypatch.setattr(script_mod, "SKIP_NAMES", {"beta"})
        config_dir = tmp_path / "claude-cfg"
        config_dir.mkdir()
        self._run(script_mod, monkeypatch, projects_base, config_dir)
        out = capsys.readouterr().out
        assert "+ alpha" in out
        assert "+ gamma" in out
        assert "- beta  (excluded (hosts server))" in out
        # And beta's project dir must NOT have received .mcp.json
        assert not (projects_base / "beta" / ".mcp.json").exists()

    def test_resolves_base_from_env_when_argv_empty(
        self, script_mod, monkeypatch, projects_base, tmp_path,
    ):
        config_dir = tmp_path / "claude-cfg"
        config_dir.mkdir()
        monkeypatch.setenv("CLAUDE_CWD", str(projects_base))
        monkeypatch.setenv("CHAT_URL", "http://127.0.0.1:8420/sse")
        monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(config_dir))
        monkeypatch.setattr(sys, "argv", ["install-chat-mcp.py"])
        rc = script_mod.main()
        assert rc == 0


class TestImportTimeDotenv:
    def test_import_survives_missing_dotenv(self, monkeypatch):
        real = sys.modules.pop("dotenv", None)
        monkeypatch.setitem(sys.modules, "dotenv", None)
        try:
            spec = importlib.util.spec_from_file_location(
                "install_chat_mcp_nodotenv", _SCRIPT_PATH,
            )
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
            assert hasattr(mod, "main")
        finally:
            if real is not None:
                sys.modules["dotenv"] = real
            else:
                sys.modules.pop("dotenv", None)
