"""Maintenance routines for ChatDB.

Extracted to keep chat_db.py under the 200-line cap. Methods operate on
the connection (`self._conn`) owned by the host class.
"""
from datetime import datetime, timedelta, timezone


class MaintenanceMixin:
    """Periodic pruning + housekeeping for the bus DB."""

    def cleanup_old(self, days: int = 30) -> dict:
        """Prune delivered/failed messages, old events, and stale outbound
        Message-IDs. Pending rows preserved."""
        cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
        m = self._conn.execute(
            "DELETE FROM messages WHERE status IN ('delivered','failed') AND created_at < ?",
            (cutoff,),
        ).rowcount
        e = self._conn.execute(
            "DELETE FROM events WHERE created_at < ?", (cutoff,)
        ).rowcount
        o = self._conn.execute(
            "DELETE FROM outbound_emails WHERE sent_at < ?", (cutoff,)
        ).rowcount
        self._conn.commit()
        return {"messages": m, "events": e, "outbound_emails": o}
