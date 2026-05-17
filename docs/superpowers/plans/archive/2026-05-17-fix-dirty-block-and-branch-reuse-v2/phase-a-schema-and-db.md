# Phase A — Schema + DB primitives

Three tasks. Each ends with a green pytest run and a commit.

- **A.1** Extract `OutboundEmailsMixin` (move-only refactor) so `chat_db.py` has headroom for new outbound fields.
- **A.2** Add `tasks.mutates_repo` and `outbound_emails.task_id` to `SCHEMA` and `MIGRATIONS`.
- **A.3** Extend `record_outbound_email` to accept `task_id`; switch `ON CONFLICT` to `DO UPDATE` so a later non-NULL `task_id` is preserved.

Phase A leaves no behavioral change visible outside the storage layer. Phases B–G consume the new columns.

---

## Task A.1: Extract `OutboundEmailsMixin`

**Files:**
- Create: `src/outbound_emails_store.py`
- Modify: `src/chat_db.py:147-174` (remove these two methods)
- Modify: `src/chat_db.py:5-12` (add mixin import); `src/chat_db.py:20-23` (add to base list)
- Test: `tests/test_outbound_emails_store.py` (create)

This is a pure move so Task A.3 can extend the methods without pushing `chat_db.py` over the 200-line cap.

- [ ] **Step 1: Write the failing test**

`tests/test_outbound_emails_store.py`:

```python
"""ChatDB inherits OutboundEmailsMixin — moving these two methods out of
chat_db.py keeps the host file under the 200-line cap. This test pins
the public surface so the move is verifiably behavior-preserving."""
from src.chat_db import ChatDB
from src.outbound_emails_store import OutboundEmailsMixin


def test_chatdb_inherits_outbound_mixin():
    assert issubclass(ChatDB, OutboundEmailsMixin)


def test_record_and_find_still_work(tmp_path):
    cdb = ChatDB(str(tmp_path / "x.db"))
    cdb.record_outbound_email("<m@x>", kind="ack", sender_agent="agent-x")
    row = cdb.find_outbound_email("<m@x>")
    assert row["kind"] == "ack"
    assert row["sender_agent"] == "agent-x"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/test_outbound_emails_store.py -v`
Expected: FAIL on `from src.outbound_emails_store import OutboundEmailsMixin` — `ModuleNotFoundError`.

- [ ] **Step 3: Create the mixin file**

`src/outbound_emails_store.py`:

```python
"""Outbound SMTP Message-ID store — extracted from chat_db.py.

Every reply we send (relay, ACK, JSON envelope, CLI-fallback) records
here so a user reply passes ``security.is_authorized`` via the
chat-thread match without an ``AUTH:`` keyword. Lives in its own
mixin so chat_db.py stays under the 200-line cap as task_id and any
future per-outbound metadata land on this table.
"""
from datetime import datetime, timezone


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class OutboundEmailsMixin:
    """Persist + look up SMTP Message-IDs we have sent."""

    def record_outbound_email(
        self, email_message_id: str, *, kind: str, sender_agent: str = "",
    ) -> None:
        if not email_message_id:
            raise ValueError("email_message_id must not be empty")
        self._conn.execute(
            "INSERT INTO outbound_emails "
            "(email_message_id, sent_at, kind, sender_agent) "
            "VALUES (?, ?, ?, ?) ON CONFLICT(email_message_id) DO NOTHING",
            (email_message_id, _now(), kind, sender_agent or None),
        )
        self._conn.commit()

    def find_outbound_email(self, email_message_id: str) -> dict | None:
        if not email_message_id:
            return None
        row = self._conn.execute(
            "SELECT * FROM outbound_emails WHERE email_message_id=?",
            (email_message_id,),
        ).fetchone()
        return dict(row) if row else None
```

Task A.3 will modify both methods. This task only moves them verbatim.

- [ ] **Step 4: Update `src/chat_db.py` imports + base list**

Replace `src/chat_db.py:5-12`:

