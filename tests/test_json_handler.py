"""Tests for src/json_handler.py — JSON email end-to-end dispatch."""
import email
import email.message
import json
import pytest
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

from src.chat_db import ChatDB
from src.json_envelope import CONTENT_TYPE
from src.json_handler import handle_json_email
from src.task_queue import TaskQueue
from src.universes import Universe
from src.worker_manager import WorkerManager


def _json_email(payload: dict, msg_id: str = "<c1@x>") -> email.message.Message:
    msg = email.message.Message()
    msg.add_header("Content-Type", CONTENT_TYPE)
    msg["Message-ID"] = msg_id
    msg["Subject"] = "app command"
    msg.set_payload(json.dumps(payload))
    return msg


@pytest.fixture
def db_path(tmp_path):
    p = str(tmp_path / "db")
    ChatDB(p)
    return p


@pytest.fixture
def resources(db_path, tmp_path, mocker):
    mocker.patch("src.worker_manager.is_alive", return_value=True)
    mocker.patch("src.worker_manager._find_external_worker_pid", return_value=None)
    proc = mocker.MagicMock(pid=123)
    proc.poll.return_value = None
    mocker.patch("src.worker_manager.subprocess.Popen", return_value=proc)
    (tmp_path / "p").mkdir()
    cdb = ChatDB(db_path)
    tq = TaskQueue(db_path)
    wm = WorkerManager(db_path=db_path, project_root=str(tmp_path), python_bin="/usr/bin/python3")
    return cdb, tq, wm


def _base_config(tmp_path, secret="s3cret"):
    universe = Universe(
        sender="bb@x", allowed_base=str(tmp_path),
        chat_db_path="db", chat_url="",
        mcp_config="/repo/.mcp.json",
        service_name_chat="", shared_secret=secret,
    )
    return {
        "smtp_host": "h", "smtp_port": 465,
        "username": "u", "password": "p",
        "authorized_sender": "bb@x",
        "email_domain": "",
        "_universe": universe,
        "claude_cwd": str(tmp_path),
        "shared_secret": secret,
    }


