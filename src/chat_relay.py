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


def relay_outbound_messages(config: dict, chat_db: ChatDB) -> None:
    """Pick up pending agent-to-user messages and send them as emails.

    On permanent SMTP errors, the message is marked failed so it won't be
    retried forever. On transient errors, it stays pending and we stop
    iterating to avoid hammering a broken connection.
    """
    pending = chat_db.get_pending_messages_for("user")
    for msg in pending:
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
        if counts["messages"] or counts["events"]:
            logger.info(
                "DB cleanup: removed %d messages, %d events",
                counts["messages"], counts["events"],
            )
    except Exception:
        logger.exception("DB cleanup failed")
