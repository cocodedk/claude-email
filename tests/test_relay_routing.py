"""Tests for src/relay_routing.py — thread + recipient resolution."""
import pytest
from src.chat_db import ChatDB
from src.relay_routing import recipient_for_message, thread_id_for_message
from src.task_queue import TaskQueue
from src.universes import Universe


@pytest.fixture
def chat_db(tmp_path):
    return ChatDB(str(tmp_path / "db"))


@pytest.fixture
def queue(tmp_path):
    path = str(tmp_path / "db")
    return TaskQueue(path)


class TestThreadId:
    def test_message_without_task_id_uses_agent_last(self, chat_db):
        msg = {"from_name": "agent-x", "task_id": None}
        chat_db.insert_message("agent-x", "user", "hi", "notify")
        chat_db.set_email_message_id(1, "<prev@x>")
        assert thread_id_for_message(chat_db, msg) == "<prev@x>"

    def test_message_with_task_id_uses_origin_message_id(self, chat_db, queue):
        tid = queue.enqueue(
            "/p", "x", origin_content_type="application/json",
            origin_message_id="<cmd@android>",
        )
        msg = {"from_name": "agent-p", "task_id": tid}
        assert thread_id_for_message(chat_db, msg) == "<cmd@android>"

    def test_task_without_origin_falls_back(self, chat_db, queue):
        tid = queue.enqueue("/p", "x")  # no origin_message_id
        chat_db.insert_message("agent-p", "user", "hi", "notify")
        chat_db.set_email_message_id(1, "<fallback@x>")
        msg = {"from_name": "agent-p", "task_id": tid}
        assert thread_id_for_message(chat_db, msg) == "<fallback@x>"

    def test_no_task_no_prev_returns_empty(self, chat_db):
        msg = {"from_name": "agent-lonely", "task_id": None}
        assert thread_id_for_message(chat_db, msg) == ""


class TestRecipient:
    def _cfg(self, universes):
        return {"authorized_sender": "bb@prod", "universes": universes}

    def test_no_task_falls_back_to_primary(self, chat_db):
        cfg = self._cfg([])
        assert recipient_for_message(chat_db, {"task_id": None}, cfg) == "bb@prod"

    def test_task_in_prod_base_returns_primary(self, chat_db, queue):
        prod = Universe(
            sender="bb@prod", allowed_base="/home/u/projects",
            chat_db_path="", chat_url="", mcp_config="", service_name_chat="",
        )
        test = Universe(
            sender="test@t", allowed_base="/home/u/projects-test",
            chat_db_path="", chat_url="", mcp_config="", service_name_chat="",
            is_test=True,
        )
        tid = queue.enqueue("/home/u/projects/test-01", "x")
        cfg = self._cfg([prod, test])
        assert recipient_for_message(chat_db, {"task_id": tid}, cfg) == "bb@prod"

    def test_task_in_test_base_returns_test_sender(self, chat_db, queue):
        prod = Universe(
            sender="bb@prod", allowed_base="/home/u/projects",
            chat_db_path="", chat_url="", mcp_config="", service_name_chat="",
        )
        test = Universe(
            sender="test@t", allowed_base="/home/u/projects-test",
            chat_db_path="", chat_url="", mcp_config="", service_name_chat="",
            is_test=True,
        )
        tid = queue.enqueue("/home/u/projects-test/test-01", "x")
        cfg = self._cfg([prod, test])
        assert recipient_for_message(chat_db, {"task_id": tid}, cfg) == "test@t"

    def test_task_outside_any_universe_falls_back(self, chat_db, queue):
        prod = Universe(
            sender="bb@prod", allowed_base="/home/u/projects",
            chat_db_path="", chat_url="", mcp_config="", service_name_chat="",
        )
        tid = queue.enqueue("/somewhere/else", "x")
        cfg = self._cfg([prod])
        assert recipient_for_message(chat_db, {"task_id": tid}, cfg) == "bb@prod"
