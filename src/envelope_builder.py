"""Outbound envelope construction. Split from json_envelope.py to keep
each module under the 200-line cap; parsing stays there, building
lives here."""
import json
import re as _re
from datetime import datetime, timezone
from typing import Any

from src.json_envelope import V


def build_envelope(
    kind: str, body: str = "", task_id: int | None = None,
    data: dict | None = None, error: dict | None = None,
    ask_id: int | None = None, routed_via: str | None = None,
    progress: dict | None = None, suggested_replies: list | None = None,
    v: int | None = None,
) -> str:
    """Build an outbound envelope as a JSON string.

    `ask_id` echoes the inbound `meta.ask_id` for chat_ask correlation.
    `routed_via` lands as ``meta.routed_via`` (agent | worker).
    `progress` lands as ``meta.progress`` for kind=progress (B5).
    `suggested_replies` lands as ``meta.suggested_replies`` for kind=question (C2).
    `v` overrides the response version (use ``negotiate_v(inbound.v)``
    at the dispatcher to honor legacy clients). Defaults to server ``V``."""
    out: dict[str, Any] = {
        "v": V if v is None else v,
        "kind": kind,
        "body": body,
        "meta": {
            "server": "claude-email/1.0",
            "sent_at": datetime.now(timezone.utc).isoformat(),
        },
    }
    if ask_id is not None:
        out["meta"]["ask_id"] = int(ask_id)
    if routed_via:
        out["meta"]["routed_via"] = routed_via
    if progress:
        out["meta"]["progress"] = progress
    if suggested_replies:
        out["meta"]["suggested_replies"] = suggested_replies
    if task_id is not None:
        out["task_id"] = int(task_id)
    if data:
        out["data"] = data
    if error:
        out["error"] = error
    return json.dumps(out, separators=(",", ":"))


def strip_auth_from_body(body: str, secret: str) -> str:
    """Same guarantee as executor.extract_command's strip_secret — never
    let the auth token live in downstream storage/logs."""
    if not secret:
        return body
    return _re.sub(_re.escape(f"AUTH:{secret}"), "", body)
