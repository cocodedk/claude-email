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


class ProjectNotFound(ValueError):
    """Raised when a requested project path is missing. Keeps ValueError
    compatibility so existing ``except ValueError`` catches still fire."""


class ProjectOutsideBase(ValueError):
    """Raised when a requested project path lies outside the allowed
    base directory — an access-control failure, not a missing-dir."""


def make_error(code: str, message: str, *, hint: str | None = None) -> dict[str, Any]:
    """Build an error payload with ``retryable`` auto-filled from the
    enum. Unknown codes raise so typos fail loudly in tests.
    """
    if code not in ERROR_CODES:
        raise ValueError(f"unknown error code {code!r}; add to ERROR_CODES")
    payload: dict[str, Any] = {
        "code": code,
        "message": message,
        "retryable": ERROR_CODES[code],
    }
    if hint:
        payload["hint"] = hint
    return payload


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
