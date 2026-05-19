"""Coverage for chat/dispatch.py: chat_commit_project push-flag coercion."""
import asyncio

from chat.dispatch import dispatch
from src.chat_db import ChatDB


class TestDispatchCommitProjectPushCoercion:
    """Regression: ``bool("false")`` is True. The chat_commit_project
    dispatcher must coerce push the same way other boolean flags do, or a
    JSON-RPC client that stringifies booleans would silently trigger a push.
    """

    def test_string_false_does_not_push(self, tmp_path, mocker, monkeypatch):
        from src.task_queue import TaskQueue
        from src.worker_manager import WorkerManager
        from src.reset_control import TokenStore
        monkeypatch.setenv("CLAUDE_CWD", str(tmp_path))
        (tmp_path / "p").mkdir()
        db = ChatDB(str(tmp_path / "x.db"))
        queue = TaskQueue(str(tmp_path / "x.db"))
        manager = WorkerManager(
            db_path=str(tmp_path / "x.db"),
            project_root=str(tmp_path),
        )
        tokens = TokenStore()
        mocker.patch(
            "chat.project_mutations.commit_all", return_value=(True, "abc1234"),
        )
        push = mocker.patch("chat.project_mutations.push_current_branch")

        result = asyncio.run(dispatch(
            db, queue, manager, tokens,
            "chat_commit_project",
            {"project": "p", "message": "WIP", "push": "false"},
        ))
        assert result["pushed"] is False
        push.assert_not_called()

    def test_string_true_does_push(self, tmp_path, mocker, monkeypatch):
        from src.task_queue import TaskQueue
        from src.worker_manager import WorkerManager
        from src.reset_control import TokenStore
        monkeypatch.setenv("CLAUDE_CWD", str(tmp_path))
        (tmp_path / "p").mkdir()
        db = ChatDB(str(tmp_path / "x.db"))
        queue = TaskQueue(str(tmp_path / "x.db"))
        manager = WorkerManager(
            db_path=str(tmp_path / "x.db"),
            project_root=str(tmp_path),
        )
        tokens = TokenStore()
        mocker.patch(
            "chat.project_mutations.commit_all", return_value=(True, "abc1234"),
        )
        mocker.patch(
            "chat.project_mutations.push_current_branch",
            return_value=(True, "pushed"),
        )

        result = asyncio.run(dispatch(
            db, queue, manager, tokens,
            "chat_commit_project",
            {"project": "p", "message": "WIP", "push": "true"},
        ))
        assert result["pushed"] is True
