"""``kind=status`` returns the queue snapshot for a project (running
task + pending list) so the Android Status button renders something
useful instead of a not-implemented error."""
import json

from src.json_handler import handle_json_email

from .conftest import base_config, json_email


class TestStatusKind:
    def test_status_returns_ack_with_queue_snapshot(self, resources, tmp_path, mocker):
        cdb, tq, wm = resources
        cfg = base_config(tmp_path)
        mock_send = mocker.patch("src.json_handler.send_reply", return_value="<r@x>")
        msg = json_email({
            "v": 1, "kind": "status", "project": "p",
            "meta": {"auth": "s3cret"},
        })
        assert handle_json_email(msg, cfg, cdb, tq, wm) is True
        body = json.loads(mock_send.call_args.kwargs["body"])
        assert body["kind"] == "ack"
        assert "running" in body["data"]
        assert "pending" in body["data"]
        assert isinstance(body["data"]["pending"], list)

    def test_status_running_task_appears_in_data(self, resources, tmp_path, mocker):
        cdb, tq, wm = resources
        cfg = base_config(tmp_path)
        proj = str((tmp_path / "p").resolve())
        tid = tq.enqueue(proj, "do work")
        tq.claim_next(proj)
        mock_send = mocker.patch("src.json_handler.send_reply", return_value="<r@x>")
        msg = json_email({
            "v": 1, "kind": "status", "project": "p",
            "meta": {"auth": "s3cret"},
        })
        handle_json_email(msg, cfg, cdb, tq, wm)
        body = json.loads(mock_send.call_args.kwargs["body"])
        assert body["data"]["running"] is not None
        assert body["data"]["running"]["id"] == tid

    def test_status_missing_project_returns_bad_envelope(self, resources, tmp_path, mocker):
        cdb, tq, wm = resources
        cfg = base_config(tmp_path)
        mock_send = mocker.patch("src.json_handler.send_reply", return_value="<r@x>")
        msg = json_email({
            "v": 1, "kind": "status",
            "meta": {"auth": "s3cret"},
        })
        handle_json_email(msg, cfg, cdb, tq, wm)
        body = json.loads(mock_send.call_args.kwargs["body"])
        assert body["kind"] == "error"
        assert body["error"]["code"] == "bad_envelope"

    def test_status_unknown_project_returns_project_not_found(self, resources, tmp_path, mocker):
        cdb, tq, wm = resources
        cfg = base_config(tmp_path)
        mock_send = mocker.patch("src.json_handler.send_reply", return_value="<r@x>")
        msg = json_email({
            "v": 1, "kind": "status", "project": "no-such-project",
            "meta": {"auth": "s3cret"},
        })
        handle_json_email(msg, cfg, cdb, tq, wm)
        body = json.loads(mock_send.call_args.kwargs["body"])
        assert body["kind"] == "error"
        assert body["error"]["code"] == "project_not_found"

    def test_status_echoes_meta_ask_id(self, resources, tmp_path, mocker):
        cdb, tq, wm = resources
        cfg = base_config(tmp_path)
        mock_send = mocker.patch("src.json_handler.send_reply", return_value="<r@x>")
        msg = json_email({
            "v": 1, "kind": "status", "project": "p",
            "meta": {"auth": "s3cret", "ask_id": 99},
        })
        handle_json_email(msg, cfg, cdb, tq, wm)
        body = json.loads(mock_send.call_args.kwargs["body"])
        assert body["meta"]["ask_id"] == 99