class TestHandleJsonEmail:
    def test_command_returns_ack(self, resources, tmp_path, mocker):
        cdb, tq, wm = resources
        cfg = _base_config(tmp_path)
        mock_send = mocker.patch("src.json_handler.send_reply", return_value="<reply@x>")
        msg = _json_email({
            "v": 1, "kind": "command", "project": "p", "body": "add tests",
            "meta": {"auth": "s3cret", "client": "x/1.0"},
        })
        assert handle_json_email(msg, cfg, cdb, tq, wm) is True
        kwargs = mock_send.call_args.kwargs
        assert kwargs["content_type"] == "application/json"
        body = json.loads(kwargs["body"])
        assert body["kind"] == "ack"
        assert body["task_id"] >= 1
        assert body["data"]["status"] == "queued"
        assert body["data"]["branch"].startswith("claude/task-")

    def test_bad_envelope_returns_error(self, resources, tmp_path, mocker):
        cdb, tq, wm = resources
        cfg = _base_config(tmp_path)
        mock_send = mocker.patch("src.json_handler.send_reply", return_value="<r@x>")
        msg = _json_email({"v": 99, "kind": "command"})
        handle_json_email(msg, cfg, cdb, tq, wm)
        body = json.loads(mock_send.call_args.kwargs["body"])
        assert body["kind"] == "error"
        assert body["error"]["code"] == "bad_envelope"

    def test_unauthorized_returns_error(self, resources, tmp_path, mocker):
        cdb, tq, wm = resources
        cfg = _base_config(tmp_path, secret="correct")
        mock_send = mocker.patch("src.json_handler.send_reply", return_value="<r@x>")
        msg = _json_email({
            "v": 1, "kind": "command", "project": "p", "body": "x",
            "meta": {"auth": "WRONG"},
        })
        handle_json_email(msg, cfg, cdb, tq, wm)
        body = json.loads(mock_send.call_args.kwargs["body"])
        assert body["kind"] == "error"
        assert body["error"]["code"] == "unauthorized"

    def test_command_missing_project_returns_bad_envelope(self, resources, tmp_path, mocker):
        cdb, tq, wm = resources
        cfg = _base_config(tmp_path)
        mock_send = mocker.patch("src.json_handler.send_reply", return_value="<r@x>")
        msg = _json_email({
            "v": 1, "kind": "command", "body": "x",
            "meta": {"auth": "s3cret"},
        })
        handle_json_email(msg, cfg, cdb, tq, wm)
        body = json.loads(mock_send.call_args.kwargs["body"])
        assert body["error"]["code"] == "bad_envelope"

    def test_unknown_project_returns_project_not_found(self, resources, tmp_path, mocker):
        cdb, tq, wm = resources
        cfg = _base_config(tmp_path)
        mock_send = mocker.patch("src.json_handler.send_reply", return_value="<r@x>")
        msg = _json_email({
            "v": 1, "kind": "command", "project": "never-made", "body": "x",
            "meta": {"auth": "s3cret"},
        })
        handle_json_email(msg, cfg, cdb, tq, wm)
        body = json.loads(mock_send.call_args.kwargs["body"])
        assert body["error"]["code"] == "project_not_found"

    def test_unimplemented_kind_returns_not_implemented(self, resources, tmp_path, mocker):
        """Unwired INBOUND kinds get `not_implemented`, distinct from
        `invalid_state` so the client can render 'not available yet' copy
        instead of a generic state error."""
        cdb, tq, wm = resources
        cfg = _base_config(tmp_path)
        mock_send = mocker.patch("src.json_handler.send_reply", return_value="<r@x>")
        msg = _json_email({
            "v": 1, "kind": "cancel", "project": "p",
            "meta": {"auth": "s3cret"},
        })
        handle_json_email(msg, cfg, cdb, tq, wm)
        body = json.loads(mock_send.call_args.kwargs["body"])
        assert body["error"]["code"] == "not_implemented"
        assert body["error"]["retryable"] is False

    def test_send_failure_logged_not_raised(self, resources, tmp_path, mocker):
        cdb, tq, wm = resources
        cfg = _base_config(tmp_path)
        mocker.patch("src.json_handler.send_reply", side_effect=RuntimeError("smtp down"))
        msg = _json_email({
            "v": 1, "kind": "command", "project": "p", "body": "x",
            "meta": {"auth": "s3cret"},
        })
        handle_json_email(msg, cfg, cdb, tq, wm)  # must not raise

    def test_ack_echoes_meta_ask_id(self, resources, tmp_path, mocker):
        cdb, tq, wm = resources
        cfg = _base_config(tmp_path)
        mock_send = mocker.patch("src.json_handler.send_reply", return_value="<r@x>")
        msg = _json_email({
            "v": 1, "kind": "command", "project": "p", "body": "x",
            "meta": {"auth": "s3cret", "ask_id": 99},
        })
        handle_json_email(msg, cfg, cdb, tq, wm)
        body = json.loads(mock_send.call_args.kwargs["body"])
        assert body["kind"] == "ack"
        assert body["meta"]["ask_id"] == 99

    def test_unauthorized_error_echoes_meta_ask_id(self, resources, tmp_path, mocker):
        cdb, tq, wm = resources
        cfg = _base_config(tmp_path, secret="correct")
        mock_send = mocker.patch("src.json_handler.send_reply", return_value="<r@x>")
        msg = _json_email({
            "v": 1, "kind": "command", "project": "p", "body": "x",
            "meta": {"auth": "WRONG", "ask_id": 13},
        })
        handle_json_email(msg, cfg, cdb, tq, wm)
        body = json.loads(mock_send.call_args.kwargs["body"])
        assert body["error"]["code"] == "unauthorized"
        assert body["meta"]["ask_id"] == 13

    def test_not_implemented_error_echoes_meta_ask_id(self, resources, tmp_path, mocker):
        cdb, tq, wm = resources
        cfg = _base_config(tmp_path)
        mock_send = mocker.patch("src.json_handler.send_reply", return_value="<r@x>")
        msg = _json_email({
            "v": 1, "kind": "cancel", "project": "p",
            "meta": {"auth": "s3cret", "ask_id": 4},
        })
        handle_json_email(msg, cfg, cdb, tq, wm)
        body = json.loads(mock_send.call_args.kwargs["body"])
        assert body["error"]["code"] == "not_implemented"
        assert body["meta"]["ask_id"] == 4

    def test_command_missing_body_error_echoes_meta_ask_id(self, resources, tmp_path, mocker):
        cdb, tq, wm = resources
        cfg = _base_config(tmp_path)
        mock_send = mocker.patch("src.json_handler.send_reply", return_value="<r@x>")
        msg = _json_email({
            "v": 1, "kind": "command", "project": "p",
            "meta": {"auth": "s3cret", "ask_id": 5},
        })
        handle_json_email(msg, cfg, cdb, tq, wm)
        body = json.loads(mock_send.call_args.kwargs["body"])
        assert body["error"]["code"] == "bad_envelope"
        assert body["meta"]["ask_id"] == 5

    def test_project_not_found_error_echoes_meta_ask_id(self, resources, tmp_path, mocker):
        cdb, tq, wm = resources
        cfg = _base_config(tmp_path)
        mock_send = mocker.patch("src.json_handler.send_reply", return_value="<r@x>")
        msg = _json_email({
            "v": 1, "kind": "command", "project": "never-made", "body": "x",
            "meta": {"auth": "s3cret", "ask_id": 77},
        })
        handle_json_email(msg, cfg, cdb, tq, wm)
        body = json.loads(mock_send.call_args.kwargs["body"])
        assert body["error"]["code"] == "project_not_found"
        assert body["meta"]["ask_id"] == 77

    def test_bad_version_error_echoes_ask_id(self, resources, tmp_path, mocker):
        """Valid JSON with meta.ask_id but bad `v` — salvage ask_id so the
        app can unblock the originating chat_ask."""
        cdb, tq, wm = resources
        cfg = _base_config(tmp_path)
        mock_send = mocker.patch("src.json_handler.send_reply", return_value="<r@x>")
        msg = _json_email({"v": 99, "kind": "command", "meta": {"ask_id": 11}})
        handle_json_email(msg, cfg, cdb, tq, wm)
        body = json.loads(mock_send.call_args.kwargs["body"])
        assert body["error"]["code"] == "bad_envelope"
        assert body["meta"]["ask_id"] == 11

    def test_unknown_kind_error_echoes_ask_id(self, resources, tmp_path, mocker):
        cdb, tq, wm = resources
        cfg = _base_config(tmp_path)
        mock_send = mocker.patch("src.json_handler.send_reply", return_value="<r@x>")
        msg = _json_email({"v": 1, "kind": "dance", "meta": {"ask_id": 22}})
        handle_json_email(msg, cfg, cdb, tq, wm)
        body = json.loads(mock_send.call_args.kwargs["body"])
        assert body["error"]["code"] == "unknown_kind"
        assert body["meta"]["ask_id"] == 22

    def test_malformed_json_error_omits_ask_id(self, resources, tmp_path, mocker):
        """Pure JSON-decode failure — no structured data, nothing to salvage."""
        cdb, tq, wm = resources
        cfg = _base_config(tmp_path)
        mock_send = mocker.patch("src.json_handler.send_reply", return_value="<r@x>")
        msg = email.message.Message()
        msg.add_header("Content-Type", CONTENT_TYPE)
        msg["Message-ID"] = "<c@x>"
        msg["Subject"] = "app command"
        msg.set_payload("{not json")
        handle_json_email(msg, cfg, cdb, tq, wm)
        body = json.loads(mock_send.call_args.kwargs["body"])
        assert body["error"]["code"] == "bad_envelope"
        assert "ask_id" not in body["meta"]

    def test_bad_envelope_is_not_retryable(self, resources, tmp_path, mocker):
        cdb, tq, wm = resources
        cfg = _base_config(tmp_path)
        mock_send = mocker.patch("src.json_handler.send_reply", return_value="<r@x>")
        handle_json_email(_json_email({"v": 99, "kind": "command"}), cfg, cdb, tq, wm)
        body = json.loads(mock_send.call_args.kwargs["body"])
        assert body["error"]["retryable"] is False

    def test_unknown_kind_is_not_retryable(self, resources, tmp_path, mocker):
        cdb, tq, wm = resources
        cfg = _base_config(tmp_path)
        mock_send = mocker.patch("src.json_handler.send_reply", return_value="<r@x>")
        handle_json_email(_json_email({"v": 1, "kind": "dance"}), cfg, cdb, tq, wm)
        body = json.loads(mock_send.call_args.kwargs["body"])
        assert body["error"]["code"] == "unknown_kind"
        assert body["error"]["retryable"] is False

    def test_unauthorized_carries_hint(self, resources, tmp_path, mocker):
        """Unauthorized → `Open Settings` affordance on the client; we
        ship a hint string it can render verbatim as the secondary line."""
        cdb, tq, wm = resources
        cfg = _base_config(tmp_path, secret="correct")
        mock_send = mocker.patch("src.json_handler.send_reply", return_value="<r@x>")
        msg = _json_email({
            "v": 1, "kind": "command", "project": "p", "body": "x",
            "meta": {"auth": "WRONG"},
        })
        handle_json_email(msg, cfg, cdb, tq, wm)
        body = json.loads(mock_send.call_args.kwargs["body"])
        assert body["error"]["retryable"] is False
        assert "hint" in body["error"]
        assert body["error"]["hint"]

    def test_project_not_found_carries_hint(self, resources, tmp_path, mocker):
        cdb, tq, wm = resources
        cfg = _base_config(tmp_path)
        mock_send = mocker.patch("src.json_handler.send_reply", return_value="<r@x>")
        msg = _json_email({
            "v": 1, "kind": "command", "project": "never-made", "body": "x",
            "meta": {"auth": "s3cret"},
        })
        handle_json_email(msg, cfg, cdb, tq, wm)
        body = json.loads(mock_send.call_args.kwargs["body"])
        assert body["error"]["code"] == "project_not_found"
        assert body["error"]["retryable"] is False
        assert "hint" in body["error"]

    def test_project_not_found_without_legacy_substring(self, resources, tmp_path, mocker):
        """Regression: json_handler must not rely on substring-matching the
        error prose to pick a code — the code flows through
        result['error_code'] from the tool layer."""
        cdb, tq, wm = resources
        cfg = _base_config(tmp_path)
        # Monkey-patch enqueue_task_tool to return a `project_not_found`
        # error *without* the legacy 'does not exist' substring.
        mocker.patch(
            "src.json_handler.enqueue_task_tool",
            return_value={"error": "arbitrary prose", "error_code": "project_not_found"},
        )
        mock_send = mocker.patch("src.json_handler.send_reply", return_value="<r@x>")
        msg = _json_email({
            "v": 1, "kind": "command", "project": "p", "body": "x",
            "meta": {"auth": "s3cret"},
        })
        handle_json_email(msg, cfg, cdb, tq, wm)
        body = json.loads(mock_send.call_args.kwargs["body"])
        assert body["error"]["code"] == "project_not_found"

    def test_tool_layer_internal_error_maps_to_invalid_state(self, resources, tmp_path, mocker):
        """When enqueue_task_tool returns an error without a known code,
        json_handler defaults to `invalid_state` — never blindly retryable."""
        cdb, tq, wm = resources
        cfg = _base_config(tmp_path)
        mocker.patch(
            "src.json_handler.enqueue_task_tool",
            return_value={"error": "weird", "error_code": "internal"},
        )
        mock_send = mocker.patch("src.json_handler.send_reply", return_value="<r@x>")
        msg = _json_email({
            "v": 1, "kind": "command", "project": "p", "body": "x",
            "meta": {"auth": "s3cret"},
        })
        handle_json_email(msg, cfg, cdb, tq, wm)
        body = json.loads(mock_send.call_args.kwargs["body"])
        assert body["error"]["code"] == "internal"
        assert body["error"]["retryable"] is True

    def test_no_auth_required_when_universe_secret_empty(self, resources, tmp_path, mocker):
        cdb, tq, wm = resources
        cfg = _base_config(tmp_path, secret="")
        mock_send = mocker.patch("src.json_handler.send_reply", return_value="<r@x>")
        msg = _json_email({
            "v": 1, "kind": "command", "project": "p", "body": "x",
            "meta": {},
        })
        handle_json_email(msg, cfg, cdb, tq, wm)
        body = json.loads(mock_send.call_args.kwargs["body"])
        assert body["kind"] == "ack"
