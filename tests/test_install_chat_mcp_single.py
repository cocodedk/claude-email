"""Tests for --single flag in scripts/install-chat-mcp.py."""
import importlib.util
import json
import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parent.parent
_SCRIPT_PATH = _REPO_ROOT / "scripts" / "install-chat-mcp.py"


@pytest.fixture
def script_mod(monkeypatch):
    for key in ("CLAUDE_CWD", "CHAT_URL", "CLAUDE_CONFIG_DIR"):
        monkeypatch.delenv(key, raising=False)
    spec = importlib.util.spec_from_file_location("install_chat_mcp", _SCRIPT_PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def single_project(tmp_path):
    proj = tmp_path / "MyProject"
    proj.mkdir()
    (proj / "src").mkdir()
    (proj / "README.md").write_text("hello")
    return proj


class TestSingleFlag:
    def _run(self, script_mod, monkeypatch, target, config_dir, single=True):
        monkeypatch.setenv("CHAT_URL", "http://127.0.0.1:8420/sse")
        monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(config_dir))
        argv = ["install-chat-mcp.py"]
        if single:
            argv.append("--single")
        argv.append(str(target))
        monkeypatch.setattr(sys, "argv", argv)
        return script_mod.main()

    def test_installs_into_project_root(
        self, script_mod, monkeypatch, single_project, tmp_path,
    ):
        config_dir = tmp_path / "cfg"
        config_dir.mkdir()
        rc = self._run(script_mod, monkeypatch, single_project, config_dir)
        assert rc == 0
        mcp = json.loads((single_project / ".mcp.json").read_text())
        assert mcp["mcpServers"]["claude-chat"]["type"] == "sse"
        settings = json.loads(
            (single_project / ".claude" / "settings.local.json").read_text(),
        )
        assert "SessionStart" in settings["hooks"]

    def test_does_not_recurse_into_subdirs(
        self, script_mod, monkeypatch, single_project, tmp_path,
    ):
        config_dir = tmp_path / "cfg"
        config_dir.mkdir()
        self._run(script_mod, monkeypatch, single_project, config_dir)
        assert not (single_project / "src" / ".mcp.json").exists()

    def test_approves_in_claude_config(
        self, script_mod, monkeypatch, single_project, tmp_path,
    ):
        config_dir = tmp_path / "cfg"
        config_dir.mkdir()
        self._run(script_mod, monkeypatch, single_project, config_dir)
        data = json.loads((config_dir / ".claude.json").read_text())
        proj_path = str(single_project.resolve())
        assert "claude-chat" in data["projects"][proj_path]["enabledMcpjsonServers"]

    def test_prints_project_name(
        self, script_mod, monkeypatch, single_project, tmp_path, capsys,
    ):
        config_dir = tmp_path / "cfg"
        config_dir.mkdir()
        self._run(script_mod, monkeypatch, single_project, config_dir)
        out = capsys.readouterr().out
        assert "MyProject" in out

    def test_idempotent(
        self, script_mod, monkeypatch, single_project, tmp_path,
    ):
        config_dir = tmp_path / "cfg"
        config_dir.mkdir()
        self._run(script_mod, monkeypatch, single_project, config_dir)
        self._run(script_mod, monkeypatch, single_project, config_dir)
        data = json.loads((config_dir / ".claude.json").read_text())
        proj_path = str(single_project.resolve())
        approved = data["projects"][proj_path]["enabledMcpjsonServers"]
        assert approved.count("claude-chat") == 1

    def test_flag_order_does_not_matter(
        self, script_mod, monkeypatch, single_project, tmp_path,
    ):
        config_dir = tmp_path / "cfg"
        config_dir.mkdir()
        monkeypatch.setenv("CHAT_URL", "http://127.0.0.1:8420/sse")
        monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(config_dir))
        monkeypatch.setattr(
            sys, "argv",
            ["install-chat-mcp.py", str(single_project), "--single"],
        )
        rc = script_mod.main()
        assert rc == 0
        assert (single_project / ".mcp.json").exists()

    def test_errors_on_not_a_directory(
        self, script_mod, monkeypatch, tmp_path, capsys,
    ):
        not_dir = tmp_path / "file.txt"
        not_dir.write_text("x")
        monkeypatch.setenv("CHAT_URL", "http://127.0.0.1:8420/sse")
        monkeypatch.setattr(
            sys, "argv",
            ["install-chat-mcp.py", "--single", str(not_dir)],
        )
        rc = script_mod.main()
        assert rc == 1
        assert "is not a directory" in capsys.readouterr().err
