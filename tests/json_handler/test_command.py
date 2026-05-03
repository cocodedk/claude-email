"""``kind=command`` happy path + tool-layer integration."""
import json

from src.json_handler import handle_json_email

from .conftest import base_config, json_email


class TestCommand:
    def test_command_returns_ack(self, resources, tmp_path, mocker):
        cdb, tq, wm = resources
        cfg = base_config(tmp_path)
        mock_send = mocker.patch("src.json_handler.send_reply", return_value="<reply@x>")
        msg = json_email({
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

    def test_command_missing_project_returns_bad_envelope(self, resources, tmp_path, mocker):
        cdb, tq, wm = resources
        cfg = base_config(tmp_path)
        mock_send = mocker.patch("src.json_handler.send_reply", return_value="<r@x>")
        msg = json_email({
            "v": 1, "kind": "command", "body": "x",
            "meta": {"auth": "s3cret"},
        })
        handle_json_email(msg, cfg, cdb, tq, wm)
        body = json.loads(mock_send.call_args.kwargs["body"])
        assert body["error"]["code"] == "bad_envelope"

    def test_unknown_project_returns_project_not_found(self, resources, tmp_path, mocker):
        cdb, tq, wm = resources
        cfg = base_config(tmp_path)
        mock_send = mocker.patch("src.json_handler.send_reply", return_value="<r@x>")
        msg = json_email({
            "v": 1, "kind": "command", "project": "never-made", "body": "x",
            "meta": {"auth": "s3cret"},
        })
        handle_json_email(msg, cfg, cdb, tq, wm)
        body = json.loads(mock_send.call_args.kwargs["body"])
        assert body["error"]["code"] == "project_not_found"

    def test_send_failure_logged_not_raised(self, resources, tmp_path, mocker):
        cdb, tq, wm = resources
        cfg = base_config(tmp_path)
        mocker.patch("src.json_handler.send_reply", side_effect=RuntimeError("smtp down"))
        msg = json_email({
            "v": 1, "kind": "command", "project": "p", "body": "x",
            "meta": {"auth": "s3cret"},
        })
        handle_json_email(msg, cfg, cdb, tq, wm)  # must not raise

    def test_no_auth_required_when_universe_secret_empty(self, resources, tmp_path, mocker):
        cdb, tq, wm = resources
        cfg = base_config(tmp_path, secret="")
        mock_send = mocker.patch("src.json_handler.send_reply", return_value="<r@x>")
        msg = json_email({
            "v": 1, "kind": "command", "project": "p", "body": "x",
            "meta": {},
        })
        handle_json_email(msg, cfg, cdb, tq, wm)
        body = json.loads(mock_send.call_args.kwargs["body"])
        assert body["kind"] == "ack"

    def test_inbound_subject_persisted_as_origin_subject(self, resources, tmp_path, mocker):
        """JSON command path threads inbound Subject into tasks.origin_subject
        so the outbound RESULT email can reuse it (symmetric with ACK)."""
        cdb, tq, wm = resources
        cfg = base_config(tmp_path)
        mocker.patch("src.json_handler.send_reply", return_value="<reply@x>")
        msg = json_email({
            "v": 1, "kind": "command", "project": "p", "body": "add tests",
            "meta": {"auth": "s3cret"},
        })
        msg.replace_header("Subject", "[test-0042] add tests")
        handle_json_email(msg, cfg, cdb, tq, wm)
        running = tq.get(1)
        assert running["origin_subject"] == "[test-0042] add tests"
