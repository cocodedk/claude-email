"""Tests for src/project_worker.py — per-project worker loop."""
import pytest
from src.chat_db import ChatDB
from src.task_queue import TaskQueue
from src.project_worker import run_task, worker_loop, WorkerConfig


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


class TestRunTask:
    def test_happy_path_marks_done(self, tq, cfg, mocker):
        tid = tq.enqueue(cfg.project_path, "do X")
        claimed = tq.claim_next(cfg.project_path)
        proc = mocker.MagicMock(pid=555)
        proc.wait.return_value = 0
        popen = mocker.patch("src.project_worker.subprocess.Popen", return_value=proc)
        run_task(tq, claimed, cfg)
        assert tq.get(tid)["status"] == "done"
        assert tq.get(tid)["pid"] == 555
        argv = popen.call_args.args[0]
        assert "--continue" in argv
        assert "--print" in argv
        assert "do X" in argv

    def test_nonzero_exit_marks_failed(self, tq, cfg, mocker):
        tid = tq.enqueue(cfg.project_path, "broken")
        claimed = tq.claim_next(cfg.project_path)
        proc = mocker.MagicMock(pid=9)
        proc.wait.return_value = 1
        mocker.patch("src.project_worker.subprocess.Popen", return_value=proc)
        run_task(tq, claimed, cfg)
        row = tq.get(tid)
        assert row["status"] == "failed"
        assert row["error_text"]

    def test_task_timeout_kills_and_fails(self, tq, cfg, mocker):
        import subprocess as sp
        tid = tq.enqueue(cfg.project_path, "slow")
        claimed = tq.claim_next(cfg.project_path)
        proc = mocker.MagicMock(pid=10)
        proc.wait.side_effect = [sp.TimeoutExpired(cmd="claude", timeout=30), 124]
        mocker.patch("src.project_worker.subprocess.Popen", return_value=proc)
        run_task(tq, claimed, cfg)
        row = tq.get(tid)
        assert row["status"] == "failed"
        assert "timeout" in row["error_text"].lower()
        proc.kill.assert_called_once()


class TestWorkerLoop:
    def test_processes_queue_in_order_then_exits(self, tq, cfg, mocker):
        ids = [tq.enqueue(cfg.project_path, f"t{i}") for i in range(3)]
        calls: list[int] = []

        def fake_run(queue, claimed, config):
            calls.append(claimed["id"])
            queue.mark_done(claimed["id"])

        worker_loop(cfg, run_task_fn=fake_run)
        assert calls == ids

    def test_exits_on_idle_timeout_when_no_tasks(self, tq, cfg):
        # No tasks enqueued → loop should return quickly
        worker_loop(cfg, run_task_fn=lambda *a, **kw: None)


class TestCfgFromEnv:
    def test_reads_required_and_optional_env(self, monkeypatch):
        from src.project_worker import _cfg_from_env
        monkeypatch.setenv("CHAT_DB_PATH", "/tmp/x.db")
        monkeypatch.setenv("CLAUDE_BIN", "claude-bin")
        monkeypatch.setenv("ROUTER_MCP_CONFIG", "/tmp/.mcp.json")
        monkeypatch.setenv("WORKER_TASK_TIMEOUT", "1200")
        monkeypatch.setenv("WORKER_IDLE_TIMEOUT", "60")
        monkeypatch.setenv("CLAUDE_YOLO", "1")
        cfg = _cfg_from_env("/proj")
        assert cfg.project_path == "/proj"
        assert cfg.db_path == "/tmp/x.db"
        assert cfg.claude_bin == "claude-bin"
        assert cfg.mcp_config == "/tmp/.mcp.json"
        assert cfg.task_timeout == 1200
        assert cfg.idle_timeout == 60.0
        assert cfg.yolo is True
