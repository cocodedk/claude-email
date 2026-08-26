"""Tests for spawner's inject_session_start_hook — core wiring,
idempotency, settings.local.json normalization."""
import json
import os
import pytest


class TestInjectSessionStartHook:
    HOOK = "/opt/claude-email/scripts/chat-session-start-hook.sh"
    DRAIN = "/opt/claude-email/scripts/chat-drain-inbox.py"
    PRECOMPACT = "/opt/claude-email/scripts/chat-precompact-hook.py"

    def test_creates_settings_file_with_all_events(self, tmp_path):
        from src.spawner import inject_session_start_hook
        precompact = "/opt/claude-email/scripts/chat-precompact-hook.py"
        posttool = "/opt/claude-email/scripts/chat-drain-on-bash-commit.sh"
        stop_hook = "/opt/claude-email/scripts/chat-stop-hook.py"
        inject_session_start_hook(
            str(tmp_path), self.HOOK, self.DRAIN, precompact, posttool, stop_hook,
        )
        data = json.loads((tmp_path / ".claude" / "settings.local.json").read_text())
        assert data == {
            "hooks": {
                "SessionStart": [{
                    "matcher": "",
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
                    "hooks": [
                        {"type": "command", "command": self.DRAIN},
                        {"type": "command", "command": stop_hook},
                    ],
                }],
                "PreCompact": [{
                    "matcher": "",
                    "hooks": [{"type": "command", "command": precompact}],
                }],
                "PostToolUse": [{
                    "matcher": "Bash",
                    "hooks": [{"type": "command", "command": posttool}],
                }],
            }
        }

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
        (tmp_path / ".claude" / "settings.local.json").write_text(json.dumps(existing))
        inject_session_start_hook(str(tmp_path), self.HOOK, self.DRAIN)
        data = json.loads((tmp_path / ".claude" / "settings.local.json").read_text())
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
        data = json.loads((tmp_path / ".claude" / "settings.local.json").read_text())
        ss_cmds = [h["command"] for h in data["hooks"]["SessionStart"][0]["hooks"]]
        ups_cmds = [h["command"] for h in data["hooks"]["UserPromptSubmit"][0]["hooks"]]
        assert ss_cmds == [self.HOOK, self.DRAIN]
        assert ups_cmds == [self.DRAIN]

    def test_precompact_relative_path_raises(self, tmp_path):
        """All hook paths in settings.local.json must be absolute — Claude Code
        resolves them relative to nothing useful otherwise."""
        from src.spawner import inject_session_start_hook
        with pytest.raises(ValueError, match="precompact_script_path"):
            inject_session_start_hook(
                str(tmp_path), self.HOOK, self.DRAIN,
                "scripts/chat-precompact-hook.py",
            )

    def test_precompact_default_path_resolves_alongside_drain(self, tmp_path):
        """Calling inject_session_start_hook without an explicit
        precompact_script_path uses the default that ships with
        claude-email — the chat-precompact-hook.py sibling of the drain
        script in this repo's scripts/ directory."""
        from src.spawner import inject_session_start_hook
        inject_session_start_hook(str(tmp_path), self.HOOK, self.DRAIN)
        data = json.loads((tmp_path / ".claude" / "settings.local.json").read_text())
        pc_entries = data["hooks"].get("PreCompact")
        assert pc_entries is not None
        cmds = [h["command"] for h in pc_entries[0]["hooks"]]
        assert len(cmds) == 1
        assert cmds[0].endswith("chat-precompact-hook.py")
        assert os.path.isabs(cmds[0])

    def test_replaces_stale_paths_when_install_moves(self, tmp_path):
        """When the claude-email repo is moved, re-running the injector
        must replace the old absolute paths — not pile up as duplicates."""
        from src.spawner import inject_session_start_hook
        old_hook = "/old/install/scripts/chat-session-start-hook.sh"
        old_drain = "/old/install/scripts/chat-drain-inbox.py"
        inject_session_start_hook(str(tmp_path), old_hook, old_drain)
        inject_session_start_hook(str(tmp_path), self.HOOK, self.DRAIN)
        data = json.loads((tmp_path / ".claude" / "settings.local.json").read_text())
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
        data = json.loads((tmp_path / ".claude" / "settings.local.json").read_text())
        ups_cmds = [h["command"] for h in data["hooks"]["UserPromptSubmit"][0]["hooks"]]
        assert ups_cmds == [DRAIN_SCRIPT]

    def test_normalizes_wrong_shape_top_level(self, tmp_path):
        from src.spawner import inject_session_start_hook
        (tmp_path / ".claude").mkdir()
        (tmp_path / ".claude" / "settings.local.json").write_text(json.dumps([1, 2, 3]))
        inject_session_start_hook(str(tmp_path), self.HOOK, self.DRAIN)
        data = json.loads((tmp_path / ".claude" / "settings.local.json").read_text())
        assert isinstance(data, dict)
        assert data["hooks"]["SessionStart"][0]["hooks"][0]["command"] == self.HOOK

    def test_skips_non_dict_entries_in_existing_event(self, tmp_path):
        """Malformed entries (e.g., a bare string where a dict was expected) are silently skipped."""
        from src.spawner import inject_session_start_hook
        (tmp_path / ".claude").mkdir()
        (tmp_path / ".claude" / "settings.local.json").write_text(json.dumps({
            "hooks": {
                "UserPromptSubmit": ["bogus-string-entry"],
            }
        }))
        inject_session_start_hook(str(tmp_path), self.HOOK, self.DRAIN)
        data = json.loads((tmp_path / ".claude" / "settings.local.json").read_text())
        ups_cmds = [h["command"] for h in data["hooks"]["UserPromptSubmit"][0]["hooks"]]
        assert ups_cmds == [self.DRAIN]

    def test_normalizes_hooks_key_when_list(self, tmp_path):
        from src.spawner import inject_session_start_hook
        (tmp_path / ".claude").mkdir()
        (tmp_path / ".claude" / "settings.local.json").write_text(json.dumps({"hooks": []}))
        inject_session_start_hook(str(tmp_path), self.HOOK, self.DRAIN)
        data = json.loads((tmp_path / ".claude" / "settings.local.json").read_text())
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
        (tmp_path / ".claude" / "settings.local.json").write_text(json.dumps(existing))
        inject_session_start_hook(str(tmp_path), self.HOOK, self.DRAIN)
        data = json.loads((tmp_path / ".claude" / "settings.local.json").read_text())
        stop_entries = data["hooks"]["Stop"]
        our_cmds = [h["command"] for h in stop_entries[0]["hooks"]]
        from src.agent_bootstrap import STOP_HOOK_SCRIPT
        assert our_cmds == [self.DRAIN, STOP_HOOK_SCRIPT]
        # The "junk" entry is dropped; the "real" entry survives with its
        # matcher and command intact.
        surviving_matchers = {e.get("matcher") for e in stop_entries[1:]}
        assert "junk" not in surviving_matchers
        assert "real" in surviving_matchers
