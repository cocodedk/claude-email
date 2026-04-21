"""Tests for agent spawner — name building, MCP injection, process spawning."""
import json
import os
import subprocess
import pytest
from src.chat_db import ChatDB


class TestBuildAgentName:
    def test_build_agent_name(self):
        from src.spawner import build_agent_name
        assert build_agent_name("/home/user/0-projects/fits") == "agent-fits"

    def test_build_agent_name_trailing_slash(self):
        from src.spawner import build_agent_name
        assert build_agent_name("/home/user/0-projects/fits/") == "agent-fits"


class TestInjectMcpConfig:
    def test_inject_mcp_config_creates_file(self, tmp_path):
        from src.spawner import inject_mcp_config

        project_dir = str(tmp_path)
        inject_mcp_config(project_dir, "http://localhost:8080/mcp")

        mcp_file = tmp_path / ".mcp.json"
        assert mcp_file.exists()
        data = json.loads(mcp_file.read_text())
        assert data == {
            "mcpServers": {
                "claude-chat": {"type": "sse", "url": "http://localhost:8080/mcp"}
            }
        }

    def test_inject_mcp_config_normalizes_wrong_shape(self, tmp_path):
        from src.spawner import inject_mcp_config
        (tmp_path / ".mcp.json").write_text(json.dumps({"mcpServers": []}))
        inject_mcp_config(str(tmp_path), "http://localhost:9090/mcp")
        data = json.loads((tmp_path / ".mcp.json").read_text())
        assert data["mcpServers"]["claude-chat"] == {
            "type": "sse", "url": "http://localhost:9090/mcp",
        }

    def test_inject_mcp_config_merges_existing(self, tmp_path):
        from src.spawner import inject_mcp_config

        mcp_file = tmp_path / ".mcp.json"
        existing = {
            "mcpServers": {
                "playwright": {
                    "command": "npx",
                    "args": ["@playwright/mcp@latest"],
                }
            }
        }
        mcp_file.write_text(json.dumps(existing))

        inject_mcp_config(str(tmp_path), "http://localhost:9090/mcp")

        data = json.loads(mcp_file.read_text())
        # Existing server preserved
        assert data["mcpServers"]["playwright"] == {
            "command": "npx",
            "args": ["@playwright/mcp@latest"],
        }
        # New server added with explicit SSE transport type
        assert data["mcpServers"]["claude-chat"] == {
            "type": "sse",
            "url": "http://localhost:9090/mcp",
        }


