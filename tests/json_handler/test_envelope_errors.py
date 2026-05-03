"""Error envelopes — code, retryable flag, hint coverage, code mapping."""
import email
import email.message
import json

from src.json_envelope import CONTENT_TYPE
from src.json_handler import handle_json_email

from .conftest import base_config, json_email


class TestEnvelopeErrors:
    def test_bad_envelope_returns_error(self, resources, tmp_path, mocker):
        cdb, tq, wm = resources
        cfg = base_config(tmp_path)
        mock_send = mocker.patch("src.json_handler.send_reply", return_value="<r@x>")
        msg = json_email({"v": 99, "kind": "command"})
        handle_json_email(msg, cfg, cdb, tq, wm)
        body = json.loads(mock_send.call_args.kwargs["body"])
        assert body["kind"] == "error"
        assert body["error"]["code"] == "bad_envelope"

    def test_unauthorized_returns_error(self, resources, tmp_path, mocker):
        cdb, tq, wm = resources
        cfg = base_config(tmp_path, secret="correct")
        mock_send = mocker.patch("src.json_handler.send_reply", return_value="<r@x>")
        msg = json_email({
            "v": 1, "kind": "command", "project": "p", "body": "x",
            "meta": {"auth": "WRONG"},
        })
        handle_json_email(msg, cfg, cdb, tq, wm)
        body = json.loads(mock_send.call_args.kwargs["body"])
        assert body["kind"] == "error"
        assert body["error"]["code"] == "unauthorized"

    def test_unimplemented_kind_returns_not_implemented(self, resources, tmp_path, mocker):
        """``status`` and ``cancel`` are wired now; ``commit`` stays
        unimplemented and exercises the not_implemented path."""
        cdb, tq, wm = resources
        cfg = base_config(tmp_path)
        mock_send = mocker.patch("src.json_handler.send_reply", return_value="<r@x>")
        msg = json_email({
            "v": 1, "kind": "commit", "project": "p",
            "meta": {"auth": "s3cret"},
        })
        handle_json_email(msg, cfg, cdb, tq, wm)
        body = json.loads(mock_send.call_args.kwargs["body"])
        assert body["error"]["code"] == "not_implemented"
        assert body["error"]["retryable"] is False

    def test_bad_envelope_is_not_retryable(self, resources, tmp_path, mocker):
        cdb, tq, wm = resources
        cfg = base_config(tmp_path)
        mock_send = mocker.patch("src.json_handler.send_reply", return_value="<r@x>")
        handle_json_email(json_email({"v": 99, "kind": "command"}), cfg, cdb, tq, wm)
        body = json.loads(mock_send.call_args.kwargs["body"])
        assert body["error"]["retryable"] is False

    def test_unknown_kind_is_not_retryable(self, resources, tmp_path, mocker):
        cdb, tq, wm = resources
        cfg = base_config(tmp_path)
        mock_send = mocker.patch("src.json_handler.send_reply", return_value="<r@x>")
        handle_json_email(json_email({"v": 1, "kind": "dance"}), cfg, cdb, tq, wm)
        body = json.loads(mock_send.call_args.kwargs["body"])
        assert body["error"]["code"] == "unknown_kind"
        assert body["error"]["retryable"] is False

    def test_unauthorized_carries_hint(self, resources, tmp_path, mocker):
        cdb, tq, wm = resources
        cfg = base_config(tmp_path, secret="correct")
        mock_send = mocker.patch("src.json_handler.send_reply", return_value="<r@x>")
        msg = json_email({
            "v": 1, "kind": "command", "project": "p", "body": "x",
            "meta": {"auth": "WRONG"},
        })
        handle_json_email(msg, cfg, cdb, tq, wm)
        body = json.loads(mock_send.call_args.kwargs["body"])
        assert body["error"]["retryable"] is False
        assert body["error"].get("hint")

    def test_project_not_found_carries_hint(self, resources, tmp_path, mocker):
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
        assert body["error"]["retryable"] is False
        assert body["error"].get("hint")

    def test_project_not_found_without_legacy_substring(self, resources, tmp_path, mocker):
        """Regression: code flows through result['error_code'], not by
        substring-matching the prose."""
        cdb, tq, wm = resources
        cfg = base_config(tmp_path)
        mocker.patch(
            "src.json_kinds.enqueue_task_tool",
            return_value={"error": "arbitrary prose", "error_code": "project_not_found"},
        )
        mock_send = mocker.patch("src.json_handler.send_reply", return_value="<r@x>")
        msg = json_email({
            "v": 1, "kind": "command", "project": "p", "body": "x",
            "meta": {"auth": "s3cret"},
        })
        handle_json_email(msg, cfg, cdb, tq, wm)
        body = json.loads(mock_send.call_args.kwargs["body"])
        assert body["error"]["code"] == "project_not_found"

    def test_tool_layer_internal_error_maps_to_invalid_state(self, resources, tmp_path, mocker):
        """Unknown tool error code defaults to invalid_state; never blindly
        retryable. (When the tool returns ``internal``, that flows through.)"""
        cdb, tq, wm = resources
        cfg = base_config(tmp_path)
        mocker.patch(
            "src.json_kinds.enqueue_task_tool",
            return_value={"error": "weird", "error_code": "internal"},
        )
        mock_send = mocker.patch("src.json_handler.send_reply", return_value="<r@x>")
        msg = json_email({
            "v": 1, "kind": "command", "project": "p", "body": "x",
            "meta": {"auth": "s3cret"},
        })
        handle_json_email(msg, cfg, cdb, tq, wm)
        body = json.loads(mock_send.call_args.kwargs["body"])
        assert body["error"]["code"] == "internal"
        assert body["error"]["retryable"] is True

    def test_malformed_json_error_omits_ask_id(self, resources, tmp_path, mocker):
        """Pure JSON-decode failure — no structured data, nothing to salvage."""
        cdb, tq, wm = resources
        cfg = base_config(tmp_path)
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