```python
from src.agent_registry import AgentRegistryMixin
from src.agent_state import AgentStateMixin
from src.chat_errors import AgentNameTaken, AgentProjectTaken
from src.chat_schema import MIGRATIONS as _MIGRATIONS, SCHEMA as _SCHEMA
from src.dashboard_queries import DashboardQueriesMixin
from src.db_maintenance import MaintenanceMixin
from src.outbound_emails_store import OutboundEmailsMixin
from src.wake_session_store import WakeSessionStoreMixin
```

Replace `src/chat_db.py:20-23`:

```python
class ChatDB(
    AgentRegistryMixin, AgentStateMixin, DashboardQueriesMixin,
    MaintenanceMixin, OutboundEmailsMixin, WakeSessionStoreMixin,
):
```

- [ ] **Step 5: Delete `record_outbound_email` + `find_outbound_email` from `src/chat_db.py`**

Delete `src/chat_db.py:147-174` entirely (the two outbound methods + their docstrings).

- [ ] **Step 6: Run tests + line check**

```
.venv/bin/pytest tests/test_outbound_emails_store.py tests/test_outbound_emails.py tests/test_chat_db.py -v
.venv/bin/pytest tests/ -q
scripts/check-line-limit.sh
```

Expected: all pass; no file >200 lines. (Exact test count varies with parametrize expansion; capture the final total in Phase H.14.)

- [ ] **Step 7: Commit**

```bash
git add src/outbound_emails_store.py src/chat_db.py tests/test_outbound_emails_store.py
git commit -m "refactor(chat_db): extract OutboundEmailsMixin to free 200-line headroom"
```

---

## Task A.2: Add `tasks.mutates_repo` + `outbound_emails.task_id`

**Files:**
- Modify: `src/chat_schema.py:41-67` (add `mutates_repo` to tasks)
- Modify: `src/chat_schema.py:75-81` (add `task_id` to outbound_emails)
- Modify: `src/chat_schema.py:85-103` (append ALTER + INDEX migrations)
- Test: `tests/test_chat_schema_migrations.py` (create)

- [ ] **Step 1: Write the failing test**

`tests/test_chat_schema_migrations.py`:

```python
"""Schema migration tests for the dirty-block / branch-reuse fix.

Both columns must be present after ChatDB() construction — fresh-DB
path (SCHEMA) and pre-existing-DB path (MIGRATIONS). NULL is the
default so existing rows stay safety-gated."""
import sqlite3

from src.chat_db import ChatDB


def _columns(conn, table):
    return {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}


class TestFreshDB:
    def test_tasks_has_mutates_repo(self, tmp_path):
        cdb = ChatDB(str(tmp_path / "a.db"))
        assert "mutates_repo" in _columns(cdb._conn, "tasks")

    def test_outbound_emails_has_task_id(self, tmp_path):
        cdb = ChatDB(str(tmp_path / "b.db"))
        assert "task_id" in _columns(cdb._conn, "outbound_emails")

    def test_mutates_repo_defaults_to_null(self, tmp_path):
        cdb = ChatDB(str(tmp_path / "c.db"))
        cdb._conn.execute(
            "INSERT INTO tasks (project_path, body, created_at) "
            "VALUES ('/p', 'x', '2026-05-17T00:00:00+00:00')"
        )
        cdb._conn.commit()
        row = cdb._conn.execute("SELECT mutates_repo FROM tasks LIMIT 1").fetchone()
        assert row["mutates_repo"] is None

    def test_outbound_task_id_index_exists(self, tmp_path):
        cdb = ChatDB(str(tmp_path / "d.db"))
        rows = cdb._conn.execute(
            "SELECT name FROM sqlite_master WHERE type='index' "
            "AND tbl_name='outbound_emails'"
        ).fetchall()
        names = {r["name"] for r in rows}
        assert "outbound_emails_task_id_idx" in names


class TestUpgradeExistingDB:
    """Simulate a deployed DB that pre-dates the new columns."""

    def _make_old_db(self, path):
        conn = sqlite3.connect(path)
        conn.executescript("""
            CREATE TABLE tasks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                project_path TEXT NOT NULL,
                body TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'pending',
                priority INTEGER NOT NULL DEFAULT 0,
                pid INTEGER,
                created_at TEXT NOT NULL
            );
            CREATE TABLE outbound_emails (
                email_message_id TEXT PRIMARY KEY,
                sent_at TEXT NOT NULL,
                kind TEXT NOT NULL,
                sender_agent TEXT
            );
        """)
        conn.commit()
        conn.close()

    def test_migrations_add_new_columns(self, tmp_path):
        path = str(tmp_path / "old.db")
        self._make_old_db(path)
        cdb = ChatDB(path)  # triggers migrations
        assert "mutates_repo" in _columns(cdb._conn, "tasks")
        assert "task_id" in _columns(cdb._conn, "outbound_emails")

    def test_migrations_are_idempotent(self, tmp_path):
        path = str(tmp_path / "old2.db")
        self._make_old_db(path)
        ChatDB(path)  # first migrate
        ChatDB(path)  # second open — must not raise
```

