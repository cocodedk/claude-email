"""Outbound message routing helpers — thread continuity + per-universe
recipient selection.

Extracted from chat_handlers.py to keep that file under the 200-line cap
as the relay grew JSON-aware in Phase 8b.
"""


def thread_id_for_message(chat_db, msg: dict) -> str:
    """Return the Message-ID that the outbound email should In-Reply-To.

    Task-linked messages thread to tasks.origin_message_id (the inbound
    command email); everything else falls back to the agent's last
    outbound Message-ID so plain-text conversations stay threaded as
    before.
    """
    task_id = msg.get("task_id")
    if task_id:
        row = chat_db._conn.execute(  # noqa: SLF001 — same-package coupling
            "SELECT origin_message_id FROM tasks WHERE id=?", (task_id,),
        ).fetchone()
        if row and row["origin_message_id"]:
            return row["origin_message_id"]
    return chat_db.get_last_email_message_id_for_agent(msg["from_name"]) or ""


def subject_base_for_message(chat_db, msg: dict) -> str:
    """Subject base for an outbound task-linked message.

    Returns ``tasks.origin_subject`` when set so the inbound identifier
    tag survives the round-trip (symmetric with the ACK path which
    reuses ``original_message.Subject`` in ``send_threaded_reply``).
    Falls back to the legacy ``[from_name] message`` template for
    non-task messages or pre-migration rows with no stored subject.
    """
    fallback = f"[{msg.get('from_name') or 'agent'}] message"
    task_id = msg.get("task_id")
    if not task_id:
        return fallback
    row = chat_db._conn.execute(  # noqa: SLF001 — same-package coupling
        "SELECT origin_subject FROM tasks WHERE id=?", (task_id,),
    ).fetchone()
    if row and row["origin_subject"]:
        return row["origin_subject"]
    return fallback


def recipient_for_message(chat_db, msg: dict, config: dict) -> str:
    """Return the email address to send this outbound to.

    Priority for task-linked messages:
      1. ``tasks.origin_from`` — the actual inbound sender. Only populated
         when the inbound came through the dispatch path. This is how alias
         senders get their replies back instead of the canonical inbox.
      2. The universe whose ``allowed_base`` owns the task's project_path
         — picks the canonical sender for that universe (primary vs test
         isolation).
      3. ``config.authorized_sender`` — primary fallback.
    """
    task_id = msg.get("task_id")
    if task_id:
        row = chat_db._conn.execute(  # noqa: SLF001
            "SELECT project_path, origin_from FROM tasks WHERE id=?", (task_id,),
        ).fetchone()
        if row and row["origin_from"]:
            return row["origin_from"]
        if row and row["project_path"]:
            proj = row["project_path"]
            for u in config.get("universes", []):
                base = getattr(u, "allowed_base", "") or ""
                if base and (proj == base or proj.startswith(base.rstrip("/") + "/")):
                    return u.sender
    return config["authorized_sender"]
