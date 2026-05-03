"""Central validator for bus agent names.

A single source of truth for the regex governing how agent names look
across the bus — the SessionStart hook, the spawner, and the proc-scan
all read CLAUDE_AGENT_NAME and must agree on what's accepted.
"""
import re
import sys

_AGENT_NAME_RE = re.compile(r"^agent-[a-z0-9][a-z0-9_-]{0,57}$")


def validated_agent_name(raw: str | None, fallback: str) -> str:
    """Return ``raw`` if it's a valid agent name; otherwise ``fallback``.

    Empty / None returns ``fallback`` silently. A non-empty but malformed
    value emits a stderr warning so misconfiguration is visible without
    breaking the session.
    """
    if not raw:
        return fallback
    if _AGENT_NAME_RE.fullmatch(raw):
        return raw
    print(
        f"validated_agent_name: rejecting invalid name {raw!r} — "
        f"falling back to {fallback!r}",
        file=sys.stderr,
    )
    return fallback