- [ ] **Step 2: Run test to verify failures**

Run: `.venv/bin/pytest tests/test_chat_schema_migrations.py -v`
Expected: FAIL — columns don't exist yet.

- [ ] **Step 3: Update SCHEMA**

In `src/chat_schema.py:41-67`, change the `tasks` block to include `mutates_repo INTEGER` after `branch_name`:

```python
CREATE TABLE IF NOT EXISTS tasks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_path TEXT NOT NULL,
    body TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',
    priority INTEGER NOT NULL DEFAULT 0,
    pid INTEGER,
    branch_name TEXT,
    mutates_repo INTEGER,
    created_at TEXT NOT NULL,
    started_at TEXT,
    completed_at TEXT,
    error_text TEXT,
    output_text TEXT,
    retry_of INTEGER,
    plan_first INTEGER NOT NULL DEFAULT 0,
    origin_content_type TEXT,
    origin_message_id TEXT,
    origin_subject TEXT,
    origin_from TEXT,
    dispatch_token TEXT,
    last_sent_status TEXT,
    origin_envelope_v INTEGER
);
```

In `src/chat_schema.py:75-81`, change the `outbound_emails` block:

```python
CREATE TABLE IF NOT EXISTS outbound_emails (
    email_message_id TEXT PRIMARY KEY,
    sent_at TEXT NOT NULL,
    kind TEXT NOT NULL,
    sender_agent TEXT,
    task_id INTEGER REFERENCES tasks(id)
);
```

- [ ] **Step 4: Append MIGRATIONS (append-only)**

Append at the end of `src/chat_schema.py:85-103`:

```python
    "ALTER TABLE tasks ADD COLUMN mutates_repo INTEGER",
    "ALTER TABLE outbound_emails ADD COLUMN task_id INTEGER REFERENCES tasks(id)",
    "CREATE INDEX IF NOT EXISTS outbound_emails_task_id_idx "
    "ON outbound_emails(task_id) WHERE task_id IS NOT NULL",
```

The `try/except sqlite3.OperationalError: pass` block in `chat_db.py:34-38` already swallows "duplicate column" errors, so idempotency holds.

Note on `REFERENCES`: SQLite permits adding a nullable column with `REFERENCES` in `ALTER TABLE`. This keeps the migrated schema byte-identical to the SCHEMA defined for fresh DBs, satisfying the "SCHEMA + equivalent migration" invariant.

- [ ] **Step 5: Run tests**

```
.venv/bin/pytest tests/test_chat_schema_migrations.py -v
.venv/bin/pytest tests/ -q
```

Expected: all pass. (Exact count varies; capture in Phase H.14.)

- [ ] **Step 6: Commit**

```bash
git add src/chat_schema.py tests/test_chat_schema_migrations.py
git commit -m "feat(schema): add tasks.mutates_repo and outbound_emails.task_id"
```

