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

    def test_unimplemented_kind_returns_invalid_state(self, resources, tmp_path, mocker):
        cdb, tq, wm = resources
        cfg = _base_config(tmp_path)
        mock_send = mocker.patch("src.json_handler.send_reply", return_value="<r@x>")
        msg = _json_email({
            "v": 1, "kind": "cancel", "project": "p",
            "meta": {"auth": "s3cret"},
        })
        handle_json_email(msg, cfg, cdb, tq, wm)
        body = json.loads(mock_send.call_args.kwargs["body"])
        assert body["error"]["code"] == "invalid_state"

    def test_send_failure_logged_not_raised(self, resources, tmp_path, mocker):
        cdb, tq, wm = resources
        cfg = _base_config(tmp_path)
        mocker.patch("src.json_handler.send_reply", side_effect=RuntimeError("smtp down"))
        msg = _json_email({
            "v": 1, "kind": "command", "project": "p", "body": "x",
            "meta": {"auth": "s3cret"},
        })
        handle_json_email(msg, cfg, cdb, tq, wm)  # must not raise

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
