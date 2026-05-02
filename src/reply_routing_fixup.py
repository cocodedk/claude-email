"""Stamp ``tasks.origin_*`` after an email-driven dispatch.

``chat_enqueue_task``'s MCP schema deliberately refuses origin_*
arguments — trusting LLM-supplied values would let any bus client
hijack a task's reply address. This module is the trusted backstop:

  1. ``main.process_email`` mints a per-dispatch UUID and exports it
     as ``$CLAUDE_EMAIL_DISPATCH_TOKEN`` to the LLM-router subprocess.
  2. The router's system prompt instructs it to pass the env var as
     ``dispatch_token`` on every chat_enqueue_task call.
  3. After execute_command returns, ``run_router_with_fixup`` finds
     every task carrying the token and stamps origin_from /
     origin_message_id / origin_subject from the trusted inbound
     headers (so the relay can email the [Update] back to the actual
     sender, not the canonical AUTHORIZED_SENDER).

Token-based correlation is unambiguous: concurrent enqueues from
other MCP clients won't carry this dispatch's token, so they're left
untouched. An email that enqueues two tasks stamps both. If the LLM
forgets to pass the token (or only answers in plain text), the fixup
is a no-op — no mis-routing.
"""
import logging
import sqlite3

logger = logging.getLogger(__name__)


def stamp_origin_by_token(
    *, db_path: str, dispatch_token: str, reply_to: str,
    origin_message_id: str = "", origin_subject: str = "",
) -> int:
    """Stamp origin_from / origin_message_id / origin_subject on every
    task carrying ``dispatch_token``.

    Returns the number of rows updated. Silent no-op when the token is
    empty or no rows match (e.g. the LLM only answered in plain text).
    Tasks with origin_from already set are skipped — never overwrite a
    prior stamp.
    """
    if not reply_to or not dispatch_token:
        return 0
    conn = sqlite3.connect(db_path)
    try:
        cur = conn.execute(
            "UPDATE tasks SET origin_from=?, "
            "  origin_message_id=COALESCE(origin_message_id, ?), "
            "  origin_subject=COALESCE(origin_subject, ?) "
            "WHERE dispatch_token = ? AND origin_from IS NULL",
            (
                reply_to, origin_message_id or None, origin_subject or None,
                dispatch_token,
            ),
        )
        conn.commit()
        return cur.rowcount
    finally:
        conn.close()


def run_router_with_fixup(
    execute_fn, *, db_path: str, dispatch_token: str, reply_to: str,
    origin_message_id: str = "", origin_subject: str = "",
):
    """Run the LLM router, then stamp every task carrying
    ``dispatch_token`` with origin_from / message-id / subject.

    The token is the unambiguous correlation: any task the router
    enqueued for this email carries it (the prompt instructs the LLM
    to read it from $CLAUDE_EMAIL_DISPATCH_TOKEN); concurrent
    enqueues from other MCP clients won't, so they're left alone.
    Stamp failures are swallowed so a buggy fixup never poisons the
    result reply.
    """
    output = execute_fn()
    if reply_to and db_path and dispatch_token:
        try:
            stamped = stamp_origin_by_token(
                db_path=db_path, dispatch_token=dispatch_token,
                reply_to=reply_to,
                origin_message_id=origin_message_id,
                origin_subject=origin_subject,
            )
            if stamped:
                logger.info(
                    "stamped origin metadata (from=%s) on %d task(s) "
                    "for dispatch %s", reply_to, stamped, dispatch_token,
                )
        except Exception:
            logger.exception("origin fixup failed")
    return output
