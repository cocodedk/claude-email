"""Hook-merge primitives for .claude/settings.json.

Extracted from agent_bootstrap.py to keep that module under the 200-line
cap. The merge logic is not used outside the bootstrap path; it lives
here only so the file budget stays clean.
"""
import os

_OUR_SCRIPT_BASENAMES = {
    "chat-session-start-hook.sh",
    "chat-drain-inbox.py",
    "chat-precompact-hook.py",
}


def _is_ours(command: str) -> bool:
    """A hook command is claude-email's if its basename matches one of
    our known scripts. Prefix-based discrimination would mis-tag third-
    party paths that happen to live under a similar root, so match by
    basename instead.
    """
    return os.path.basename(command) in _OUR_SCRIPT_BASENAMES


def _merge_hook_event(
    hooks: dict, event: str, matcher: str, our_commands: list[str],
) -> None:
    """Ensure `event` has our commands while preserving every third-party
    entry verbatim (matcher + remaining hooks).

    Stale paths from a prior install layout are dropped (recognized via
    _is_ours) while genuine third-party hooks survive. Each third-party
    entry keeps its own matcher and any remaining hooks, so installing
    into a project with a custom Stop matcher (for example) does not
    collapse it into our generic block.
    """
    entries = hooks.get(event)
    if not isinstance(entries, list):
        entries = []
    kept_entries: list[dict] = []
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        entry_hooks = entry.get("hooks")
        if not isinstance(entry_hooks, list):
            continue
        kept_hooks = [
            h for h in entry_hooks
            if not (
                isinstance(h, dict)
                and h.get("type") == "command"
                and _is_ours(h.get("command", ""))
            )
        ]
        if kept_hooks:
            kept_entry = dict(entry)
            kept_entry["hooks"] = kept_hooks
            kept_entries.append(kept_entry)
    our_entry = {
        "matcher": matcher,
        "hooks": [{"type": "command", "command": c} for c in our_commands],
    }
    hooks[event] = [our_entry, *kept_entries]