---

## Task A.3: `record_outbound_email` accepts `task_id` + uses `DO UPDATE`

**Files:**
- Modify: `src/outbound_emails_store.py`
- Modify: `tests/test_outbound_emails.py` (add 4 cases)

The reviewer's catch on this is load-bearing: when the relay does `set_email_message_id` and then `record_outbound_email`, or when a duplicate-send happens, `ON CONFLICT DO NOTHING` would silently drop a later non-NULL `task_id`. `DO UPDATE SET task_id = COALESCE(...)` keeps the first non-NULL value seen.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_outbound_emails.py` inside `class TestRecordOutbound`:

```python
    def test_record_with_task_id(self, cdb):
        cdb.record_outbound_email(
            "<t1@x>", kind="result", sender_agent="agent-y", task_id=42,
        )
        row = cdb.find_outbound_email("<t1@x>")
        assert row["task_id"] == 42

    def test_record_without_task_id_stores_null(self, cdb):
        cdb.record_outbound_email("<t2@x>", kind="ack")
        row = cdb.find_outbound_email("<t2@x>")
        assert row["task_id"] is None

    def test_later_task_id_fills_null(self, cdb):
        """Common case: relay records the ID first (no task_id), then a
        follow-up code path records again with the real task_id. The
        DO UPDATE / COALESCE pair must let the non-NULL win."""
        cdb.record_outbound_email("<t3@x>", kind="ack")  # task_id=NULL
        cdb.record_outbound_email("<t3@x>", kind="ack", task_id=99)
        assert cdb.find_outbound_email("<t3@x>")["task_id"] == 99

    def test_existing_task_id_is_not_overwritten(self, cdb):
        """If a row already has task_id=A, a later record with task_id=B
        must keep A. Same email_message_id should never legitimately
        belong to two tasks; keep the first."""
        cdb.record_outbound_email("<t4@x>", kind="ack", task_id=5)
        cdb.record_outbound_email("<t4@x>", kind="ack", task_id=7)
        assert cdb.find_outbound_email("<t4@x>")["task_id"] == 5
```

- [ ] **Step 2: Run tests to verify failures**

Run: `.venv/bin/pytest tests/test_outbound_emails.py::TestRecordOutbound -v`
Expected: FAIL — `TypeError: record_outbound_email() got an unexpected keyword argument 'task_id'`.

- [ ] **Step 3: Extend `record_outbound_email`**

Replace the method in `src/outbound_emails_store.py`:

```python
    def record_outbound_email(
        self, email_message_id: str, *, kind: str, sender_agent: str = "",
        task_id: int | None = None,
    ) -> None:
        if not email_message_id:
            raise ValueError("email_message_id must not be empty")
        self._conn.execute(
            "INSERT INTO outbound_emails "
            "(email_message_id, sent_at, kind, sender_agent, task_id) "
            "VALUES (?, ?, ?, ?, ?) "
            "ON CONFLICT(email_message_id) DO UPDATE SET "
            "task_id = COALESCE(outbound_emails.task_id, excluded.task_id)",
            (email_message_id, _now(), kind, sender_agent or None, task_id),
        )
        self._conn.commit()
```

`find_outbound_email` needs no change — `SELECT *` surfaces the new column once Task A.2 has run.

`kind` and `sender_agent` are intentionally NOT updated by the conflict path. The first send establishes the row's "shape"; later sends only need to fill in metadata that was unknown earlier (currently just `task_id`).

- [ ] **Step 4: Run tests**

```
.venv/bin/pytest tests/test_outbound_emails.py -v
.venv/bin/pytest tests/ -q
```

Expected: all pass. (Exact count varies; capture in Phase H.14.)

- [ ] **Step 5: Commit**

```bash
git add src/outbound_emails_store.py tests/test_outbound_emails.py
git commit -m "feat(outbound): record_outbound_email accepts task_id (DO UPDATE preserves it)"
```