class TestInjectSessionStartHook:
    HOOK = "/opt/claude-email/scripts/chat-session-start-hook.sh"
    DRAIN = "/opt/claude-email/scripts/chat-drain-inbox.py"

    def test_creates_settings_file_with_all_events(self, tmp_path):
        from src.spawner import inject_session_start_hook
        inject_session_start_hook(str(tmp_path), self.HOOK, self.DRAIN)
        data = json.loads((tmp_path / ".claude" / "settings.json").read_text())
        assert data == {
            "hooks": {
                "SessionStart": [{
                    "matcher": "startup|resume",
                    "hooks": [
                        {"type": "command", "command": self.HOOK},
                        {"type": "command", "command": self.DRAIN},
                    ],
                }],
                "UserPromptSubmit": [{
                    "matcher": "",
                    "hooks": [{"type": "command", "command": self.DRAIN}],
                }],
                "Stop": [{
                    "matcher": "",
                    "hooks": [{"type": "command", "command": self.DRAIN}],
                }],
            }
        }

    def test_stop_event_wired_to_drain_script(self, tmp_path):
        """Stop hook closes the gap between 'peer sent message mid-response'
        and 'next user prompt' — it reinjects pending messages as a block
        reason before the session idles."""
        from src.spawner import inject_session_start_hook
        inject_session_start_hook(str(tmp_path), self.HOOK, self.DRAIN)
        data = json.loads((tmp_path / ".claude" / "settings.json").read_text())
        stop_entries = data["hooks"]["Stop"]
        assert len(stop_entries) == 1
        cmds = [h["command"] for h in stop_entries[0]["hooks"]]
        assert cmds == [self.DRAIN]

    def test_stop_replaces_stale_drain_path_on_reinstall(self, tmp_path):
        from src.spawner import inject_session_start_hook
        old_drain = "/old/install/scripts/chat-drain-inbox.py"
        inject_session_start_hook(str(tmp_path), self.HOOK, old_drain)
        inject_session_start_hook(str(tmp_path), self.HOOK, self.DRAIN)
        data = json.loads((tmp_path / ".claude" / "settings.json").read_text())
        stop_cmds = [h["command"] for h in data["hooks"]["Stop"][0]["hooks"]]
        assert stop_cmds == [self.DRAIN]
        assert old_drain not in stop_cmds

    def test_stop_preserves_third_party_hooks(self, tmp_path):
        """Third-party Stop hook entries keep their own matcher and hooks —
        our drain lands as a separate entry, not merged into theirs."""
        from src.spawner import inject_session_start_hook
        (tmp_path / ".claude").mkdir()
        existing = {
            "hooks": {
                "Stop": [{"matcher": "my-matcher", "hooks": [
                    {"type": "command", "command": "/opt/other/notify.sh"},
                ]}],
            },
        }
        (tmp_path / ".claude" / "settings.json").write_text(json.dumps(existing))
        inject_session_start_hook(str(tmp_path), self.HOOK, self.DRAIN)
        data = json.loads((tmp_path / ".claude" / "settings.json").read_text())
        stop_entries = data["hooks"]["Stop"]
        # Our entry is first, third-party entry preserved second with its
        # original matcher + remaining hooks intact.
        our_cmds = [h["command"] for h in stop_entries[0]["hooks"]]
        assert our_cmds == [self.DRAIN]
        kept = next(
            (e for e in stop_entries[1:] if e.get("matcher") == "my-matcher"),
            None,
        )
        assert kept is not None
        kept_cmds = [h["command"] for h in kept["hooks"]]
        assert kept_cmds == ["/opt/other/notify.sh"]

    def test_preserves_third_party_hooks(self, tmp_path):
        from src.spawner import inject_session_start_hook
        (tmp_path / ".claude").mkdir()
        existing = {
            "theme": "dark",
            "hooks": {
                "UserPromptSubmit": [{"matcher": "custom", "hooks": [
                    {"type": "command", "command": "/bin/true"},
                ]}],
            },
        }
        (tmp_path / ".claude" / "settings.json").write_text(json.dumps(existing))
        inject_session_start_hook(str(tmp_path), self.HOOK, self.DRAIN)
        data = json.loads((tmp_path / ".claude" / "settings.json").read_text())
        assert data["theme"] == "dark"
        ups_entries = data["hooks"]["UserPromptSubmit"]
        our_cmds = [h["command"] for h in ups_entries[0]["hooks"]]
        assert self.DRAIN in our_cmds
        # Third-party entry preserved with its original matcher.
        kept = next(
            (e for e in ups_entries[1:] if e.get("matcher") == "custom"), None,
        )
        assert kept is not None
        assert [h["command"] for h in kept["hooks"]] == ["/bin/true"]

    def test_is_idempotent(self, tmp_path):
        from src.spawner import inject_session_start_hook
        inject_session_start_hook(str(tmp_path), self.HOOK, self.DRAIN)
        inject_session_start_hook(str(tmp_path), self.HOOK, self.DRAIN)
        data = json.loads((tmp_path / ".claude" / "settings.json").read_text())
        ss_cmds = [h["command"] for h in data["hooks"]["SessionStart"][0]["hooks"]]
        ups_cmds = [h["command"] for h in data["hooks"]["UserPromptSubmit"][0]["hooks"]]
        assert ss_cmds == [self.HOOK, self.DRAIN]
        assert ups_cmds == [self.DRAIN]

    def test_replaces_stale_paths_when_install_moves(self, tmp_path):
        """When the claude-email repo is moved, re-running the injector
        must replace the old absolute paths — not pile up as duplicates."""
        from src.spawner import inject_session_start_hook
        old_hook = "/old/install/scripts/chat-session-start-hook.sh"
        old_drain = "/old/install/scripts/chat-drain-inbox.py"
        inject_session_start_hook(str(tmp_path), old_hook, old_drain)
        inject_session_start_hook(str(tmp_path), self.HOOK, self.DRAIN)
        data = json.loads((tmp_path / ".claude" / "settings.json").read_text())
        ss = [h["command"] for h in data["hooks"]["SessionStart"][0]["hooks"]]
        assert ss == [self.HOOK, self.DRAIN]
        assert old_hook not in ss
        assert old_drain not in ss

    def test_rejects_relative_hook_path(self, tmp_path):
        from src.spawner import inject_session_start_hook
        with pytest.raises(ValueError, match="hook_script_path must be absolute"):
            inject_session_start_hook(str(tmp_path), "hook.sh", self.DRAIN)

    def test_rejects_relative_drain_path(self, tmp_path):
        from src.spawner import inject_session_start_hook
        with pytest.raises(ValueError, match="drain_script_path must be absolute"):
            inject_session_start_hook(str(tmp_path), self.HOOK, "drain.py")

    def test_default_drain_path(self, tmp_path):
        """When drain_script_path is omitted, DRAIN_SCRIPT is used."""
        from src.spawner import inject_session_start_hook
        from src.agent_bootstrap import DRAIN_SCRIPT
        inject_session_start_hook(str(tmp_path), self.HOOK)
        data = json.loads((tmp_path / ".claude" / "settings.json").read_text())
        ups_cmds = [h["command"] for h in data["hooks"]["UserPromptSubmit"][0]["hooks"]]
        assert ups_cmds == [DRAIN_SCRIPT]

    def test_normalizes_wrong_shape_top_level(self, tmp_path):
        from src.spawner import inject_session_start_hook
        (tmp_path / ".claude").mkdir()
        (tmp_path / ".claude" / "settings.json").write_text(json.dumps([1, 2, 3]))
        inject_session_start_hook(str(tmp_path), self.HOOK, self.DRAIN)
        data = json.loads((tmp_path / ".claude" / "settings.json").read_text())
        assert isinstance(data, dict)
        assert data["hooks"]["SessionStart"][0]["hooks"][0]["command"] == self.HOOK

    def test_skips_non_dict_entries_in_existing_event(self, tmp_path):
        """Malformed entries (e.g., a bare string where a dict was expected) are silently skipped."""
        from src.spawner import inject_session_start_hook
        (tmp_path / ".claude").mkdir()
        (tmp_path / ".claude" / "settings.json").write_text(json.dumps({
            "hooks": {
                "UserPromptSubmit": ["bogus-string-entry"],
            }
        }))
        inject_session_start_hook(str(tmp_path), self.HOOK, self.DRAIN)
        data = json.loads((tmp_path / ".claude" / "settings.json").read_text())
        ups_cmds = [h["command"] for h in data["hooks"]["UserPromptSubmit"][0]["hooks"]]
        assert ups_cmds == [self.DRAIN]

    def test_normalizes_hooks_key_when_list(self, tmp_path):
        from src.spawner import inject_session_start_hook
        (tmp_path / ".claude").mkdir()
        (tmp_path / ".claude" / "settings.json").write_text(json.dumps({"hooks": []}))
        inject_session_start_hook(str(tmp_path), self.HOOK, self.DRAIN)
        data = json.loads((tmp_path / ".claude" / "settings.json").read_text())
        assert isinstance(data["hooks"], dict)
        assert data["hooks"]["SessionStart"][0]["hooks"][0]["command"] == self.HOOK

    def test_skips_entries_whose_hooks_key_is_not_a_list(self, tmp_path):
        """Defensively skip malformed entries where 'hooks' is a dict/str —
        they must not crash the merge."""
        from src.spawner import inject_session_start_hook
        (tmp_path / ".claude").mkdir()
        existing = {
            "hooks": {
                "Stop": [
                    {"matcher": "junk", "hooks": "not-a-list"},
                    {"matcher": "real", "hooks": [
                        {"type": "command", "command": "/opt/other/notify.sh"},
                    ]},
                ],
            },
        }
        (tmp_path / ".claude" / "settings.json").write_text(json.dumps(existing))
        inject_session_start_hook(str(tmp_path), self.HOOK, self.DRAIN)
        data = json.loads((tmp_path / ".claude" / "settings.json").read_text())
        stop_entries = data["hooks"]["Stop"]
        our_cmds = [h["command"] for h in stop_entries[0]["hooks"]]
        assert our_cmds == [self.DRAIN]
        # The "junk" entry is dropped; the "real" entry survives with its
        # matcher and command intact.
        surviving_matchers = {e.get("matcher") for e in stop_entries[1:]}
        assert "junk" not in surviving_matchers
        assert "real" in surviving_matchers


