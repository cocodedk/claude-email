"""End-to-end test: inbound envelope v is persisted on the task row
and echoed by every async envelope emitter (result / status / origin
wrap). Regression coverage for the Codex P1 finding: v:1 inbound was
producing v:2 outbound for non-ack envelopes."""
import json
import subprocess
import sys

import pytest

from src.chat_db import ChatDB
from src.origin_envelope import wrap_if_json_origin
from src.status_envelope import emit_status
from src.task_notifier import _json_body
from src.task_queue import TaskQueue


@pytest.fixture
def setup(tmp_path):
    db_path = str(tmp_path / "t.db")
    db = ChatDB(db_path)
    queue = TaskQueue(db_path)
    return db, queue


def test_enqueue_persists_origin_envelope_v(setup):
    db, queue = setup
    tid = queue.enqueue(
        "/x", "do thing", origin_content_type="application/json",
        origin_envelope_v=1,
    )
    row = queue.get(tid)
    assert row["origin_envelope_v"] == 1


def test_enqueue_default_origin_envelope_v_is_null(setup):
    db, queue = setup
    tid = queue.enqueue("/x", "do thing")
    row = queue.get(tid)
    assert row["origin_envelope_v"] is None


def test_enqueue_routed_persists_origin_envelope_v(setup):
    db, queue = setup
    tid = queue.enqueue_routed(
        "/x", "do thing",
        origin_content_type="application/json",
        origin_envelope_v=1,
    )
    row = queue.get(tid)
    assert row["origin_envelope_v"] == 1


def test_result_envelope_echoes_v1_for_v1_origin(setup):
    db, queue = setup
    tid = queue.enqueue(
        "/x", "do x", origin_content_type="application/json",
        origin_envelope_v=1,
    )
    queue.claim_next("/x")
    queue.mark_done(tid)
    row = queue.get(tid)
    body = _json_body(row)
    assert json.loads(body)["v"] == 1


def test_result_envelope_echoes_v2_for_v2_origin(setup):
    db, queue = setup
    tid = queue.enqueue(
        "/x", "do x", origin_content_type="application/json",
        origin_envelope_v=2,
    )
    queue.claim_next("/x")
    queue.mark_done(tid)
    row = queue.get(tid)
    body = _json_body(row)
    assert json.loads(body)["v"] == 2


def test_status_envelope_echoes_v1_for_v1_origin(setup):
    db, queue = setup
    tid = queue.enqueue(
        "/x", "do x", origin_content_type="application/json",
        origin_envelope_v=1,
    )
    sent = []

    def fake_insert(from_name, to_name, body, msg_type, **kw):
        sent.append({"body": body, "msg_type": msg_type})

    db.insert_message = fake_insert  # type: ignore[assignment]
    emit_status(db, task_id=tid, status="stalled")
    assert sent, "expected emit_status to push a message"
    body = json.loads(sent[0]["body"])
    assert body["v"] == 1


def test_origin_wrap_echoes_v1_for_v1_origin(setup):
    db, queue = setup
    tid = queue.enqueue(
        "/x", "do x", origin_content_type="application/json",
        origin_envelope_v=1,
    )
    body, ct = wrap_if_json_origin(db, "progress", "halfway", task_id=tid)
    assert json.loads(body)["v"] == 1
    assert ct == "application/json"


def test_envelope_builder_import_order_clean(tmp_path):
    """Codex P2 regression: importing src.envelope_builder before
    src.json_envelope must not raise. Use a subprocess for a clean
    module cache."""
    r = subprocess.run(
        [sys.executable, "-c", "import src.envelope_builder"],
        cwd="/home/cocodedk/0-projects/claude-email",
        capture_output=True, text=True,
    )
    assert r.returncode == 0, r.stderr


def test_project_listing_import_order_clean(tmp_path):
    """Codex P2 regression: importing chat.project_listing before
    chat.project_tools must not raise."""
    r = subprocess.run(
        [sys.executable, "-c", "import chat.project_listing"],
        cwd="/home/cocodedk/0-projects/claude-email",
        capture_output=True, text=True,
    )
    assert r.returncode == 0, r.stderr
