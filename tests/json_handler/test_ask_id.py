"""``meta.ask_id`` echo: every reply (ack or error) on a JSON envelope
must carry the ask_id back so the app can unblock the originating
``chat_ask`` call. Salvaged from invalid envelopes when possible."""
import json

from src.json_handler import handle_json_email

from .conftest import base_config, json_email


class TestAskIdEcho:
    def test_ack_echoes_meta_ask_id(self, resources, tmp_path, mocker):
        cdb, tq, wm = resources
        cfg = base_config(tmp_path)
        mock_send = mocker.patch("src.json_handler.send_reply", return_value="<r@x>")
        msg = json_email({
            "v": 1, "kind": "command", "project": "p", "body": "x",
            "meta": {"auth": "s3cret", "ask_id": 99},
        })
        handle_json_email(msg, cfg, cdb, tq, wm)
        body = json.loads(mock_send.call_args.kwargs["body"])
        assert body["kind"] == "ack"
        assert body["meta"]["ask_id"] == 99

    def test_unauthorized_error_echoes_meta_ask_id(self, resources, tmp_path, mocker):
        cdb, tq, wm = resources
        cfg = base_config(tmp_path, secret="correct")
        mock_send = mocker.patch("src.json_handler.send_reply", return_value="<r@x>")
        msg = json_email({
            "v": 1, "kind": "command", "project": "p", "body": "x",
            "meta": {"auth": "WRONG", "ask_id": 13},
        })
        handle_json_email(msg, cfg, cdb, tq, wm)
        body = json.loads(mock_send.call_args.kwargs["body"])
        assert body["error"]["code"] == "unauthorized"
        assert body["meta"]["ask_id"] == 13

    def test_not_implemented_error_echoes_meta_ask_id(self, resources, tmp_path, mocker):
        cdb, tq, wm = resources
        cfg = base_config(tmp_path)
        mock_send = mocker.patch("src.json_handler.send_reply", return_value="<r@x>")
        msg = json_email({
            "v": 1, "kind": "commit", "project": "p",
            "meta": {"auth": "s3cret", "ask_id": 4},
        })
        handle_json_email(msg, cfg, cdb, tq, wm)
        body = json.loads(mock_send.call_args.kwargs["body"])
        assert body["error"]["code"] == "not_implemented"
        assert body["meta"]["ask_id"] == 4

    def test_command_missing_body_error_echoes_meta_ask_id(self, resources, tmp_path, mocker):
        cdb, tq, wm = resources
        cfg = base_config(tmp_path)
        mock_send = mocker.patch("src.json_handler.send_reply", return_value="<r@x>")
        msg = json_email({
            "v": 1, "kind": "command", "project": "p",
            "meta": {"auth": "s3cret", "ask_id": 5},
        })
        handle_json_email(msg, cfg, cdb, tq, wm)
        body = json.loads(mock_send.call_args.kwargs["body"])
        assert body["error"]["code"] == "bad_envelope"
        assert body["meta"]["ask_id"] == 5

    def test_project_not_found_error_echoes_meta_ask_id(self, resources, tmp_path, mocker):
        cdb, tq, wm = resources
        cfg = base_config(tmp_path)
        mock_send = mocker.patch("src.json_handler.send_reply", return_value="<r@x>")
        msg = json_email({
            "v": 1, "kind": "command", "project": "never-made", "body": "x",
            "meta": {"auth": "s3cret", "ask_id": 77},
        })
        handle_json_email(msg, cfg, cdb, tq, wm)
        body = json.loads(mock_send.call_args.kwargs["body"])
        assert body["error"]["code"] == "project_not_found"
        assert body["meta"]["ask_id"] == 77

    def test_bad_version_error_echoes_ask_id(self, resources, tmp_path, mocker):
        """Valid JSON with meta.ask_id but bad ``v`` — salvage ask_id so
        the app can unblock the originating chat_ask."""
        cdb, tq, wm = resources
        cfg = base_config(tmp_path)
        mock_send = mocker.patch("src.json_handler.send_reply", return_value="<r@x>")
        msg = json_email({"v": 99, "kind": "command", "meta": {"ask_id": 11}})
        handle_json_email(msg, cfg, cdb, tq, wm)
        body = json.loads(mock_send.call_args.kwargs["body"])
        assert body["error"]["code"] == "bad_envelope"
        assert body["meta"]["ask_id"] == 11

    def test_unknown_kind_error_echoes_ask_id(self, resources, tmp_path, mocker):
        cdb, tq, wm = resources
        cfg = base_config(tmp_path)
        mock_send = mocker.patch("src.json_handler.send_reply", return_value="<r@x>")
        msg = json_email({"v": 1, "kind": "dance", "meta": {"ask_id": 22}})
        handle_json_email(msg, cfg, cdb, tq, wm)
        body = json.loads(mock_send.call_args.kwargs["body"])
        assert body["error"]["code"] == "unknown_kind"
        assert body["meta"]["ask_id"] == 22
