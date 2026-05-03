"""Shared "branch on origin_content_type" wrapper for outbound JSON envelopes.

Used by progress_envelope and question_envelope (and any future kind=X
emission) to decide whether to wrap a message as a JSON envelope or
forward it as plain text. Plain-origin tasks always stay plain text —
the same pattern task_notifier and status_envelope follow inline."""
from src.chat_db import ChatDB
from src.json_envelope import CONTENT_TYPE as _JSON_CT, build_envelope


def wrap_if_json_origin(
    db: ChatDB, kind: str, message: str, task_id: int | None,
    **envelope_kwargs,
) -> tuple[str, str]:
    """Wrap as JSON envelope iff the task originated from one.

    Returns ``(body, content_type)``: a kind=<kind> envelope when
    ``task_id`` resolves to a JSON-origin task, else ``(message, "")``.
    ``envelope_kwargs`` are forwarded to ``build_envelope`` for the
    kind-specific meta fields (progress, suggested_replies, etc.)."""
    if task_id is None:
        return message, ""
    row = db._conn.execute(  # noqa: SLF001
        "SELECT origin_content_type FROM tasks WHERE id=?", (task_id,),
    ).fetchone()
    if not row or (row["origin_content_type"] or "") != _JSON_CT:
        return message, ""
    return build_envelope(
        kind, body=message, task_id=task_id, **envelope_kwargs,
    ), _JSON_CT
