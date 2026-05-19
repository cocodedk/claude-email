"""Tests for spawner MCP config injection and project approval helpers."""
import json


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
