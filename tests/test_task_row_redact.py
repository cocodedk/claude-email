"""Pin the redaction extraction. public_row must strip every key in
_REDACT_FROM_PUBLIC so dispatch_token (a bearer credential) never
leaves the DB layer."""
from src.task_row_redact import _REDACT_FROM_PUBLIC, public_row


def test_redact_set_includes_dispatch_token():
    assert "dispatch_token" in _REDACT_FROM_PUBLIC


def test_public_row_strips_redacted_keys():
    row = {"id": 1, "body": "x", "dispatch_token": "secret"}
    out = public_row(row)
    assert "dispatch_token" not in out
    assert out["id"] == 1
    assert out["body"] == "x"


def test_public_row_passes_through_unredacted_keys():
    row = {"id": 1, "branch_name": "claude/task-1-x", "mutates_repo": 0}
    out = public_row(row)
    assert out == row