class TestValidateProjectPath:
    def test_valid_path_returns_resolved(self, tmp_path):
        from src.spawner import validate_project_path

        d = tmp_path / "proj"
        d.mkdir()
        result = validate_project_path(str(d))
        assert result == str(d.resolve())

    def test_nonexistent_dir_raises(self, tmp_path):
        from src.spawner import validate_project_path

        with pytest.raises(ValueError, match="does not exist"):
            validate_project_path(str(tmp_path / "nope"))

    def test_outside_allowed_base_raises(self, tmp_path):
        from src.spawner import validate_project_path

        outside = tmp_path / "outside"
        outside.mkdir()
        allowed = tmp_path / "allowed"
        allowed.mkdir()

        with pytest.raises(ValueError, match="outside allowed base"):
            validate_project_path(str(outside), allowed_base=str(allowed))

    def test_inside_allowed_base_passes(self, tmp_path):
        from src.spawner import validate_project_path

        base = tmp_path / "base"
        base.mkdir()
        proj = base / "proj"
        proj.mkdir()

        result = validate_project_path(str(proj), allowed_base=str(base))
        assert result == str(proj.resolve())

    def test_traversal_blocked(self, tmp_path):
        from src.spawner import validate_project_path

        base = tmp_path / "base"
        base.mkdir()
        # tmp_path exists but is outside base
        with pytest.raises(ValueError, match="outside allowed base"):
            validate_project_path(str(base / ".."), allowed_base=str(base))

    def test_bare_name_resolved_against_allowed_base(self, tmp_path):
        from src.spawner import validate_project_path

        base = tmp_path / "base"
        base.mkdir()
        proj = base / "babakcast"
        proj.mkdir()

        result = validate_project_path("babakcast", allowed_base=str(base))
        assert result == str(proj.resolve())

    def test_relative_subpath_resolved_against_allowed_base(self, tmp_path):
        from src.spawner import validate_project_path

        base = tmp_path / "base"
        base.mkdir()
        nested = base / "group" / "sub"
        nested.mkdir(parents=True)

        result = validate_project_path("group/sub", allowed_base=str(base))
        assert result == str(nested.resolve())

    def test_bare_name_without_allowed_base_falls_through(self, tmp_path, monkeypatch):
        from src.spawner import validate_project_path

        # With no allowed_base, "foo" resolves against cwd — unchanged legacy behavior
        monkeypatch.chdir(tmp_path)
        (tmp_path / "foo").mkdir()
        result = validate_project_path("foo")
        assert result == str((tmp_path / "foo").resolve())


