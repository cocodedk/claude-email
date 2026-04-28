"""Outbound relay + periodic DB cleanup for the chat bus.

Extracted from ``src/chat_handlers.py`` to keep that file under the
200-line cap. chat_handlers.py owns *inbound* email routing; this
module owns *outbound* relay (agent → user emails) and housekeeping.
"""
from __future__ import annotations

import logging
import smtplib
import time

from src.chat_db import ChatDB
from src.email_format import prepend_tag, tag_for_message_type
from src.mailer import send_reply
from src.relay_routing import (
    recipient_for_message, subject_base_for_message, thread_id_for_message,
)

logger = logging.getLogger(__name__)

_PERMANENT_SMTP_ERRORS = (
    smtplib.SMTPRecipientsRefused,
    smtplib.SMTPSenderRefused,
    smtplib.SMTPAuthenticationError,
    smtplib.SMTPHeloError,
    smtplib.SMTPNotSupportedError,
)

_CLEANUP_INTERVAL_SECONDS = 86400
_CLEANUP_RETENTION_DAYS = 30
_last_cleanup_ts = 0.0


def _should_relay(chat_db: ChatDB, msg: dict) -> bool:
    """True iff this outbound message must leave the bus as SMTP.

    1. ``type='ask'`` always relays — chat_ask blocks the agent until
       the user replies, and the reply only arrives by email. Dropping
       an ask silently strands the agent for the full 1h ask-timeout
       (the x-cleaner regression).

    2. Email-origin evidence: ``msg.task_id`` resolves to a task with
       ``origin_message_id``, OR a prior ``user→from_name`` row exists
       (the @agent-command fallback). CLI-session ``chat_notify`` calls
       satisfy neither and stay on the bus so the user isn't surprised
       by unsolicited mail.
    """
    if (msg.get("type") or "") == "ask":
        return True
    task_id = msg.get("task_id")
    if task_id:
        row = chat_db._conn.execute(  # noqa: SLF001 — same-package coupling
            "SELECT origin_message_id FROM tasks WHERE id=?", (task_id,),
        ).fetchone()
        if row and row["origin_message_id"]:
            return True
    row = chat_db._conn.execute(
        "SELECT 1 FROM messages WHERE from_name='user' AND to_name=? LIMIT 1",
        (msg["from_name"],),
    ).fetchone()
    return row is not None


def relay_outbound_messages(config: dict, chat_db: ChatDB) -> None:
    """Pick up pending agent-to-user messages and send them as emails.

    Type ``ask`` always relays (the user has to receive it to reply);
    ``notify`` from a CLI-only agent is drained as 'delivered' without
    SMTP so it doesn't accumulate on the bus or surprise the user with
    unsolicited mail. The Message-ID of every sent email is persisted
    twice — into ``messages.email_message_id`` (legacy thread-match) AND
    ``outbound_emails`` (new unified lookup) — so security accepts user
    replies on this thread without an ``AUTH:`` keyword.

    On permanent SMTP errors, the message is marked failed so it won't
    be retried forever. On transient errors, it stays pending and we
    stop iterating to avoid hammering a broken connection.
    """
    pending = chat_db.get_pending_messages_for("user")
    for msg in pending:
        if not _should_relay(chat_db, msg):
            chat_db.mark_message_delivered(msg["id"])
            logger.debug(
                "Skipping non-email-origin message %d from %s — drained without SMTP",
                msg["id"], msg["from_name"],
            )
            continue
        content_type = msg.get("content_type") or "text/plain"
        subj_base = subject_base_for_message(chat_db, msg)
        subject = subj_base if content_type == "application/json" else prepend_tag(
            subj_base, tag_for_message_type(msg.get("type") or ""),
        )
        thread_id = thread_id_for_message(chat_db, msg)
        try:
            email_msg_id = send_reply(
                smtp_host=config["smtp_host"], smtp_port=config["smtp_port"],
                username=config["username"], password=config["password"],
                to=recipient_for_message(chat_db, msg, config),
                subject=subject, body=msg["body"],
                in_reply_to=thread_id, references=thread_id,
                email_domain=config.get("email_domain", ""),
                content_type=content_type,
            )
        except _PERMANENT_SMTP_ERRORS as exc:
            logger.error("Permanent SMTP error relaying message %d: %s — marking failed", msg["id"], exc)
            chat_db.mark_message_failed(msg["id"])
            continue
        except (smtplib.SMTPException, OSError) as exc:
            logger.warning("Transient SMTP error relaying message %d: %s — will retry", msg["id"], exc)
            return
        if email_msg_id:
            chat_db.set_email_message_id(msg["id"], email_msg_id)
            chat_db.record_outbound_email(
                email_msg_id,
                kind=msg.get("type") or "notify",
                sender_agent=msg["from_name"],
            )
        chat_db.mark_message_delivered(msg["id"])
        logger.info("Relayed message %d from %s to user", msg["id"], msg["from_name"])


def maybe_cleanup_db(chat_db: ChatDB) -> None:
    """Prune old delivered/failed messages + events once per day."""
    global _last_cleanup_ts
    now = time.time()
    if now - _last_cleanup_ts < _CLEANUP_INTERVAL_SECONDS:
        return
    _last_cleanup_ts = now
    try:
        counts = chat_db.cleanup_old(days=_CLEANUP_RETENTION_DAYS)
        if counts["messages"] or counts["events"] or counts.get("outbound_emails"):
            logger.info(
                "DB cleanup: removed %d messages, %d events, %d outbound IDs",
                counts["messages"], counts["events"],
                counts.get("outbound_emails", 0),
            )
    except Exception:
        logger.exception("DB cleanup failed")
