"""Shared SQLite database layer for the claude-chat system."""
import sqlite3
from datetime import datetime, timezone

from src.agent_registry import AgentRegistryMixin
from src.agent_state import AgentStateMixin
from src.chat_db_impls import ChatDbImplsMixin
from src.chat_db_lookups import ChatDbLookupsMixin
from src.chat_db_tx import TransactionMixin
from src.chat_errors import AgentNameTaken, AgentProjectTaken
from src.chat_schema import MIGRATIONS as _MIGRATIONS, SCHEMA as _SCHEMA
from src.dashboard_queries import DashboardQueriesMixin
from src.db_maintenance import MaintenanceMixin
from src.outbound_emails_store import OutboundEmailsMixin
from src.wake_session_store import WakeSessionStoreMixin

__all__ = ["AgentNameTaken", "AgentProjectTaken", "ChatDB"]


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class ChatDB(
    TransactionMixin,
    ChatDbImplsMixin, ChatDbLookupsMixin,
    AgentRegistryMixin, AgentStateMixin, DashboardQueriesMixin,
    MaintenanceMixin, OutboundEmailsMixin, WakeSessionStoreMixin,
):
    """Single entry-point for all chat DB operations."""

    def __init__(self, path: str):
        self.path = path
        self._init_db_lock()
        self._conn = self._open_conn(path)
        self._conn.executescript(_SCHEMA)
        for stmt in _MIGRATIONS:
            try:
                self._conn.execute(stmt)
            except sqlite3.OperationalError:
                pass  # column/index already present
        self._conn.commit()
        self._wake_nudge = None

    def set_wake_nudge(self, event) -> None:
        """Fire event after each insert (writer must be on the event's loop thread)."""
        self._wake_nudge = event

    def _nudge_wake(self) -> None:
        if self._wake_nudge is not None:
            self._wake_nudge.set()

    # ── Messages — public wrappers ────────────────────────

    def insert_message(
        self, from_name: str, to_name: str, body: str,
        msg_type: str, in_reply_to: int | None = None,
        content_type: str = "", task_id: int | None = None,
        in_reply_to_eid: str = "", email_message_id: str = "",
    ) -> dict:
        return self._run_tx(
            self._impl_insert_message,
            from_name, to_name, body, msg_type, in_reply_to,
            content_type, task_id, in_reply_to_eid, email_message_id,
        )

    def get_pending_messages_for(self, to_name: str) -> list[dict]:
        return self._read(self._impl_get_pending_messages_for, to_name)

    def claim_pending_messages_for(self, to_name: str) -> list[dict]:
        """Atomically mark pending messages as delivered and return them.

        SQLite's RETURNING does not guarantee row order, so we sort by id
        before returning to match get_pending_messages_for / _format_context.
        """
        return self._run_tx(self._impl_claim_pending_messages_for, to_name)

    def get_distinct_pending_recipients(self) -> list[str]:
        return self._read(self._impl_get_distinct_pending_recipients)

    def mark_message_delivered(self, msg_id: int) -> None:
        self._run_tx(self._impl_mark_message_delivered, msg_id)

    def mark_message_failed(self, msg_id: int) -> None:
        self._run_tx(self._impl_mark_message_failed, msg_id)

    def recover_failed_messages_for(self, to_name: str) -> int:
        # `failed` is a wake-watcher respawn-loop guard, not a permanent
        # loss signal — register_agent calls this so the agent reclaims
        # abandoned mail when it comes back.
        return self._run_tx(self._impl_recover_failed_messages_for, to_name)

    def set_email_message_id(self, msg_id: int, email_message_id: str) -> None:
        self._run_tx(self._impl_set_email_message_id, msg_id, email_message_id)

    def find_message_by_email_id(self, email_message_id: str) -> dict | None:
        return self._read(self._impl_find_message_by_email_id, email_message_id)

    def get_message(self, msg_id: int) -> dict | None:
        return self._read(self._impl_get_message, msg_id)

    def get_last_email_message_id_for_agent(self, agent_name: str) -> str | None:
        return self._read(
            self._impl_get_last_email_message_id_for_agent, agent_name,
        )

    def get_reply_to_message(self, msg_id: int) -> dict | None:
        return self._read(self._impl_get_reply_to_message, msg_id)

    # ── Status-envelope helpers ────────────────────────────

    def emit_status_message(
        self, *, task_id: int, status: str, agent_name: str,
        body: str, content_type: str,
    ) -> dict | None:
        """Atomic dedup-mark + insert for status envelope traffic.

        Returns the inserted message row, or None if dedup skipped
        (current last_sent_status already equals status for this task).
        """
        return self._run_tx(
            self._impl_emit_status_message,
            task_id, status, agent_name, body, content_type,
        )

    def clear_status_dedup(self, task_id: int) -> None:
        self._run_tx(self._impl_clear_status_dedup, task_id)

    def clear_status_dedup_for_project(self, project_path: str) -> None:
        self._run_tx(self._impl_clear_status_dedup_for_project, project_path)

    # ── Relay / routing / envelope lookups ────────────────

    def lookup_task_origin_message_id(self, task_id: int) -> dict | None:
        return self._read(self._impl_lookup_task_origin_message_id, task_id)

    def lookup_user_to_agent_message(self, agent_name: str) -> dict | None:
        return self._read(self._impl_lookup_user_to_agent_message, agent_name)

    def lookup_origin_envelope_version(self, task_id: int) -> dict | None:
        return self._read(self._impl_lookup_origin_envelope_version, task_id)

    def lookup_task_origin_subject(self, task_id: int) -> dict | None:
        return self._read(self._impl_lookup_task_origin_subject, task_id)

    def lookup_task_routing(self, task_id: int) -> dict | None:
        return self._read(self._impl_lookup_task_routing, task_id)

    def lookup_task_status_info(self, task_id: int) -> dict | None:
        return self._read(self._impl_lookup_task_status_info, task_id)

    # ── Events (internal) ──────────────────────────────────

    def _log_event(self, participant: str, event_type: str, summary: str) -> None:
        """Public entry — used by callers that aren't already inside a tx.
        Inside _run_tx bodies, call self._impl_log_event directly to nest."""
        self._run_tx(self._impl_log_event, participant, event_type, summary)
