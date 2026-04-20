"""Tests for src/json_envelope.py."""
import email
import email.message
import json
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

import pytest
from src.json_envelope import (
    CONTENT_TYPE, Envelope, EnvelopeError,
    build_envelope, is_json_email, parse_envelope,
)


def _json_msg(payload: dict | str) -> email.message.Message:
    msg = email.message.Message()
    msg.add_header("Content-Type", CONTENT_TYPE)
    body = payload if isinstance(payload, str) else json.dumps(payload)
    msg.set_payload(body)
    return msg


def _multipart_with_json(payload: dict) -> email.message.Message:
    msg = MIMEMultipart("alternative")
    msg.attach(MIMEText("fallback plain text", "plain"))
    json_part = email.message.Message()
    json_part.add_header("Content-Type", CONTENT_TYPE)
    json_part.set_payload(json.dumps(payload))
    msg.attach(json_part)
    return msg


class TestIsJsonEmail:
    def test_top_level_json_detected(self):
        assert is_json_email(_json_msg({"v": 1, "kind": "command"})) is True

    def test_multipart_json_part_detected(self):
        assert is_json_email(_multipart_with_json({"v": 1, "kind": "status"})) is True

    def test_plain_text_not_json(self):
        msg = email.message.Message()
        msg.add_header("Content-Type", "text/plain")
        msg.set_payload("hello")
        assert is_json_email(msg) is False

    def test_multipart_without_json_not_json(self):
        msg = MIMEMultipart("alternative")
        msg.attach(MIMEText("plain", "plain"))
        msg.attach(MIMEText("<p>html</p>", "html"))
        assert is_json_email(msg) is False


class TestParseEnvelope:
    def test_minimal_command_envelope(self):
        env = parse_envelope(_json_msg({
            "v": 1, "kind": "command", "body": "hello",
            "meta": {"client": "x/1.0", "auth": "s3cret"},
        }))
        assert env.kind == "command"
        assert env.body == "hello"
        assert env.auth == "s3cret"
        assert env.client == "x/1.0"

    def test_retry_with_task_id(self):
        env = parse_envelope(_json_msg({
            "v": 1, "kind": "retry", "task_id": 42, "new_body": "also add docs",
        }))
        assert env.task_id == 42
        assert env.new_body == "also add docs"

    def test_string_task_id_coerced_to_int(self):
        env = parse_envelope(_json_msg({
            "v": 1, "kind": "reply", "task_id": "99", "body": "x",
        }))
        assert env.task_id == 99

    def test_unparseable_task_id_is_none(self):
        env = parse_envelope(_json_msg({
            "v": 1, "kind": "status", "task_id": "not-a-number",
        }))
        assert env.task_id is None

    def test_multipart_extracted(self):
        env = parse_envelope(_multipart_with_json({
            "v": 1, "kind": "status", "project": "test-01",
        }))
        assert env.kind == "status"
        assert env.project == "test-01"

    def test_version_mismatch_rejected(self):
        with pytest.raises(EnvelopeError) as ei:
            parse_envelope(_json_msg({"v": 2, "kind": "command"}))
        assert ei.value.code == "bad_envelope"

    def test_unknown_kind_rejected(self):
        with pytest.raises(EnvelopeError) as ei:
            parse_envelope(_json_msg({"v": 1, "kind": "dance"}))
        assert ei.value.code == "unknown_kind"

    def test_invalid_json_rejected(self):
        with pytest.raises(EnvelopeError) as ei:
            parse_envelope(_json_msg("{not json"))
        assert ei.value.code == "bad_envelope"

    def test_non_object_rejected(self):
        with pytest.raises(EnvelopeError) as ei:
            parse_envelope(_json_msg('[1,2,3]'))
        assert ei.value.code == "bad_envelope"

    def test_missing_application_json_part(self):
        msg = email.message.Message()
        msg.add_header("Content-Type", "text/plain")
        msg.set_payload("just text")
        with pytest.raises(EnvelopeError) as ei:
            parse_envelope(msg)
        assert ei.value.code == "bad_envelope"


class TestBuildEnvelope:
    def test_ack_carries_task_id_and_data(self):
        out = build_envelope(
            "ack", body="Queued as #42.", task_id=42,
            data={"status": "queued", "branch": "claude/task-42-x"},
        )
        parsed = json.loads(out)
        assert parsed["v"] == 1
        assert parsed["kind"] == "ack"
        assert parsed["task_id"] == 42
        assert parsed["data"]["status"] == "queued"
        assert "meta" in parsed and "sent_at" in parsed["meta"]

    def test_error_envelope(self):
        out = build_envelope(
            "error", body="bad path",
            error={"code": "project_not_found", "message": "tezt-01 — did you mean test-01?"},
        )
        parsed = json.loads(out)
        assert parsed["kind"] == "error"
        assert parsed["error"]["code"] == "project_not_found"
        assert "task_id" not in parsed  # absent when not created

    def test_result_envelope(self):
        out = build_envelope(
            "result", body="Done.", task_id=42,
            data={"status": "done", "branch": "claude/task-42-x"},
        )
        parsed = json.loads(out)
        assert parsed["data"]["status"] == "done"
        assert parsed["task_id"] == 42

    def test_ask_id_echoed_in_meta_when_set(self):
        out = build_envelope("ack", body="ok", task_id=1, ask_id=7)
        parsed = json.loads(out)
        assert parsed["meta"]["ask_id"] == 7

    def test_ask_id_absent_from_meta_when_none(self):
        out = build_envelope("ack", body="ok", task_id=1)
        parsed = json.loads(out)
        assert "ask_id" not in parsed["meta"]

    def test_ask_id_on_error_envelope(self):
        out = build_envelope(
            "error", body="nope",
            error={"code": "unauthorized", "message": "auth fail"},
            ask_id=42,
        )
        parsed = json.loads(out)
        assert parsed["meta"]["ask_id"] == 42
        assert parsed["error"]["code"] == "unauthorized"


class TestStripAuth:
    def test_removes_exact_token(self):
        from src.json_envelope import strip_auth_from_body
        assert strip_auth_from_body("prefix AUTH:xyz suffix", "xyz") == "prefix  suffix"

    def test_empty_secret_is_noop(self):
        from src.json_envelope import strip_auth_from_body
        assert strip_auth_from_body("AUTH:x", "") == "AUTH:x"
