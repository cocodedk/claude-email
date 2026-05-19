"""Tests for src/project_worker.py — worker_loop and _cfg_from_env."""
import pytest
from src.project_worker import worker_loop

from tests._project_worker_helpers import tq, cfg  # noqa: F401


@pytest.fixture(autouse=True)
def _skip_branch_prep(mocker):
    """Default: treat project_path as non-git so run_task skips branch work.
    Tests that exercise the branch dance override src.branch_prep.is_git_repo
    themselves."""
    mocker.patch("src.branch_prep.is_git_repo", return_value=False)


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
