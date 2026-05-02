"""Tests for src/dispatch.py — per-sender resource dispatch."""
import email.message
import pytest
from src.dispatch import (
    build_universe_resources, dispatch_by_sender, universes_from_config,
)
from src.universes import Universe


def _make_msg(from_addr="user@example.com"):
    msg = email.message.Message()
    msg["From"] = from_addr
    msg["Return-Path"] = f"<{from_addr}>"
    return msg


def _universe(sender="user@example.com", base="/home/u/p", db_suffix=""):
    return Universe(
        sender=sender,
        allowed_base=base,
        chat_db_path=f"chat{db_suffix}.db",
        chat_url="http://x/sse",
        mcp_config="/repo/.mcp.json",
        service_name_chat="claude-chat.service",
    )


class TestBuildUniverseResources:
    def test_returns_one_entry_per_universe(self, tmp_path):
        u1 = _universe(db_suffix=str(tmp_path / "a"))
        u2 = _universe(sender="t@x", db_suffix=str(tmp_path / "b"))
        u1.chat_db_path = str(tmp_path / "a.db")
        u2.chat_db_path = str(tmp_path / "b.db")
        u1.allowed_base = str(tmp_path)
        u2.allowed_base = str(tmp_path)
        res = build_universe_resources([u1, u2])
        assert "user@example.com" in res
        assert "t@x" in res
        assert len(res) == 2

    def test_worker_manager_carries_universe_mcp_config(self, tmp_path):
        u = _universe()
        u.chat_db_path = str(tmp_path / "a.db")
        u.allowed_base = str(tmp_path)
        u.mcp_config = "/repo/.mcp-test.json"
        _, _, _, wm = build_universe_resources([u])["user@example.com"]
        assert wm._module_env == {
            "ROUTER_MCP_CONFIG": "/repo/.mcp-test.json",
            "CHAT_DB_PATH": str(tmp_path / "a.db"),
        }


class TestDispatchBySender:
    def test_unknown_sender_falls_through(self, mocker):
        calls = []
        def fake_process(msg, config, **kwargs):
            calls.append(("fallthrough", kwargs))
        msg = _make_msg(from_addr="evil@x")
        config = {"authorized_senders": ["bb@x", "test@x"]}
        dispatch_by_sender(msg, config, {}, fake_process)
        assert len(calls) == 1
        assert calls[0][1] == {}  # no scoped resources

    def test_matching_sender_scopes_resources(self, mocker):
        received = {}
        def fake_process(msg, config, chat_db=None, task_queue=None, worker_manager=None):
            received["cfg_universe"] = config["_universe"]
            received["cdb"] = chat_db
            received["tq"] = task_queue
            received["wm"] = worker_manager
        u = _universe()
        cdb, tq, wm = object(), object(), object()
        resources = {"user@example.com": (u, cdb, tq, wm)}
        msg = _make_msg("user@example.com")
        dispatch_by_sender(
            msg, {"authorized_senders": ["user@example.com"]}, resources, fake_process,
        )
        assert received["cfg_universe"] is u
        assert received["cdb"] is cdb
        assert received["tq"] is tq
        assert received["wm"] is wm


class TestUniversesFromConfig:
    def test_returns_existing_list_when_present(self):
        u = _universe()
        result = universes_from_config({"universes": [u]})
        assert result == [u]

    def test_synthesizes_universe_from_flat_config(self):
        config = {
            "authorized_sender": "bb@x",
            "claude_cwd": "/home/u/p",
            "chat_db_path": "claude-chat.db",
            "chat_url": "http://x/sse",
            "service_name_chat": "claude-chat.service",
        }
        result = universes_from_config(config)
        assert len(result) == 1
        assert result[0].sender == "bb@x"
        assert result[0].allowed_base == "/home/u/p"
        assert result[0].chat_db_path == "claude-chat.db"

    def test_empty_config_still_returns_synthetic(self):
        result = universes_from_config({})
        assert len(result) == 1
        assert result[0].sender == ""


class TestAliasRouting:
    """When a universe has aliases (multiple AUTHORIZED_SENDER entries),
    a message from any alias routes to the same resource bundle. Critical:
    the canonical sender and every alias end up calling process_email with
    the same chat_db / task_queue / worker_manager triple, so the
    conversation state is shared across the person's addresses."""

    def test_alias_resolves_to_canonical_universe(self, tmp_path):
        u = Universe(
            sender="user@example.com",
            aliases=("alias@example.com",),
            allowed_base=str(tmp_path),
            chat_db_path=str(tmp_path / "c.db"),
            chat_url="", mcp_config="", service_name_chat="",
        )
        resources = build_universe_resources([u])
        # Both addresses map to THE SAME bundle (same ChatDB, same TaskQueue).
        primary_bundle = resources["user@example.com"]
        alias_bundle = resources["alias@example.com"]
        assert primary_bundle is alias_bundle

    def test_dispatch_routes_alias_to_canonical_bundle(self):
        received = {}
        def fake_process(msg, config, chat_db=None, **_):
            received["cdb"] = chat_db
            received["sender_in_scoped_config"] = config["authorized_sender"]
            received["all_senders"] = config["authorized_senders"]
            received["reply_to"] = config.get("reply_to")
        u = Universe(
            sender="user@example.com",
            aliases=("alias@example.com",),
            allowed_base="/", chat_db_path="", chat_url="",
            mcp_config="", service_name_chat="",
        )
        cdb, tq, wm = object(), object(), object()
        resources = {
            "user@example.com": (u, cdb, tq, wm),
            "alias@example.com": (u, cdb, tq, wm),  # same tuple
        }
        # Inbound message comes from the ALIAS, not the canonical.
        msg = _make_msg("alias@example.com")
        dispatch_by_sender(
            msg,
            {"authorized_senders": ["user@example.com", "alias@example.com"]},
            resources, fake_process,
        )
        # Routed to the canonical universe's bundle — both are authorized.
        assert received["cdb"] is cdb
        # Canonical / all-senders keep their existing meaning…
        assert received["sender_in_scoped_config"] == "user@example.com"
        assert received["all_senders"] == ["user@example.com", "alias@example.com"]
        # …but reply_to carries the actual inbound sender so reply paths
        # don't dump alias replies into the canonical inbox.
        assert received["reply_to"] == "alias@example.com"
