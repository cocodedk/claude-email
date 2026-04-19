"""Tests for src/reset_control.py — token store + hard reset."""
import time
import pytest
from src.chat_db import ChatDB
from src.task_queue import TaskQueue
from src.reset_control import TokenStore, perform_reset


@pytest.fixture
def tq(tmp_path):
    path = str(tmp_path / "db")
    ChatDB(path)
    return TaskQueue(path)


class TestTokenStore:
    def test_issue_returns_unique_tokens(self):
        s = TokenStore(ttl_seconds=60)
        a = s.issue("/p")
        b = s.issue("/p")
        assert a != b

    def test_consume_valid_token_returns_true(self):
        s = TokenStore(ttl_seconds=60)
        token = s.issue("/p")
        assert s.consume("/p", token) is True

    def test_consume_is_single_use(self):
        s = TokenStore(ttl_seconds=60)
        token = s.issue("/p")
        s.consume("/p", token)
        assert s.consume("/p", token) is False

    def test_consume_wrong_project_rejects(self):
        s = TokenStore(ttl_seconds=60)
        token = s.issue("/p")
        assert s.consume("/other", token) is False

    def test_consume_expired_token_rejects(self):
        s = TokenStore(ttl_seconds=0)
        token = s.issue("/p")
        time.sleep(0.01)
        assert s.consume("/p", token) is False

    def test_unknown_token_rejects(self):
        s = TokenStore(ttl_seconds=60)
        assert s.consume("/p", "bogus") is False

    def test_purge_removes_expired(self):
        s = TokenStore(ttl_seconds=0)
        s.issue("/p")
        time.sleep(0.01)
        s.purge()
        assert len(s._tokens) == 0


class TestPerformReset:
    def test_reset_drains_queue_and_runs_git(self, tq, tmp_path, mocker):
        (tmp_path / "p").mkdir()
        proj = str((tmp_path / "p").resolve())
        tq.enqueue(proj, "task-a")
        run = mocker.patch("src.reset_control.subprocess.run")
        run.return_value.returncode = 0
        run.return_value.stdout = ""
        run.return_value.stderr = ""
        result = perform_reset(tq, proj)
        assert result["drained"] == 1
        assert result["status"] == "reset"
        # Two commands: reset + clean
        cmds = [c.args[0] for c in run.call_args_list]
        assert ["git", "reset", "--hard", "HEAD"] in cmds
        assert ["git", "clean", "-fd"] in cmds

    def test_reset_cancels_running_task(self, tq, tmp_path, mocker):
        (tmp_path / "p").mkdir()
        proj = str((tmp_path / "p").resolve())
        tid = tq.enqueue(proj, "running")
        tq.claim_next(proj)
        tq.set_pid(tid, 4242)
        mocker.patch("src.reset_control.subprocess.run", return_value=mocker.MagicMock(returncode=0, stdout="", stderr=""))
        mocker.patch("src.task_control.os.kill")
        mocker.patch("src.task_control._wait_for_exit", return_value=True)
        result = perform_reset(tq, proj)
        assert result["cancelled_task_id"] == tid
        assert tq.get(tid)["status"] == "cancelled"

    def test_reset_git_error_reported(self, tq, tmp_path, mocker):
        (tmp_path / "p").mkdir()
        proj = str((tmp_path / "p").resolve())
        run_mock = mocker.MagicMock(returncode=1, stdout="", stderr="not a git repo")
        mocker.patch("src.reset_control.subprocess.run", return_value=run_mock)
        result = perform_reset(tq, proj)
        assert result["status"] == "reset_failed"
        assert "not a git repo" in result["error"]

    def test_reset_clean_failure_reported(self, tq, tmp_path, mocker):
        (tmp_path / "p").mkdir()
        proj = str((tmp_path / "p").resolve())
        ok = mocker.MagicMock(returncode=0, stdout="", stderr="")
        fail = mocker.MagicMock(returncode=2, stdout="", stderr="clean blew up")
        mocker.patch("src.reset_control.subprocess.run", side_effect=[ok, fail])
        result = perform_reset(tq, proj)
        assert result["status"] == "reset_failed"
        assert "clean blew up" in result["error"]


class TestTokenStorePurge:
    def test_purge_keeps_fresh_tokens(self):
        from src.reset_control import TokenStore
        s = TokenStore(ttl_seconds=60)
        s.issue("/a")
        s.purge()
        assert len(s._tokens) == 1
