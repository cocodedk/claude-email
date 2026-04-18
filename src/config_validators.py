"""Validators for env-var config values."""

ALLOWED_CLAUDE_EFFORTS = frozenset({"low", "medium", "high", "xhigh", "max"})


def validated_effort(effort: str | None) -> str | None:
    """Return effort unchanged if valid, None if unset, or raise ValueError."""
    if effort is None:
        return None
    if effort not in ALLOWED_CLAUDE_EFFORTS:
        raise ValueError(
            f"CLAUDE_EFFORT must be one of {sorted(ALLOWED_CLAUDE_EFFORTS)}, got: {effort!r}"
        )
    return effort
