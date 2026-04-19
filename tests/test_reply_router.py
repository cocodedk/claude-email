"""Tests for src/reply_router.py — reply sub-classification + apply_reply."""
import pytest
from src.chat_db import ChatDB
from src.reply_router import classify_reply, apply_reply


@pytest.fixture
def db(tmp_path):
    return ChatDB(str(tmp_path / "db"))


def _seed_agent(db, name, project_path):
    db.register_agent(name, project_path)


def _seed_message(db, from_name, to_name, body, msg_type):
    return db.insert_message(from_name, to_name, body, msg_type)


class TestClassifyReply:
    def test_reply_to_ask_returns_ask_route(self, db, tmp_path):
        _seed_agent(db, "agent-foo", str(tmp_path))
        original = _seed_message(db, "agent-foo", "user", "want X?", "ask")
        decision = classify_reply(db, "agent-foo", original["id"], str(tmp_path))
        assert decision.route == "ask"

    def test_reply_to_notify_with_project_returns_project_route(self, db, tmp_path):
        (tmp_path / "p").mkdir()
        proj = str((tmp_path / "p").resolve())
        _seed_agent(db, "agent-p", proj)
        original = _seed_message(db, "agent-p", "user", "I did X", "notify")
        decision = classify_reply(db, "agent-p", original["id"], str(tmp_path))
        assert decision.route == "project"
        assert decision.project_path == proj

    def test_no_agent_returns_bus(self, db, tmp_path):
        original = _seed_message(db, "ghost", "user", "hi", "notify")
        decision = classify_reply(db, "ghost", original["id"], str(tmp_path))
        assert decision.route == "bus"

    def test_project_outside_base_returns_bus(self, db, tmp_path):
        outside = tmp_path.parent / "outside-reply"
        outside.mkdir(exist_ok=True)
        try:
            _seed_agent(db, "agent-x", str(outside))
            original = _seed_message(db, "agent-x", "user", "hi", "notify")
            decision = classify_reply(db, "agent-x", original["id"], str(tmp_path))
            assert decision.route == "bus"
        finally:
            outside.rmdir()

    def test_empty_allowed_base_returns_bus(self, db, tmp_path):
        _seed_agent(db, "agent-x", str(tmp_path))
        original = _seed_message(db, "agent-x", "user", "hi", "notify")
        decision = classify_reply(db, "agent-x", original["id"], "")
        assert decision.route == "bus"

    def test_unknown_project_path_returns_bus(self, db, tmp_path):
        _seed_agent(db, "agent-x", "/does/not/exist")
        original = _seed_message(db, "agent-x", "user", "hi", "notify")
        decision = classify_reply(db, "agent-x", original["id"], str(tmp_path))
        assert decision.route == "bus"

    def test_missing_original_message_returns_bus(self, db, tmp_path):
        _seed_agent(db, "agent-x", str(tmp_path))
        decision = classify_reply(db, "agent-x", 9999, str(tmp_path))
        # No original record → falls through to project if agent valid
        assert decision.route in {"project", "bus"}


class _FakeWorkerManager:
    def __init__(self, pid=123):
        self.pid = pid
        self.calls = []

    def ensure_worker(self, path):
        self.calls.append(path)
        return self.pid


class _FakeTaskQueue:
    def __init__(self):
        self.enqueued = []

    def enqueue(self, path, body, priority=0):
        self.enqueued.append((path, body, priority))
        return 42


class TestApplyReply:
    def test_project_reply_enqueues_and_acks(self, db, tmp_path):
        (tmp_path / "p").mkdir()
        proj = str((tmp_path / "p").resolve())
        _seed_agent(db, "agent-p", proj)
        original = _seed_message(db, "agent-p", "user", "done", "notify")
        tq = _FakeTaskQueue()
        wm = _FakeWorkerManager(pid=555)
        ack, _tag = apply_reply(
            db, tq, wm,
            agent_name="agent-p", original_message_id=original["id"],
            body="also add docs", allowed_base=str(tmp_path),
        )
        assert "#42" in ack and "555" in ack
        assert tq.enqueued == [(proj, "also add docs", 0)]
        assert wm.calls == [proj]

    def test_ask_reply_goes_to_bus(self, db, tmp_path):
        _seed_agent(db, "agent-x", str(tmp_path))
        original = _seed_message(db, "agent-x", "user", "continue?", "ask")
        ack, _tag = apply_reply(
            db, _FakeTaskQueue(), _FakeWorkerManager(),
            agent_name="agent-x", original_message_id=original["id"],
            body="yes", allowed_base=str(tmp_path),
        )
        assert "waiting" in ack.lower()

    def test_bus_only_fallback(self, db, tmp_path):
        original = _seed_message(db, "orphan", "user", "hi", "notify")
        ack, _tag = apply_reply(
            db, _FakeTaskQueue(), _FakeWorkerManager(),
            agent_name="orphan", original_message_id=original["id"],
            body="hello", allowed_base=str(tmp_path),
        )
        assert "chat bus" in ack.lower()

    def test_ensure_worker_failure_falls_back_to_bus(self, db, tmp_path):
        (tmp_path / "p").mkdir()
        _seed_agent(db, "agent-p", str((tmp_path / "p").resolve()))
        original = _seed_message(db, "agent-p", "user", "done", "notify")

        class _Failing:
            def ensure_worker(self, path):
                raise ValueError("no path")

        ack, _tag = apply_reply(
            db, _FakeTaskQueue(), _Failing(),
            agent_name="agent-p", original_message_id=original["id"],
            body="hi", allowed_base=str(tmp_path),
        )
        assert "couldn't queue" in ack

    def test_records_reply_in_db(self, db, tmp_path):
        _seed_agent(db, "agent-x", str(tmp_path))
        original = _seed_message(db, "agent-x", "user", "hi", "notify")
        apply_reply(
            db, None, None,
            agent_name="agent-x", original_message_id=original["id"],
            body="audit me", allowed_base="",
        )
        pending = db.get_pending_messages_for("agent-x")
        assert any(m["body"] == "audit me" for m in pending)

    def test_none_task_queue_still_records(self, db, tmp_path):
        _seed_agent(db, "agent-x", str(tmp_path))
        original = _seed_message(db, "agent-x", "user", "hi", "notify")
        ack, _tag = apply_reply(
            db, None, None,
            agent_name="agent-x", original_message_id=original["id"],
            body="x", allowed_base=str(tmp_path),
        )
        assert "bus" in ack.lower()

    def test_path_resolve_oserror_classified_as_bus(self, db, tmp_path, mocker):
        """If Path.resolve raises (rare — permission/kernel), fall back to bus."""
        from src import reply_router
        _seed_agent(db, "agent-x", str(tmp_path))
        original = _seed_message(db, "agent-x", "user", "hi", "notify")
        real_resolve = reply_router.Path.resolve
        def raising_resolve(self, *args, **kwargs):
            if str(self) == str(tmp_path):
                raise OSError("simulated")
            return real_resolve(self, *args, **kwargs)
        mocker.patch.object(reply_router.Path, "resolve", raising_resolve)
        decision = classify_reply(db, "agent-x", original["id"], str(tmp_path))
        assert decision.route == "bus"