class TestApproveMcpServerForProject:
    """Pre-approve a project-scope MCP server in the config dir's .claude.json.

    Claude Code requires explicit per-project approval of .mcp.json servers;
    without it the spawned agent launches without the chat tools. This helper
    injects approval so email-spawned agents work out of the box.
    """

    def test_creates_claude_json_when_missing(self, tmp_path):
        from src.spawner import approve_mcp_server_for_project
        approve_mcp_server_for_project(str(tmp_path), "/p/my-proj", "claude-chat")
        data = json.loads((tmp_path / ".claude.json").read_text())
        assert data["projects"]["/p/my-proj"]["enabledMcpjsonServers"] == ["claude-chat"]

    def test_creates_project_entry_when_other_projects_exist(self, tmp_path):
        from src.spawner import approve_mcp_server_for_project
        (tmp_path / ".claude.json").write_text(json.dumps({
            "projects": {"/other": {"enabledMcpjsonServers": ["some-server"]}},
            "topLevel": "keep-me",
        }))
        approve_mcp_server_for_project(str(tmp_path), "/p/new", "claude-chat")
        data = json.loads((tmp_path / ".claude.json").read_text())
        assert data["projects"]["/p/new"]["enabledMcpjsonServers"] == ["claude-chat"]
        assert data["projects"]["/other"]["enabledMcpjsonServers"] == ["some-server"]
        assert data["topLevel"] == "keep-me"

    def test_appends_to_existing_enabled_list(self, tmp_path):
        from src.spawner import approve_mcp_server_for_project
        (tmp_path / ".claude.json").write_text(json.dumps({
            "projects": {
                "/p": {
                    "enabledMcpjsonServers": ["pre-existing"],
                    "someOtherField": 42,
                }
            }
        }))
        approve_mcp_server_for_project(str(tmp_path), "/p", "claude-chat")
        data = json.loads((tmp_path / ".claude.json").read_text())
        assert data["projects"]["/p"]["enabledMcpjsonServers"] == ["pre-existing", "claude-chat"]
        assert data["projects"]["/p"]["someOtherField"] == 42

    def test_is_idempotent_when_already_approved(self, tmp_path):
        from src.spawner import approve_mcp_server_for_project
        (tmp_path / ".claude.json").write_text(json.dumps({
            "projects": {"/p": {"enabledMcpjsonServers": ["claude-chat"]}}
        }))
        approve_mcp_server_for_project(str(tmp_path), "/p", "claude-chat")
        approve_mcp_server_for_project(str(tmp_path), "/p", "claude-chat")
        data = json.loads((tmp_path / ".claude.json").read_text())
        assert data["projects"]["/p"]["enabledMcpjsonServers"] == ["claude-chat"]

    def test_creates_config_dir_when_missing(self, tmp_path):
        from src.spawner import approve_mcp_server_for_project
        target = tmp_path / "new_cfg_dir"
        approve_mcp_server_for_project(str(target), "/p", "claude-chat")
        assert (target / ".claude.json").exists()

    def test_handles_corrupted_json_by_rewriting(self, tmp_path):
        from src.spawner import approve_mcp_server_for_project
        (tmp_path / ".claude.json").write_text("{ not valid json")
        approve_mcp_server_for_project(str(tmp_path), "/p", "claude-chat")
        data = json.loads((tmp_path / ".claude.json").read_text())
        assert data["projects"]["/p"]["enabledMcpjsonServers"] == ["claude-chat"]

    def test_handles_wrong_shape_top_level(self, tmp_path):
        """Valid JSON of the wrong shape (e.g. list) must not crash."""
        from src.spawner import approve_mcp_server_for_project
        (tmp_path / ".claude.json").write_text(json.dumps([1, 2, 3]))
        approve_mcp_server_for_project(str(tmp_path), "/p", "claude-chat")
        data = json.loads((tmp_path / ".claude.json").read_text())
        assert data["projects"]["/p"]["enabledMcpjsonServers"] == ["claude-chat"]

    def test_handles_projects_as_list(self, tmp_path):
        from src.spawner import approve_mcp_server_for_project
        (tmp_path / ".claude.json").write_text(json.dumps({"projects": []}))
        approve_mcp_server_for_project(str(tmp_path), "/p", "claude-chat")
        data = json.loads((tmp_path / ".claude.json").read_text())
        assert data["projects"]["/p"]["enabledMcpjsonServers"] == ["claude-chat"]

    def test_handles_non_list_enabled_servers(self, tmp_path):
        from src.spawner import approve_mcp_server_for_project
        (tmp_path / ".claude.json").write_text(json.dumps({
            "projects": {"/p": {"enabledMcpjsonServers": "not-a-list"}}
        }))
        approve_mcp_server_for_project(str(tmp_path), "/p", "claude-chat")
        data = json.loads((tmp_path / ".claude.json").read_text())
        assert data["projects"]["/p"]["enabledMcpjsonServers"] == ["claude-chat"]

    def test_handles_project_entry_as_string(self, tmp_path):
        """A non-dict project entry gets normalized, not crashed on."""
        from src.spawner import approve_mcp_server_for_project
        (tmp_path / ".claude.json").write_text(json.dumps({
            "projects": {"/p": "unexpected-string-shape"}
        }))
        approve_mcp_server_for_project(str(tmp_path), "/p", "claude-chat")
        data = json.loads((tmp_path / ".claude.json").read_text())
        assert data["projects"]["/p"]["enabledMcpjsonServers"] == ["claude-chat"]


