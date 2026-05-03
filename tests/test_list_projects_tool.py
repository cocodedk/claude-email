"""``list_projects_tool``: discover git repos under allowed_base + merge
with task-history. Powers the Android app's Projects tab (kind=list_projects).
"""
import os

import pytest

from src.chat_db import ChatDB
from src.task_queue import TaskQueue


@pytest.fixture
def tq(tmp_path):
    p = str(tmp_path / "x.db")
    ChatDB(p)
    return TaskQueue(p)


def _git_dir(parent, name: str):
    """Make ``parent/name`` look like a git repo (just the .git dir)."""
    d = parent / name
    d.mkdir()
    (d / ".git").mkdir()
    return d


class TestListProjectsTool:
    def test_returns_empty_when_no_git_repos(self, tmp_path, tq):
        from chat.tools import list_projects_tool
        result = list_projects_tool(tq, allowed_base=str(tmp_path))
        assert result == {"projects": []}

    def test_includes_idle_git_repo_with_null_activity(self, tmp_path, tq):
        from chat.tools import list_projects_tool
        _git_dir(tmp_path, "alpha")
        result = list_projects_tool(tq, allowed_base=str(tmp_path))
        assert len(result["projects"]) == 1
        row = result["projects"][0]
        assert row["name"] == "alpha"
        assert row["path"] == str((tmp_path / "alpha").resolve())
        assert row["running_task_id"] is None
        assert row["queue_depth"] == 0
        assert row["last_activity_at"] is None

    def test_excludes_non_git_directories(self, tmp_path, tq):
        from chat.tools import list_projects_tool
        (tmp_path / "scratch").mkdir()  # plain dir, no .git
        _git_dir(tmp_path, "alpha")
        names = [p["name"] for p in list_projects_tool(tq, allowed_base=str(tmp_path))["projects"]]
        assert names == ["alpha"]

    def test_excludes_hidden_directories(self, tmp_path, tq):
        from chat.tools import list_projects_tool
        _git_dir(tmp_path, ".hidden")
        _git_dir(tmp_path, "visible")
        names = [p["name"] for p in list_projects_tool(tq, allowed_base=str(tmp_path))["projects"]]
        assert names == ["visible"]

    def test_excludes_plain_files(self, tmp_path, tq):
        from chat.tools import list_projects_tool
        (tmp_path / "README.md").write_text("not a project")
        _git_dir(tmp_path, "alpha")
        names = [p["name"] for p in list_projects_tool(tq, allowed_base=str(tmp_path))["projects"]]
        assert names == ["alpha"]

    def test_includes_running_task(self, tmp_path, tq):
        from chat.tools import list_projects_tool
        proj = _git_dir(tmp_path, "alpha")
        tid = tq.enqueue(str(proj.resolve()), "do work")
        tq.claim_next(str(proj.resolve()))
        row = list_projects_tool(tq, allowed_base=str(tmp_path))["projects"][0]
        assert row["running_task_id"] == tid

    def test_queue_depth_counts_pending(self, tmp_path, tq):
        from chat.tools import list_projects_tool
        proj = _git_dir(tmp_path, "alpha")
        tq.enqueue(str(proj.resolve()), "p1")
        tq.enqueue(str(proj.resolve()), "p2")
        tq.enqueue(str(proj.resolve()), "p3")
        row = list_projects_tool(tq, allowed_base=str(tmp_path))["projects"][0]
        assert row["queue_depth"] == 3

    def test_last_activity_uses_latest_task_timestamp(self, tmp_path, tq):
        """Latest task's completed_at → started_at → created_at."""
        from chat.tools import list_projects_tool
        proj = _git_dir(tmp_path, "alpha")
        tid = tq.enqueue(str(proj.resolve()), "done")
        tq.claim_next(str(proj.resolve()))
        tq.mark_done(tid)
        row = list_projects_tool(tq, allowed_base=str(tmp_path))["projects"][0]
        # tasks.latest_task returns the most-recently-inserted, with timestamps
        # populated by the queue layer; we just confirm a non-null ISO string.
        assert row["last_activity_at"] is not None
        assert "T" in row["last_activity_at"]  # ISO 8601 looks like 2026-...T...

    def test_path_is_absolute_resolved(self, tmp_path, tq):
        """Symlinks / relative paths resolve so the app gets a stable id."""
        from chat.tools import list_projects_tool
        real = tmp_path / "real"
        real.mkdir()
        _git_dir(real, "alpha")
        link = tmp_path / "via-link"
        link.symlink_to(real)
        rows = list_projects_tool(tq, allowed_base=str(link))["projects"]
        assert len(rows) == 1
        assert rows[0]["path"] == str((real / "alpha").resolve())

    def test_empty_allowed_base_returns_empty(self, tq):
        from chat.tools import list_projects_tool
        result = list_projects_tool(tq, allowed_base="")
        assert result == {"projects": []}

    def test_nonexistent_allowed_base_returns_empty(self, tmp_path, tq):
        from chat.tools import list_projects_tool
        result = list_projects_tool(tq, allowed_base=str(tmp_path / "no-such-dir"))
        assert result == {"projects": []}

    def test_projects_sorted_by_name(self, tmp_path, tq):
        """Stable ordering — the app's row order shouldn't shuffle every poll."""
        from chat.tools import list_projects_tool
        for n in ("zulu", "alpha", "mike"):
            _git_dir(tmp_path, n)
        names = [p["name"] for p in list_projects_tool(tq, allowed_base=str(tmp_path))["projects"]]
        assert names == ["alpha", "mike", "zulu"]
