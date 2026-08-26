"""Tests for the PostToolUse[Bash] hook wiring in inject_session_start_hook."""
import json
import os
import pytest


class TestInjectSessionStartHookPostTool:
    HOOK = "/opt/claude-email/scripts/chat-session-start-hook.sh"
    DRAIN = "/opt/claude-email/scripts/chat-drain-inbox.py"
    PRECOMPACT = "/opt/claude-email/scripts/chat-precompact-hook.py"
    POSTTOOL = "/opt/claude-email/scripts/chat-drain-on-bash-commit.sh"

    def test_posttool_uses_bash_matcher(self, tmp_path):
        """PostToolUse must scope to Bash invocations only — other tool
        types should not fire the drain wrapper."""
        from src.spawner import inject_session_start_hook
        inject_session_start_hook(
            str(tmp_path), self.HOOK, self.DRAIN, self.PRECOMPACT,
            self.POSTTOOL,
        )
        data = json.loads((tmp_path / ".claude" / "settings.local.json").read_text())
        entries = data["hooks"]["PostToolUse"]
        assert len(entries) == 1
        assert entries[0]["matcher"] == "Bash"
        cmds = [h["command"] for h in entries[0]["hooks"]]
        assert cmds == [self.POSTTOOL]

    def test_posttool_relative_path_raises(self, tmp_path):
        from src.spawner import inject_session_start_hook
        with pytest.raises(ValueError, match="posttool_drain_script_path"):
            inject_session_start_hook(
                str(tmp_path), self.HOOK, self.DRAIN, self.PRECOMPACT,
                "scripts/chat-drain-on-bash-commit.sh",
            )

    def test_posttool_default_path_resolves_alongside_drain(self, tmp_path):
        """Calling inject_session_start_hook without an explicit
        posttool_drain_script_path uses the default that ships with
        claude-email — chat-drain-on-bash-commit.sh in this repo's
        scripts/ directory."""
        from src.spawner import inject_session_start_hook
        inject_session_start_hook(str(tmp_path), self.HOOK, self.DRAIN)
        data = json.loads((tmp_path / ".claude" / "settings.local.json").read_text())
        pt_entries = data["hooks"].get("PostToolUse")
        assert pt_entries is not None
        cmds = [h["command"] for h in pt_entries[0]["hooks"]]
        assert len(cmds) == 1
        assert cmds[0].endswith("chat-drain-on-bash-commit.sh")
        assert os.path.isabs(cmds[0])

    def test_posttool_replaces_stale_path_on_reinstall(self, tmp_path):
        """When the install path moves, the PostToolUse wrapper's old
        absolute path must be replaced — not piled up as a duplicate.
        Mirrors the cleanup that SessionStart/Stop/PreCompact already do."""
        from src.spawner import inject_session_start_hook
        old_posttool = "/old/install/scripts/chat-drain-on-bash-commit.sh"
        inject_session_start_hook(
            str(tmp_path), self.HOOK, self.DRAIN, self.PRECOMPACT, old_posttool,
        )
        inject_session_start_hook(
            str(tmp_path), self.HOOK, self.DRAIN, self.PRECOMPACT, self.POSTTOOL,
        )
        data = json.loads((tmp_path / ".claude" / "settings.local.json").read_text())
        cmds = [h["command"] for h in data["hooks"]["PostToolUse"][0]["hooks"]]
        assert cmds == [self.POSTTOOL]
        assert old_posttool not in cmds

    def test_posttool_preserves_third_party_bash_hooks(self, tmp_path):
        """A project's own PostToolUse[Bash] entry (e.g. a /simplify
        reminder) is preserved as a separate entry. Multiple Bash entries
        coexist — Claude Code fires them all in order."""
        from src.spawner import inject_session_start_hook
        (tmp_path / ".claude").mkdir()
        third_party_cmd = "/opt/other/log-bash.sh"
        existing = {
            "hooks": {
                "PostToolUse": [{
                    "matcher": "Bash",
                    "hooks": [{"type": "command", "command": third_party_cmd}],
                }],
            },
        }
        (tmp_path / ".claude" / "settings.local.json").write_text(json.dumps(existing))
        inject_session_start_hook(
            str(tmp_path), self.HOOK, self.DRAIN, self.PRECOMPACT, self.POSTTOOL,
        )
        data = json.loads((tmp_path / ".claude" / "settings.local.json").read_text())
        entries = data["hooks"]["PostToolUse"]
        # Our wrapper landed first as its own entry.
        assert [h["command"] for h in entries[0]["hooks"]] == [self.POSTTOOL]
        # Third-party Bash hook preserved as a separate entry.
        kept = next(
            (e for e in entries[1:] if e.get("matcher") == "Bash"), None,
        )
        assert kept is not None
        assert [h["command"] for h in kept["hooks"]] == [third_party_cmd]
