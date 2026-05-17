"""Row-redaction helpers for the task queue.

Extracted from src/task_queue.py to keep that file under the 200-line
cap. dispatch_token is a bearer token — knowing it lets a caller
inject their own enqueue into the email-router's correlation window,
so it must never leave the DB layer.
"""

_REDACT_FROM_PUBLIC = ("dispatch_token",)


def public_row(row: dict) -> dict:
    """Drop bearer-token columns from a task row before it leaves the DB layer."""
    return {k: v for k, v in row.items() if k not in _REDACT_FROM_PUBLIC}
