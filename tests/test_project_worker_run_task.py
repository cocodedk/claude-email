"""Tests for src/project_worker.py — per-project worker loop (run_task)."""
import pytest
from src.project_worker import run_task

from tests._project_worker_helpers import tq, cfg, _mock_proc  # noqa: F401


@pytest.fixture(autouse=True)
def _skip_branch_prep(mocker):
    """Default: treat project_path as non-git so run_task skips branch work.
    Tests that exercise the branch dance override src.branch_prep.is_git_repo
    themselves."""
    mocker.patch("src.branch_prep.is_git_repo", return_value=False)


class TestRunTask:
    def test_happy_path_marks_done(self, tq, cfg, mocker):
        tid = tq.enqueue(cfg.project_path, "do X")
        claimed = tq.claim_next(cfg.project_path)
        proc = _mock_proc(mocker, pid=555, returncode=0, stdout="all good")
        popen = mocker.patch("src.project_worker.subprocess.Popen", return_value=proc)
        run_task(tq, claimed, cfg)
        row = tq.get(tid)
        assert row["status"] == "done"
        assert row["pid"] == 555
        assert row["output_text"] == "all good"
        argv = popen.call_args.args[0]
        assert "--continue" in argv
        assert "--print" in argv
        assert "do X" in argv

    def test_popen_uses_stdin_devnull(self, tq, cfg, mocker):
        import subprocess
        tq.enqueue(cfg.project_path, "x")
        claimed = tq.claim_next(cfg.project_path)
        proc = _mock_proc(mocker, pid=1, returncode=0)
        popen = mocker.patch("src.project_worker.subprocess.Popen", return_value=proc)
        run_task(tq, claimed, cfg)
        assert popen.call_args.kwargs["stdin"] == subprocess.DEVNULL

    def test_nonzero_exit_marks_failed_with_output_tail(self, tq, cfg, mocker):
        tid = tq.enqueue(cfg.project_path, "broken")
        claimed = tq.claim_next(cfg.project_path)
        proc = _mock_proc(mocker, pid=9, returncode=1, stdout="Traceback line\nboom")
        mocker.patch("src.project_worker.subprocess.Popen", return_value=proc)
        run_task(tq, claimed, cfg)
        row = tq.get(tid)
        assert row["status"] == "failed"
        assert row["error_text"]
        assert "boom" in row["output_text"]

    def test_does_not_overwrite_cancelled_status(self, tq, cfg, mocker):
        tid = tq.enqueue(cfg.project_path, "cancelled-midflight")
        claimed = tq.claim_next(cfg.project_path)
        proc = _mock_proc(mocker, pid=42, returncode=137, stdout="killed mid-flight")
        mocker.patch("src.project_worker.subprocess.Popen", return_value=proc)
        tq.cancel(tid)
        run_task(tq, claimed, cfg)
        row = tq.get(tid)
        assert row["status"] == "cancelled"
        # Output still captured even when status was cancelled externally
        assert "killed" in (row["output_text"] or "")

    def test_task_timeout_kills_and_fails(self, tq, cfg, mocker):
        tid = tq.enqueue(cfg.project_path, "slow")
        claimed = tq.claim_next(cfg.project_path)
        proc = _mock_proc(mocker, pid=10, stdout="some partial output", timeout_first=True)
        mocker.patch("src.project_worker.subprocess.Popen", return_value=proc)
        run_task(tq, claimed, cfg)
        row = tq.get(tid)
        assert row["status"] == "failed"
        assert "timeout" in row["error_text"].lower()
        assert "partial" in (row["output_text"] or "")
        proc.kill.assert_called_once()

    def test_plan_first_wraps_body_in_prompt(self, tq, cfg, mocker):
        """When plan_first=1 on the task row, the claude command line
        carries the plan-first prefix so the worker claude knows to
        propose-then-confirm before touching code."""
        tid = tq.enqueue(cfg.project_path, "refactor everything", plan_first=True)
        claimed = tq.claim_next(cfg.project_path)
        proc = _mock_proc(mocker, pid=1, returncode=0)
        popen = mocker.patch("src.project_worker.subprocess.Popen", return_value=proc)
        run_task(tq, claimed, cfg)
        argv = popen.call_args.args[0]
        # Body is the last argv element (after --print)
        body_arg = argv[argv.index("--print") + 1]
        assert "BEFORE doing any actual work" in body_arg
        assert "refactor everything" in body_arg

    def test_plan_first_absent_runs_body_as_is(self, tq, cfg, mocker):
        tid = tq.enqueue(cfg.project_path, "add a test", plan_first=False)
        claimed = tq.claim_next(cfg.project_path)
        proc = _mock_proc(mocker, pid=1, returncode=0)
        popen = mocker.patch("src.project_worker.subprocess.Popen", return_value=proc)
        run_task(tq, claimed, cfg)
        argv = popen.call_args.args[0]
        body_arg = argv[argv.index("--print") + 1]
        assert "BEFORE doing any actual work" not in body_arg
        assert body_arg == "add a test"

    def test_long_output_truncated(self, tq, cfg, mocker):
        tid = tq.enqueue(cfg.project_path, "noisy")
        claimed = tq.claim_next(cfg.project_path)
        big = "x" * 10_000
        proc = _mock_proc(mocker, pid=1, returncode=0, stdout=big)
        mocker.patch("src.project_worker.subprocess.Popen", return_value=proc)
        run_task(tq, claimed, cfg)
        out = tq.get(tid)["output_text"]
        assert out.startswith("…(truncated)")
        assert len(out.encode()) < 5_000

    def test_popen_injects_mcp_nonblocking_env_when_enabled(self, tq, cfg, mocker):
        cfg.mcp_nonblocking = True
        tq.enqueue(cfg.project_path, "x")
        claimed = tq.claim_next(cfg.project_path)
        proc = _mock_proc(mocker, pid=1, returncode=0)
        popen = mocker.patch("src.project_worker.subprocess.Popen", return_value=proc)
        run_task(tq, claimed, cfg)
        env = popen.call_args.kwargs.get("env") or {}
        assert env.get("MCP_CONNECTION_NONBLOCKING") == "true"
