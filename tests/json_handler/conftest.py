"""Shared fixtures + helpers for src/json_handler tests.

Folder-scoped so the per-file ``db_path`` / ``resources`` fixtures other
test files define don't collide with these.
"""
import email
import email.message
import json

import pytest

from src.chat_db import ChatDB
from src.json_envelope import CONTENT_TYPE
from src.task_queue import TaskQueue
from src.universes import Universe
from src.worker_manager import WorkerManager


def json_email(payload: dict, msg_id: str = "<c1@x>") -> email.message.Message:
    """Build a minimal application/json email carrying ``payload``."""
    msg = email.message.Message()
    msg.add_header("Content-Type", CONTENT_TYPE)
    msg["Message-ID"] = msg_id
    msg["Subject"] = "app command"
    msg.set_payload(json.dumps(payload))
    return msg


def base_config(tmp_path, secret: str = "s3cret") -> dict:
    """Universe-scoped config with ``allowed_base = tmp_path`` so
    ``project="p"`` resolves to ``tmp_path/p``."""
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
    wm = WorkerManager(
        db_path=db_path, project_root=str(tmp_path),
        python_bin="/usr/bin/python3",
    )
    return cdb, tq, wm
