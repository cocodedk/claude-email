"""``kind=cancel`` cancels the running task in a project. ``drain_queue``
extends the cancel to all pending tasks too."""
import json

from src.json_handler import handle_json_email

from .conftest import base_config, json_email


class TestCancelKind:
    def test_cancel_idle_returns_ack_with_idle_status(self, resources, tmp_path, mocker):
        cdb, tq, wm = resources
        cfg = base_config(tmp_path)
        mock_send = mocker.patch("src.json_handler.send_reply", return_value="<r@x>")
        msg = json_email({
            "v": 1, "kind": "cancel", "project": "p",
            "meta": {"auth": "s3cret"},
        })
        assert handle_json_email(msg, cfg, cdb, tq, wm) is True
        body = json.loads(mock_send.call_args.kwargs["body"])
        assert body["kind"] == "ack"
        assert body["data"]["status"] == "idle"

    def test_cancel_running_task(self, resources, tmp_path, mocker):
        cdb, tq, wm = resources
        cfg = base_config(tmp_path)
        proj = str((tmp_path / "p").resolve())
        tid = tq.enqueue(proj, "doomed")
        tq.claim_next(proj)
        tq.set_pid(tid, 12345)
        mocker.patch("src.task_control.os.kill")
        mocker.patch("src.task_control._wait_for_exit", return_value=True)
        mock_send = mocker.patch("src.json_handler.send_reply", return_value="<r@x>")
        msg = json_email({
            "v": 1, "kind": "cancel", "project": "p",
            "meta": {"auth": "s3cret"},
        })
        handle_json_email(msg, cfg, cdb, tq, wm)
        body = json.loads(mock_send.call_args.kwargs["body"])
        assert body["kind"] == "ack"
        assert body["data"]["status"] == "cancelled"
        assert body["data"]["task_id"] == tid

    def test_cancel_with_drain_queue(self, resources, tmp_path, mocker):
        cdb, tq, wm = resources
        cfg = base_config(tmp_path)
        proj = str((tmp_path / "p").resolve())
        tq.enqueue(proj, "pending-1")
        tq.enqueue(proj, "pending-2")
        mock_send = mocker.patch("src.json_handler.send_reply", return_value="<r@x>")
        msg = json_email({
            "v": 1, "kind": "cancel", "project": "p", "drain_queue": True,
            "meta": {"auth": "s3cret"},
        })
        handle_json_email(msg, cfg, cdb, tq, wm)
        body = json.loads(mock_send.call_args.kwargs["body"])
        assert body["kind"] == "ack"
        assert body["data"].get("drained") == 2

    def test_cancel_missing_project_returns_bad_envelope(self, resources, tmp_path, mocker):
        cdb, tq, wm = resources
        cfg = base_config(tmp_path)
        mock_send = mocker.patch("src.json_handler.send_reply", return_value="<r@x>")
        msg = json_email({
            "v": 1, "kind": "cancel",
            "meta": {"auth": "s3cret"},
        })
        handle_json_email(msg, cfg, cdb, tq, wm)
        body = json.loads(mock_send.call_args.kwargs["body"])
        assert body["kind"] == "error"
        assert body["error"]["code"] == "bad_envelope"

    def test_cancel_unknown_project_returns_project_not_found(self, resources, tmp_path, mocker):
        cdb, tq, wm = resources
        cfg = base_config(tmp_path)
        mock_send = mocker.patch("src.json_handler.send_reply", return_value="<r@x>")
        msg = json_email({
            "v": 1, "kind": "cancel", "project": "no-such-project",
            "meta": {"auth": "s3cret"},
        })
        handle_json_email(msg, cfg, cdb, tq, wm)
        body = json.loads(mock_send.call_args.kwargs["body"])
        assert body["kind"] == "error"
        assert body["error"]["code"] == "project_not_found"

    def test_cancel_echoes_meta_ask_id(self, resources, tmp_path, mocker):
        cdb, tq, wm = resources
        cfg = base_config(tmp_path)
        mock_send = mocker.patch("src.json_handler.send_reply", return_value="<r@x>")
        msg = json_email({
            "v": 1, "kind": "cancel", "project": "p",
            "meta": {"auth": "s3cret", "ask_id": 17},
        })
        handle_json_email(msg, cfg, cdb, tq, wm)
        body = json.loads(mock_send.call_args.kwargs["body"])
        assert body["meta"]["ask_id"] == 17