class TestSpawnAgent:
    @pytest.fixture
    def db(self, tmp_path):
        return ChatDB(str(tmp_path / "test.db"))

    def test_spawn_agent_calls_subprocess(self, db, tmp_path, mocker):
        from src.spawner import spawn_agent

        mock_proc = mocker.MagicMock()
        mock_proc.pid = 42
        mock_popen = mocker.patch("src.spawner.subprocess.Popen", return_value=mock_proc)
        mocker.patch("src.spawner.inject_mcp_config")

        project_dir = tmp_path / "my-project"
        project_dir.mkdir()
        name, pid = spawn_agent(db, str(project_dir), "http://localhost:8080/mcp")

        assert name == "agent-my-project"
        assert pid == 42

        mock_popen.assert_called_once()
        call_kwargs = mock_popen.call_args
        assert call_kwargs.kwargs["cwd"] == str(project_dir)
        assert call_kwargs.kwargs["shell"] is False

        # DB was updated
        agent = db.get_agent("agent-my-project")
        assert agent is not None
        assert agent["pid"] == 42

    def test_spawn_agent_with_instruction(self, db, tmp_path, mocker):
        from src.spawner import spawn_agent

        mock_proc = mocker.MagicMock()
        mock_proc.pid = 99
        mock_popen = mocker.patch("src.spawner.subprocess.Popen", return_value=mock_proc)
        mocker.patch("src.spawner.inject_mcp_config")

        project_dir = tmp_path / "proj"
        project_dir.mkdir()
        name, pid = spawn_agent(
            db, str(project_dir), "http://localhost:8080/mcp",
            instruction="run all tests",
        )

        cmd = mock_popen.call_args.args[0]
        assert cmd == ["claude", "--print", "run all tests"]

    def test_spawn_agent_without_instruction_uses_interactive(self, db, tmp_path, mocker):
        from src.spawner import spawn_agent

        mock_proc = mocker.MagicMock()
        mock_proc.pid = 50
        mock_popen = mocker.patch("src.spawner.subprocess.Popen", return_value=mock_proc)
        mocker.patch("src.spawner.inject_mcp_config")

        project_dir = tmp_path / "idle"
        project_dir.mkdir()
        spawn_agent(db, str(project_dir), "http://localhost:8080/mcp")

        cmd = mock_popen.call_args.args[0]
        assert cmd == ["claude"]
        assert "--print" not in cmd

    def test_spawn_agent_uses_devnull(self, db, tmp_path, mocker):
        from src.spawner import spawn_agent
        import subprocess

        mock_proc = mocker.MagicMock()
        mock_proc.pid = 7
        mock_popen = mocker.patch("src.spawner.subprocess.Popen", return_value=mock_proc)
        mocker.patch("src.spawner.inject_mcp_config")

        project_dir = tmp_path / "p"
        project_dir.mkdir()
        spawn_agent(db, str(project_dir), "http://localhost:8080/mcp")

        kwargs = mock_popen.call_args.kwargs
        assert kwargs["stdout"] == subprocess.DEVNULL
        assert kwargs["stderr"] == subprocess.DEVNULL

    def test_spawn_nonexistent_dir_raises(self, db, tmp_path, mocker):
        from src.spawner import spawn_agent

        mocker.patch("src.spawner.inject_mcp_config")

        with pytest.raises(ValueError, match="does not exist"):
            spawn_agent(db, str(tmp_path / "nope"), "http://localhost:8080/mcp")

    def test_spawn_outside_allowed_base_raises(self, db, tmp_path, mocker):
        from src.spawner import spawn_agent

        mocker.patch("src.spawner.inject_mcp_config")

        outside = tmp_path / "outside"
        outside.mkdir()
        base = tmp_path / "base"
        base.mkdir()

        with pytest.raises(ValueError, match="outside allowed base"):
            spawn_agent(
                db, str(outside), "http://localhost:8080/mcp",
                allowed_base=str(base),
            )

    def test_spawn_agent_yolo_adds_skip_permissions(self, db, tmp_path, mocker):
        from src.spawner import spawn_agent

        mock_proc = mocker.MagicMock()
        mock_proc.pid = 11
        mock_popen = mocker.patch("src.spawner.subprocess.Popen", return_value=mock_proc)
        mocker.patch("src.spawner.inject_mcp_config")

        project_dir = tmp_path / "proj"
        project_dir.mkdir()
        spawn_agent(
            db, str(project_dir), "http://localhost:8080/mcp",
            instruction="go", yolo=True,
        )
        cmd = mock_popen.call_args.args[0]
        assert cmd == ["claude", "--dangerously-skip-permissions", "--print", "go"]

    def test_spawn_agent_yolo_without_instruction(self, db, tmp_path, mocker):
        from src.spawner import spawn_agent

        mock_proc = mocker.MagicMock()
        mock_proc.pid = 12
        mock_popen = mocker.patch("src.spawner.subprocess.Popen", return_value=mock_proc)
        mocker.patch("src.spawner.inject_mcp_config")

        project_dir = tmp_path / "proj"
        project_dir.mkdir()
        spawn_agent(db, str(project_dir), "http://localhost:8080/mcp", yolo=True)
        cmd = mock_popen.call_args.args[0]
        assert cmd == ["claude", "--dangerously-skip-permissions"]

    def test_spawn_agent_writes_session_start_hook(self, db, tmp_path, mocker):
        from src.spawner import spawn_agent

        mock_proc = mocker.MagicMock()
        mock_proc.pid = 101
        mocker.patch("src.spawner.subprocess.Popen", return_value=mock_proc)

        project_dir = tmp_path / "my-project"
        project_dir.mkdir()

        spawn_agent(db, str(project_dir), "http://localhost:8080/mcp")

        assert (project_dir / ".mcp.json").exists()
        settings = json.loads((project_dir / ".claude" / "settings.json").read_text())
        cmd = settings["hooks"]["SessionStart"][0]["hooks"][0]["command"]
        assert os.path.isabs(cmd)
        assert cmd.endswith("/scripts/chat-session-start-hook.sh")

    def test_spawn_agent_extra_env(self, db, tmp_path, mocker):
        from src.spawner import spawn_agent

        mock_proc = mocker.MagicMock()
        mock_proc.pid = 13
        mock_popen = mocker.patch("src.spawner.subprocess.Popen", return_value=mock_proc)
        mocker.patch("src.spawner.inject_mcp_config")

        project_dir = tmp_path / "proj"
        project_dir.mkdir()
        spawn_agent(
            db, str(project_dir), "http://localhost:8080/mcp",
            extra_env={"CLAUDE_CONFIG_DIR": "/home/u/.claude-personal", "IS_SANDBOX": "1"},
        )
        env = mock_popen.call_args.kwargs["env"]
        assert env["CLAUDE_CONFIG_DIR"] == "/home/u/.claude-personal"
        assert env["IS_SANDBOX"] == "1"
        assert "PATH" in env


