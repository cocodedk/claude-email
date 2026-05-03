"""Build kind=progress envelopes for notify_user / chat_notify (B5).

Wire format locked with agent-Claude-Email-App on 2026-05-03:
``meta.progress = {current?, total?, percent?, label?}``. Invalid
entries are silently dropped (drift-tolerant). When filtering empties
the dict, callers fall back to plain text. Plain-origin tasks always
stay plain text — same branch-on-origin pattern as task_notifier."""
from src.chat_db import ChatDB
from src.json_envelope import CONTENT_TYPE as _JSON_CT, build_envelope

_LABEL_MAX = 200


def filter_progress(progress) -> dict:
    """Drop invalid/unknown entries; return the cleaned dict (may be empty).

    Accepts any input type — non-dicts (and None / empty dicts) yield ``{}``
    so callers don't have to type-guard before passing MCP arguments through."""
    if not isinstance(progress, dict):
        return {}
    out: dict = {}
    cur = progress.get("current")
    if isinstance(cur, int) and not isinstance(cur, bool) and cur >= 0:
        out["current"] = cur
    tot = progress.get("total")
    if isinstance(tot, int) and not isinstance(tot, bool) and tot >= 1:
        out["total"] = tot
    pct = progress.get("percent")
    if (
        isinstance(pct, (int, float)) and not isinstance(pct, bool)
        and 0 <= pct <= 100
    ):
        out["percent"] = pct
    label = progress.get("label")
    if isinstance(label, str) and 0 < len(label) <= _LABEL_MAX:
        out["label"] = label
    return out


def build_progress_body(
    db: ChatDB, message: str, task_id: int | None, progress: dict | None,
) -> tuple[str, str]:
    """Decide body + content_type for a notify_user message.

    Returns ``(body, content_type)``. Wraps in a kind=progress JSON
    envelope when ``progress`` filters non-empty AND the task is
    JSON-origin. Otherwise returns ``(message, "")`` for plain text."""
    filtered = filter_progress(progress)
    if not filtered or task_id is None:
        return message, ""
    row = db._conn.execute(  # noqa: SLF001
        "SELECT origin_content_type FROM tasks WHERE id=?", (task_id,),
    ).fetchone()
    if not row or (row["origin_content_type"] or "") != _JSON_CT:
        return message, ""
    return build_envelope(
        "progress", body=message, task_id=task_id, progress=filtered,
    ), _JSON_CT
