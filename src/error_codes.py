"""Stable error-code table + helpers for the JSON envelope protocol.

Every `kind=error` envelope carries a `code` from this table. The client
uses the code to drive UI decisions (retry affordance, Settings links,
etc.) — never by regex-matching the human `message`. The boolean value
is the retryable default: ``True`` = safe to auto-retry or show a Retry
button, ``False`` = requires user action.

Additive fields may be introduced later (e.g. ``retry_after_seconds`` on
``rate_limited``); clients MUST ignore unknown fields and treat unknown
codes as ``internal``.
"""
from typing import Any


ERROR_CODES: dict[str, bool] = {
    "bad_envelope":      False,
    "unknown_kind":      False,
    "unauthorized":      False,
    "forbidden":         False,
    "project_not_found": False,
    "invalid_state":     False,
    "not_implemented":   False,
    "rate_limited":      True,
    "internal":          True,
}

# A hint per code so every error envelope is informative by default — the
# user-facing principle is "an error flag has no value; every error must
# tell the user what to do next." Per-call ``make_error(..., hint=...)``
# overrides when context-specific guidance is sharper than the default.
DEFAULT_HINTS: dict[str, str] = {
    "bad_envelope":      "The client sent a malformed envelope. Update the app or report the message-id to the maintainer.",
    "unknown_kind":      "The client sent an envelope kind this server doesn't recognise. Update the client.",
    "unauthorized":      "Open Settings and re-enter the shared secret.",
    "forbidden":         "The requested path is outside the configured CLAUDE_CWD. Check the project name in Settings.",
    "project_not_found": "Check the project name in Settings.",
    "invalid_state":     "The requested operation can't run in the current state. Try again or check status.",
    "not_implemented":   "This feature isn't supported by the running claude-email yet. Update or wait for a release.",
    "rate_limited":      "Too many requests in a short window. Retry after a brief pause.",
    "internal":          "Server error — restart claude-email (`systemctl --user restart claude-email`) and re-send.",
}


class ProjectNotFound(ValueError):
    """Raised when a requested project path is missing. Keeps ValueError
    compatibility so existing ``except ValueError`` catches still fire."""


class ProjectOutsideBase(ValueError):
    """Raised when a requested project path lies outside the allowed
    base directory — an access-control failure, not a missing-dir."""


def make_error(code: str, message: str, *, hint: str | None = None) -> dict[str, Any]:
    """Build an error payload with ``retryable`` and a default ``hint``
    auto-filled from the tables. The ``hint`` arg overrides the default
    when context warrants. Unknown codes raise so typos fail loudly.
    """
    if code not in ERROR_CODES:
        raise ValueError(f"unknown error code {code!r}; add to ERROR_CODES")
    return {
        "code": code,
        "message": message,
        "retryable": ERROR_CODES[code],
        "hint": hint or DEFAULT_HINTS[code],
    }


def error_result_from_exc(exc: Exception) -> dict[str, str]:
    """Map a project-resolution exception to a ``{error, error_code}``
    result dict. Kept alongside the code table so call sites have a
    single import for all error-shaping.
    """
    if isinstance(exc, ProjectNotFound):
        code = "project_not_found"
    elif isinstance(exc, ProjectOutsideBase):
        code = "forbidden"
    else:
        code = "internal"
    return {"error": str(exc), "error_code": code}
