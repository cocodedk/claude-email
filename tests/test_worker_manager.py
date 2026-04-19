"""Tests for src/worker_manager.py — one worker process per canonical project path."""
import pytest
from src.worker_manager import WorkerManager


@pytest.fixture
def mgr(tmp_path, mocker):
    mocker.patch("src.worker_manager.is_alive", return_value=True)
    return WorkerManager(
        db_path=str(tmp_path / "db.sqlite"),
        project_root=str(tmp_path),
        python_bin="/usr/bin/python3",
        module_env={"CHAT_DB_PATH": str(tmp_path / "db.sqlite")},
    )


class TestEnsureWorker:
    def test_first_call_spawns(self, mgr, tmp_path, mocker):
        (tmp_path / "p").mkdir()
        proc = mocker.MagicMock(pid=1111)
        proc.poll.return_value = None
        popen = mocker.patch("src.worker_manager.subprocess.Popen", return_value=proc)
        pid = mgr.ensure_worker(str(tmp_path / "p"))
        assert pid == 1111
        popen.assert_called_once()
        argv = popen.call_args.args[0]
        assert argv[0] == "/usr/bin/python3"
        assert "src.project_worker" in argv
        # cwd must be the claude-email repo so `-m src...` resolves;
        # NOT the project_root passed in (that's only a fallback for tests
        # that don't care about this axis).
        from src.worker_manager import _REPO_ROOT
        assert popen.call_args.kwargs["cwd"] == _REPO_ROOT

    def test_second_call_reuses_existing(self, mgr, tmp_path, mocker):
        (tmp_path / "p").mkdir()
        proc = mocker.MagicMock(pid=2222)
        proc.poll.return_value = None
        popen = mocker.patch("src.worker_manager.subprocess.Popen", return_value=proc)
        mgr.ensure_worker(str(tmp_path / "p"))
        mgr.ensure_worker(str(tmp_path / "p"))
        assert popen.call_count == 1

    def test_resolved_path_is_key(self, mgr, tmp_path, mocker):
        (tmp_path / "p").mkdir()
        (tmp_path / "link").symlink_to(tmp_path / "p")
        proc = mocker.MagicMock(pid=3)
        proc.poll.return_value = None
        popen = mocker.patch("src.worker_manager.subprocess.Popen", return_value=proc)
        mgr.ensure_worker(str(tmp_path / "p"))
        mgr.ensure_worker(str(tmp_path / "link"))
        assert popen.call_count == 1

    def test_dead_worker_triggers_respawn(self, mgr, tmp_path, mocker):
        (tmp_path / "p").mkdir()
        first = mocker.MagicMock(pid=10)
        first.poll.return_value = None
        second = mocker.MagicMock(pid=11)
        second.poll.return_value = None
        popen = mocker.patch("src.worker_manager.subprocess.Popen", side_effect=[first, second])
        mgr.ensure_worker(str(tmp_path / "p"))
        # Simulate worker process exiting
        first.poll.return_value = 0
        # is_alive lookup also returns False now
        mocker.patch("src.worker_manager.is_alive", return_value=False)
        pid = mgr.ensure_worker(str(tmp_path / "p"))
        assert pid == 11
        assert popen.call_count == 2

    def test_nonexistent_project_raises(self, mgr):
        with pytest.raises(ValueError):
            mgr.ensure_worker("/does/not/exist")


class TestReap:
    def test_reap_removes_dead_entries(self, mgr, tmp_path, mocker):
        (tmp_path / "p").mkdir()
        proc = mocker.MagicMock(pid=9)
        proc.poll.return_value = None
        mocker.patch("src.worker_manager.subprocess.Popen", return_value=proc)
        mgr.ensure_worker(str(tmp_path / "p"))
        proc.poll.return_value = 0
        mocker.patch("src.worker_manager.is_alive", return_value=False)
        mgr.reap()
        assert mgr.active_workers() == {}

    def test_reap_keeps_alive_entries(self, mgr, tmp_path, mocker):
        (tmp_path / "p").mkdir()
        proc = mocker.MagicMock(pid=9)
        proc.poll.return_value = None
        mocker.patch("src.worker_manager.subprocess.Popen", return_value=proc)
        mgr.ensure_worker(str(tmp_path / "p"))
        mgr.reap()
        assert str((tmp_path / "p").resolve()) in mgr.active_workers()


class TestPidOf:
    def test_pid_of_returns_alive_pid(self, mgr, tmp_path, mocker):
        (tmp_path / "p").mkdir()
        proc = mocker.MagicMock(pid=42)
        proc.poll.return_value = None
        mocker.patch("src.worker_manager.subprocess.Popen", return_value=proc)
        mgr.ensure_worker(str(tmp_path / "p"))
        assert mgr.pid_of(str(tmp_path / "p")) == 42

    def test_pid_of_returns_none_for_unknown(self, mgr, tmp_path):
        (tmp_path / "p").mkdir()
        assert mgr.pid_of(str(tmp_path / "p")) is None

    def test_pid_of_returns_none_for_nonexistent_path(self, mgr):
        assert mgr.pid_of("/does/not/exist/anywhere") is None
