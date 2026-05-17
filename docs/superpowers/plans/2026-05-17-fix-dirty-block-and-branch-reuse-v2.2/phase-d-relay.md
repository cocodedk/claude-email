# Phase D — Relay stamps `task_id` on outbound emails

One task. Wires `msg.task_id` through `relay_outbound_messages` to `record_outbound_email`. The schema column (Phase A.2), the method signature (Phase A.3), and the `messages.task_id` source (already populated by `chat_notify` / `chat_ask` callers) are all in place — this task connects them.

Phase A.3's `DO UPDATE / COALESCE` semantics ensure that if `set_email_message_id` ran first (recording the row with task_id=NULL), the relay's later call with the real `task_id` will fill it in.

---

## Task D.7: `relay_outbound_messages` passes `task_id` through

**Files:**
- Modify: `src/chat_relay.py:112-118`
- Modify: `tests/test_chat_relay.py` (add one test class)

`send_threaded_reply` in `chat_handlers.py` is the inbound-ACK path — those messages have no originating task, so its `record_outbound_email` call stays as-is (defaults `task_id` to None).

- [ ] **Step 1: Read existing fixtures**

Run: `.venv/bin/pytest tests/test_chat_relay.py --collect-only -q | head -20`

Inspect `tests/test_chat_relay.py` to find the existing `config` fixture (the SMTP credentials dict every relay test uses). Reuse it in the new test instead of redefining.

- [ ] **Step 2: Write the failing test**

Append to `tests/test_chat_relay.py`:

```python
class TestRelayStampsTaskId:
    """An agent's chat_notify carries msg.task_id through the bus; the
    relay must persist it on outbound_emails so a user reply on this
    thread can be walked back to the originating task (Phase F)."""

    def test_relay_passes_task_id_to_outbound_table(
        self, tmp_path, mocker, config,
    ):
        from src.chat_db import ChatDB
        from src.chat_relay import relay_outbound_messages

        cdb = ChatDB(str(tmp_path / "db"))
        cdb.register_agent("agent-p", str(tmp_path))
        # Seed a task so _should_relay treats the message as email-origin.
        cdb._conn.execute(
            "INSERT INTO tasks (id, project_path, body, created_at, "
            "origin_message_id, origin_from) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (777, str(tmp_path), "x", "2026-05-17T00:00:00+00:00",
             "<orig@x>", "user@example.org"),
        )
        cdb._conn.commit()
        cdb.insert_message(
            "agent-p", "user", "result body", "notify", task_id=777,
        )
        mocker.patch(
            "src.chat_relay.send_reply", return_value="<sent-id@x>",
        )
        relay_outbound_messages(config, cdb)

        row = cdb.find_outbound_email("<sent-id@x>")
        assert row is not None
        assert row["task_id"] == 777

    def test_relay_without_task_id_records_null(
        self, tmp_path, mocker, config,
    ):
        """ask messages from a CLI-only agent (no task) still relay (ask
        always relays) but their outbound row has task_id=NULL."""
        from src.chat_db import ChatDB
        from src.chat_relay import relay_outbound_messages

        cdb = ChatDB(str(tmp_path / "db2"))
        cdb.register_agent("agent-q", str(tmp_path))
        cdb.insert_message(
            "agent-q", "user", "should I continue?", "ask",
        )  # no task_id
        mocker.patch(
            "src.chat_relay.send_reply", return_value="<sent-q@x>",
        )
        relay_outbound_messages(config, cdb)

        row = cdb.find_outbound_email("<sent-q@x>")
        assert row is not None
        assert row["task_id"] is None
```

If `tests/test_chat_relay.py` lacks a module-level `config` fixture, copy the dict from the nearest existing relay test (the file uses one — check the top of the existing `TestRelayOutbound` class).

- [ ] **Step 3: Run test to verify it fails**

Run: `.venv/bin/pytest tests/test_chat_relay.py::TestRelayStampsTaskId -v`
Expected: `test_relay_passes_task_id_to_outbound_table` FAILs on `assert row["task_id"] == 777` (currently NULL).

- [ ] **Step 4: Pass `task_id` in `relay_outbound_messages`**

Edit `src/chat_relay.py:112-118`:

```python
        if email_msg_id:
            chat_db.set_email_message_id(msg["id"], email_msg_id)
            chat_db.record_outbound_email(
                email_msg_id,
                kind=msg.get("type") or "notify",
                sender_agent=msg["from_name"],
                task_id=msg.get("task_id"),
            )
```

That's the entire change. `msg["task_id"]` is already in the row dict because `messages.task_id` is in the schema (`src/chat_schema.py:28`) and `insert_message` accepts the kwarg (`src/chat_db.py:52-71`).

- [ ] **Step 5: Run tests**

```
.venv/bin/pytest tests/test_chat_relay.py -v
.venv/bin/pytest tests/ -q
```

Expected: all PASS. (Exact count varies; capture in Phase H.14.)

- [ ] **Step 6: Commit**

```bash
git add src/chat_relay.py tests/test_chat_relay.py
git commit -m "feat(relay): stamp task_id on outbound emails for reply-walkback"
```
