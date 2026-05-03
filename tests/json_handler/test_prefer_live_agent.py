"""``meta.prefer_live_agent`` routing on ``kind=command``.

When the client sets ``prefer_live_agent=true`` AND a registered agent
is live for the resolved project_path, the backend inserts a
``routed_via_agent`` virtual task row carrying the inbound origin
metadata, drops a bus message from "user" to that agent referencing
the task_id, and returns an ack with ``meta.routed_via="agent"``.
Otherwise it falls through to the existing worker spawn path with
``meta.routed_via="worker"``.
"""
import json

from src.json_handler import handle_json_email
from tests._fs_helpers import make_git_dir

from .conftest import base_config, json_email


class TestEnvelopeParsing:
    def test_meta_prefer_live_agent_default_false(self, resources, tmp_path, mocker):
        cdb, tq, wm = resources
        cfg = base_config(tmp_path)
        mock_send = mocker.patch("src.json_handler.send_reply", return_value="<r@x>")
        msg = json_email({
            "v": 1, "kind": "command", "project": "p", "body": "x",
            "meta": {"auth": "s3cret"},
        })
        handle_json_email(msg, cfg, cdb, tq, wm)
        body = json.loads(mock_send.call_args.kwargs["body"])
        assert body["meta"]["routed_via"] == "worker"


class TestRoutesToLiveAgent:
    def test_routes_to_agent_when_preferred_and_connected(
        self, resources, tmp_path, mocker,
    ):
        cdb, tq, wm = resources
        cfg = base_config(tmp_path)
        proj = (tmp_path / "p").resolve()
        cdb.register_agent("agent-p", str(proj))
        mock_send = mocker.patch("src.json_handler.send_reply", return_value="<r@x>")
        msg = json_email({
            "v": 1, "kind": "command", "project": "p", "body": "ping",
            "meta": {"auth": "s3cret", "prefer_live_agent": True},
        })
        handle_json_email(msg, cfg, cdb, tq, wm)
        body = json.loads(mock_send.call_args.kwargs["body"])
        assert body["kind"] == "ack"
        assert body["meta"]["routed_via"] == "agent"
        assert body["data"]["status"] == "routed"
        assert body["data"]["agent"] == "agent-p"
        # Virtual task row exists, carries origin metadata, status='routed_via_agent'.
        row = tq._conn.execute(  # noqa: SLF001
            "SELECT status, origin_subject, project_path FROM tasks WHERE id=?",
            (body["data"]["task_id"],),
        ).fetchone()
        assert row["status"] == "routed_via_agent"
        assert row["project_path"] == str(proj)
        # The agent has a message in its inbox carrying the task_id back.
        msgs = cdb._conn.execute(  # noqa: SLF001
            "SELECT body, type, task_id FROM messages "
            "WHERE from_name='user' AND to_name='agent-p'",
        ).fetchall()
        assert len(msgs) == 1
        assert msgs[0]["task_id"] == body["data"]["task_id"]
        assert msgs[0]["type"] == "ask"
        assert "ping" in msgs[0]["body"]
        # Message body teaches the agent how to thread the reply back.
        assert "task_id=" in msgs[0]["body"]
        assert "chat_message_agent" in msgs[0]["body"]

    def test_falls_through_to_worker_when_no_live_agent(
        self, resources, tmp_path, mocker,
    ):
        cdb, tq, wm = resources
        cfg = base_config(tmp_path)
        # No agent registered for the project.
        mock_send = mocker.patch("src.json_handler.send_reply", return_value="<r@x>")
        msg = json_email({
            "v": 1, "kind": "command", "project": "p", "body": "x",
            "meta": {"auth": "s3cret", "prefer_live_agent": True},
        })
        handle_json_email(msg, cfg, cdb, tq, wm)
        body = json.loads(mock_send.call_args.kwargs["body"])
        assert body["kind"] == "ack"
        assert body["meta"]["routed_via"] == "worker"
        assert body["data"]["status"] == "queued"

    def test_falls_through_to_worker_when_prefer_false(
        self, resources, tmp_path, mocker,
    ):
        """Even with a registered live agent, prefer_live_agent=false
        sticks with the worker path."""
        cdb, tq, wm = resources
        cfg = base_config(tmp_path)
        cdb.register_agent("agent-p", str((tmp_path / "p").resolve()))
        mock_send = mocker.patch("src.json_handler.send_reply", return_value="<r@x>")
        msg = json_email({
            "v": 1, "kind": "command", "project": "p", "body": "x",
            "meta": {"auth": "s3cret", "prefer_live_agent": False},
        })
        handle_json_email(msg, cfg, cdb, tq, wm)
        body = json.loads(mock_send.call_args.kwargs["body"])
        assert body["meta"]["routed_via"] == "worker"

    def test_falls_through_when_chat_db_unavailable(
        self, resources, tmp_path,
    ):
        """Defensive: if handle_command is called without a chat_db,
        the live-agent path bails (returns None) so we still spawn a worker."""
        from src.json_envelope import parse_envelope
        from src.json_kinds import handle_command
        _, tq, wm = resources
        msg = json_email({
            "v": 1, "kind": "command", "project": "p", "body": "x",
            "meta": {"auth": "s3cret", "prefer_live_agent": True},
        })
        env = parse_envelope(msg)
        out = handle_command(
            env, tq, wm, str(tmp_path),
            inbound_msg_id="<c1@x>", inbound_subject="s",
            inbound_from="bb@x", chat_db=None,
        )
        assert json.loads(out)["meta"]["routed_via"] == "worker"

    def test_unknown_project_returns_project_not_found_even_with_prefer(
        self, resources, tmp_path, mocker,
    ):
        """If the project doesn't resolve, agent-routing can't find a
        live agent either — falls through to the worker path which
        produces the user-facing project_not_found error envelope."""
        cdb, tq, wm = resources
        cfg = base_config(tmp_path)
        mock_send = mocker.patch("src.json_handler.send_reply", return_value="<r@x>")
        msg = json_email({
            "v": 1, "kind": "command", "project": "nope", "body": "x",
            "meta": {"auth": "s3cret", "prefer_live_agent": True},
        })
        handle_json_email(msg, cfg, cdb, tq, wm)
        body = json.loads(mock_send.call_args.kwargs["body"])
        assert body["error"]["code"] == "project_not_found"

    def test_virtual_task_origin_from_addresses_relay_correctly(
        self, resources, tmp_path, mocker,
    ):
        """The virtual task row carries origin_from so the relay's
        recipient_for_message picks the right inbox for the agent's
        eventual reply."""
        cdb, tq, wm = resources
        cfg = base_config(tmp_path)
        cfg["reply_to"] = "alias@example.com"
        cdb.register_agent("agent-p", str((tmp_path / "p").resolve()))
        mocker.patch("src.json_handler.send_reply", return_value="<r@x>")
        msg = json_email({
            "v": 1, "kind": "command", "project": "p", "body": "x",
            "meta": {"auth": "s3cret", "prefer_live_agent": True},
        })
        handle_json_email(msg, cfg, cdb, tq, wm)
        # Find the virtual task row.
        row = tq._conn.execute(  # noqa: SLF001
            "SELECT origin_from FROM tasks WHERE status='routed_via_agent'"
        ).fetchone()
        assert row["origin_from"] == "alias@example.com"
