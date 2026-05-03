"""Build kind=progress envelopes for notify_user / chat_notify.

``meta.progress = {current?, total?, percent?, label?}`` — see the
``progress`` arg in chat_notify's MCP schema. Invalid entries are
silently dropped (drift-tolerant). Plain-origin tasks stay plain text."""
from src.chat_db import ChatDB
from src.origin_envelope import wrap_if_json_origin

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
    """Plain text unless ``progress`` filters non-empty AND task is JSON-origin."""
    filtered = filter_progress(progress)
    if not filtered:
        return message, ""
    return wrap_if_json_origin(
        db, "progress", message, task_id, progress=filtered,
    )
