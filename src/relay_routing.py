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

    Task-linked messages go back to the sender whose universe owns the
    task's project_path. Non-task messages go to config.authorized_sender
    (the primary sender). This is how test-universe notifications reach
    test@example.com instead of defaulting to user@example.com.
    """
    task_id = msg.get("task_id")
    if task_id:
        row = chat_db._conn.execute(  # noqa: SLF001
            "SELECT project_path FROM tasks WHERE id=?", (task_id,),
        ).fetchone()
        if row and row["project_path"]:
            proj = row["project_path"]
            for u in config.get("universes", []):
                base = getattr(u, "allowed_base", "") or ""
                if base and (proj == base or proj.startswith(base.rstrip("/") + "/")):
                    return u.sender
    return config["authorized_sender"]
