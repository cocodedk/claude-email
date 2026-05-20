"""Parametrized per-event tests for inject_session_start_hook —
Stop and PreCompact share a shape (matcher="", one command pointing at
their dedicated script) so the wiring, stale-path replacement, and
third-party preservation are exercised once across both events."""
import json
import pytest


class TestInjectSessionStartHookEvents:
    HOOK = "/opt/claude-email/scripts/chat-session-start-hook.sh"
    DRAIN = "/opt/claude-email/scripts/chat-drain-inbox.py"
    PRECOMPACT = "/opt/claude-email/scripts/chat-precompact-hook.py"
    STOP_HOOK = "/opt/claude-email/scripts/chat-stop-hook.py"

    # (event, expected_script_attrs, old_stale_path, third_party_matcher, third_party_cmd)
    EVENT_SHAPE_PARAMS = [
        ("Stop", ["DRAIN", "STOP_HOOK"], "/old/install/scripts/chat-drain-inbox.py",
         "stop-third-party", "/opt/other/notify.sh"),
        ("PreCompact", ["PRECOMPACT"], "/old/install/scripts/chat-precompact-hook.py",
         "precompact-third-party", "/opt/other/log.sh"),
    ]

    def _inject(self, project_dir: str, *, drain: str | None = None, precompact: str | None = None):
        from src.spawner import inject_session_start_hook
        inject_session_start_hook(
            project_dir, self.HOOK, drain or self.DRAIN, precompact or self.PRECOMPACT,
            stop_hook_script_path=self.STOP_HOOK,
        )

    @pytest.mark.parametrize(
        "event,script_attrs,_old,_matcher,_cmd", EVENT_SHAPE_PARAMS,
    )
    def test_event_wired_to_its_script(
        self, tmp_path, event, script_attrs, _old, _matcher, _cmd,
    ):
        """Side-effect hook events (Stop, PreCompact) each get their own
        dedicated entry under the empty matcher with commands pointing at
        the right scripts (Stop gets drain + stop-hook; PreCompact gets one)."""
        self._inject(str(tmp_path))
        data = json.loads((tmp_path / ".claude" / "settings.json").read_text())
        entries = data["hooks"][event]
        assert len(entries) == 1
        assert entries[0]["matcher"] == ""
        cmds = [h["command"] for h in entries[0]["hooks"]]
        assert cmds == [getattr(self, a) for a in script_attrs]

    @pytest.mark.parametrize(
        "event,script_attrs,old_path,_matcher,_cmd", EVENT_SHAPE_PARAMS,
    )
    def test_event_replaces_stale_path_on_reinstall(
        self, tmp_path, event, script_attrs, old_path, _matcher, _cmd,
    ):
        from src.spawner import inject_session_start_hook
        if event == "Stop":
            inject_session_start_hook(
                str(tmp_path), self.HOOK, old_path,
                stop_hook_script_path=self.STOP_HOOK,
            )
        else:
            inject_session_start_hook(
                str(tmp_path), self.HOOK, self.DRAIN, old_path,
                stop_hook_script_path=self.STOP_HOOK,
            )
        self._inject(str(tmp_path))
        data = json.loads((tmp_path / ".claude" / "settings.json").read_text())
        cmds = [h["command"] for h in data["hooks"][event][0]["hooks"]]
        assert cmds == [getattr(self, a) for a in script_attrs]
        assert old_path not in cmds

    @pytest.mark.parametrize(
        "event,script_attrs,_old,matcher,cmd", EVENT_SHAPE_PARAMS,
    )
    def test_event_preserves_third_party_hooks(
        self, tmp_path, event, script_attrs, _old, matcher, cmd,
    ):
        """Third-party hook entries keep their own matcher and hooks —
        our scripts land as a separate entry, not merged into theirs."""
        (tmp_path / ".claude").mkdir()
        existing = {
            "hooks": {event: [{"matcher": matcher, "hooks": [
                {"type": "command", "command": cmd},
            ]}]},
        }
        (tmp_path / ".claude" / "settings.json").write_text(json.dumps(existing))
        self._inject(str(tmp_path))
        data = json.loads((tmp_path / ".claude" / "settings.json").read_text())
        entries = data["hooks"][event]
        assert [h["command"] for h in entries[0]["hooks"]] == [
            getattr(self, a) for a in script_attrs
        ]
        kept = next(
            (e for e in entries[1:] if e.get("matcher") == matcher), None,
        )
        assert kept is not None
        assert [h["command"] for h in kept["hooks"]] == [cmd]