class TestSpawnAgentModelEffortBudget:
    """Tests for CLAUDE_MODEL, CLAUDE_EFFORT, CLAUDE_MAX_BUDGET_USD knobs in spawn_agent."""

    @pytest.fixture
    def db(self, tmp_path):
        return ChatDB(str(tmp_path / "test.db"))

    def _popen_mock(self, mocker, pid=77):
        mock_proc = mocker.MagicMock()
        mock_proc.pid = pid
        mock_popen = mocker.patch("src.spawner.subprocess.Popen", return_value=mock_proc)
        mocker.patch("src.spawner.inject_mcp_config")
        return mock_popen

    def test_model_flag_in_spawn_with_instruction(self, db, tmp_path, mocker):
        from src.spawner import spawn_agent
        mock_popen = self._popen_mock(mocker)
        project_dir = tmp_path / "proj"
        project_dir.mkdir()
        spawn_agent(db, str(project_dir), "http://localhost:8080/mcp",
                    instruction="go", model="claude-opus-4-5")
        cmd = mock_popen.call_args.args[0]
        assert "--model" in cmd
        assert cmd[cmd.index("--model") + 1] == "claude-opus-4-5"

    def test_model_flag_in_spawn_without_instruction(self, db, tmp_path, mocker):
        from src.spawner import spawn_agent
        mock_popen = self._popen_mock(mocker)
        project_dir = tmp_path / "proj2"
        project_dir.mkdir()
        spawn_agent(db, str(project_dir), "http://localhost:8080/mcp",
                    model="claude-opus-4-5")
        cmd = mock_popen.call_args.args[0]
        assert "--model" in cmd
        assert cmd[cmd.index("--model") + 1] == "claude-opus-4-5"

    def test_effort_flag_in_spawn_with_instruction(self, db, tmp_path, mocker):
        from src.spawner import spawn_agent
        mock_popen = self._popen_mock(mocker)
        project_dir = tmp_path / "proj3"
        project_dir.mkdir()
        spawn_agent(db, str(project_dir), "http://localhost:8080/mcp",
                    instruction="do it", effort="high")
        cmd = mock_popen.call_args.args[0]
        assert "--effort" in cmd
        assert cmd[cmd.index("--effort") + 1] == "high"

    def test_effort_flag_in_spawn_without_instruction(self, db, tmp_path, mocker):
        from src.spawner import spawn_agent
        mock_popen = self._popen_mock(mocker)
        project_dir = tmp_path / "proj4"
        project_dir.mkdir()
        spawn_agent(db, str(project_dir), "http://localhost:8080/mcp", effort="low")
        cmd = mock_popen.call_args.args[0]
        assert "--effort" in cmd
        assert cmd[cmd.index("--effort") + 1] == "low"

    def test_max_budget_usd_in_spawn_with_instruction(self, db, tmp_path, mocker):
        from src.spawner import spawn_agent
        mock_popen = self._popen_mock(mocker)
        project_dir = tmp_path / "proj5"
        project_dir.mkdir()
        spawn_agent(db, str(project_dir), "http://localhost:8080/mcp",
                    instruction="run", max_budget_usd="1.00")
        cmd = mock_popen.call_args.args[0]
        assert "--max-budget-usd" in cmd
        assert cmd[cmd.index("--max-budget-usd") + 1] == "1.00"

    def test_max_budget_usd_skipped_without_instruction_and_logs(self, db, tmp_path, mocker):
        from src.spawner import spawn_agent
        mock_popen = self._popen_mock(mocker)
        mock_logger = mocker.patch("src.spawner.logger")
        project_dir = tmp_path / "proj6"
        project_dir.mkdir()
        spawn_agent(db, str(project_dir), "http://localhost:8080/mcp",
                    max_budget_usd="1.00")
        cmd = mock_popen.call_args.args[0]
        assert "--max-budget-usd" not in cmd
        # should log exactly one INFO message about skipping
        info_calls = [c for c in mock_logger.info.call_args_list
                      if "budget" in c.args[0].lower()]
        assert len(info_calls) == 1
