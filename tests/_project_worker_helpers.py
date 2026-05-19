"""Shared fixtures and factory for project_worker test splits.

Underscore prefix → pytest skips collecting this module. Test files
import `tq`, `cfg`, and `_mock_proc` from here to keep each split file
under the 200-line cap without duplicating the helpers.
"""
import pytest
from src.chat_db import ChatDB
from src.task_queue import TaskQueue
from src.project_worker import WorkerConfig


@pytest.fixture
def tq(tmp_path):
    path = str(tmp_path / "db")
    ChatDB(path)
    return TaskQueue(path)


@pytest.fixture
def cfg(tmp_path):
    (tmp_path / "p").mkdir()
    return WorkerConfig(
        project_path=str(tmp_path / "p"),
        db_path=str(tmp_path / "db"),
        claude_bin="claude",
        mcp_config=str(tmp_path / ".mcp.json"),
        task_timeout=30,
        idle_timeout=0.1,
    )


def _mock_proc(mocker, pid, returncode=0, stdout="", timeout_first=False):
    proc = mocker.MagicMock(pid=pid)
    proc.returncode = returncode
    if timeout_first:
        import subprocess as sp
        proc.communicate.side_effect = [
            sp.TimeoutExpired(cmd="claude", timeout=30),
            (stdout, None),
        ]
    else:
        proc.communicate.return_value = (stdout, None)
    return proc
