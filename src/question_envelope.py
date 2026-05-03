"""Build kind=question envelopes for ask_user / chat_ask.

``meta.suggested_replies = [...]`` — up to 4 short strings rendered as
tappable chips above the app's composer. Drift-tolerant validator:
trims, drops empty / non-str / too-long entries, dedups, caps at 4.
Plain-origin tasks keep plain text."""
from src.chat_db import ChatDB
from src.origin_envelope import wrap_if_json_origin

_REPLY_MAX_LEN = 30
_REPLY_MAX_COUNT = 4


def filter_suggested_replies(replies) -> list[str]:
    """Trim, drop invalid/too-long, dedup, cap at 4. Returns possibly empty list."""
    if not isinstance(replies, list):
        return []
    out: list[str] = []
    seen: set[str] = set()
    for entry in replies:
        if not isinstance(entry, str):
            continue
        trimmed = entry.strip()
        if not trimmed or len(trimmed) > _REPLY_MAX_LEN or trimmed in seen:
            continue
        out.append(trimmed)
        seen.add(trimmed)
        if len(out) >= _REPLY_MAX_COUNT:
            break
    return out


def build_question_body(
    db: ChatDB, message: str, task_id: int | None, replies,
) -> tuple[str, str]:
    """Plain text unless ``replies`` filters non-empty AND task is JSON-origin."""
    filtered = filter_suggested_replies(replies)
    if not filtered:
        return message, ""
    return wrap_if_json_origin(
        db, "question", message, task_id, suggested_replies=filtered,
    )
