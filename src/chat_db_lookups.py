"""ChatDB lookup _impl bodies for relay / routing / envelope reads.

Extracted from chat_db_impls.py to keep that file under the 200-line cap.
All methods are inner read bodies — they never call .commit()/.rollback()
and never call a public wrapper.
"""


class ChatDbLookupsMixin:
    """Lookup _impl bodies for relay, routing, and envelope operations."""

    def _impl_lookup_task_origin_message_id(self, task_id: int) -> dict | None:
        row = self._conn.execute(
            "SELECT origin_message_id FROM tasks WHERE id=?", (task_id,),
        ).fetchone()
        return dict(row) if row else None

    def _impl_lookup_user_to_agent_message(self, agent_name: str) -> dict | None:
        row = self._conn.execute(
            "SELECT 1 FROM messages WHERE from_name='user' AND to_name=? LIMIT 1",
            (agent_name,),
        ).fetchone()
        return dict(row) if row else None

    def _impl_lookup_origin_envelope_version(self, task_id: int) -> dict | None:
        row = self._conn.execute(
            "SELECT origin_content_type, origin_envelope_v FROM tasks WHERE id=?",
            (task_id,),
        ).fetchone()
        return dict(row) if row else None

    def _impl_lookup_task_origin_subject(self, task_id: int) -> dict | None:
        row = self._conn.execute(
            "SELECT origin_subject FROM tasks WHERE id=?", (task_id,),
        ).fetchone()
        return dict(row) if row else None

    def _impl_lookup_task_routing(self, task_id: int) -> dict | None:
        row = self._conn.execute(
            "SELECT project_path, origin_from FROM tasks WHERE id=?", (task_id,),
        ).fetchone()
        return dict(row) if row else None

    def _impl_lookup_task_status_info(self, task_id: int) -> dict | None:
        row = self._conn.execute(
            "SELECT project_path, origin_content_type, origin_envelope_v "
            "FROM tasks WHERE id=?",
            (task_id,),
        ).fetchone()
        return dict(row) if row else None
