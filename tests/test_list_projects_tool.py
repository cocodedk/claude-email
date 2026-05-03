"""``list_projects_tool``: discover git repos under allowed_base + merge
with task-history."""
import pytest

from chat.tools import list_projects_tool
from src.chat_db import ChatDB
from src.task_queue import TaskQueue
from tests._fs_helpers import make_git_dir


@pytest.fixture
def tq(tmp_path):
    p = str(tmp_path / "x.db")
    ChatDB(p)
    return TaskQueue(p)


class TestListProjectsTool:
    def test_returns_empty_when_no_git_repos(self, tmp_path, tq):
        assert list_projects_tool(tq, allowed_base=str(tmp_path)) == {"projects": []}

    def test_includes_idle_git_repo_with_null_activity(self, tmp_path, tq):
        make_git_dir(tmp_path, "alpha")
        result = list_projects_tool(tq, allowed_base=str(tmp_path))
        assert len(result["projects"]) == 1
        row = result["projects"][0]
        assert row["name"] == "alpha"
        assert row["path"] == str((tmp_path / "alpha").resolve())
        assert row["running_task_id"] is None
        assert row["queue_depth"] == 0
        assert row["last_activity_at"] is None

    def test_excludes_non_git_directories(self, tmp_path, tq):
        (tmp_path / "scratch").mkdir()
        make_git_dir(tmp_path, "alpha")
        names = [p["name"] for p in list_projects_tool(tq, allowed_base=str(tmp_path))["projects"]]
        assert names == ["alpha"]

    def test_excludes_hidden_directories(self, tmp_path, tq):
        make_git_dir(tmp_path, ".hidden")
        make_git_dir(tmp_path, "visible")
        names = [p["name"] for p in list_projects_tool(tq, allowed_base=str(tmp_path))["projects"]]
        assert names == ["visible"]

    def test_excludes_plain_files(self, tmp_path, tq):
        (tmp_path / "README.md").write_text("not a project")
        make_git_dir(tmp_path, "alpha")
        names = [p["name"] for p in list_projects_tool(tq, allowed_base=str(tmp_path))["projects"]]
        assert names == ["alpha"]

    def test_includes_running_task(self, tmp_path, tq):
        proj = make_git_dir(tmp_path, "alpha")
        tid = tq.enqueue(str(proj.resolve()), "do work")
        tq.claim_next(str(proj.resolve()))
        row = list_projects_tool(tq, allowed_base=str(tmp_path))["projects"][0]
        assert row["running_task_id"] == tid

    def test_queue_depth_counts_pending(self, tmp_path, tq):
        proj = make_git_dir(tmp_path, "alpha")
        tq.enqueue(str(proj.resolve()), "p1")
        tq.enqueue(str(proj.resolve()), "p2")
        tq.enqueue(str(proj.resolve()), "p3")
        row = list_projects_tool(tq, allowed_base=str(tmp_path))["projects"][0]
        assert row["queue_depth"] == 3

    def test_last_activity_uses_latest_task_timestamp(self, tmp_path, tq):
        proj = make_git_dir(tmp_path, "alpha")
        tid = tq.enqueue(str(proj.resolve()), "done")
        tq.claim_next(str(proj.resolve()))
        tq.mark_done(tid)
        row = list_projects_tool(tq, allowed_base=str(tmp_path))["projects"][0]
        assert row["last_activity_at"] is not None
        assert "T" in row["last_activity_at"]

    def test_path_is_absolute_resolved(self, tmp_path, tq):
        real = tmp_path / "real"
        real.mkdir()
        make_git_dir(real, "alpha")
        link = tmp_path / "via-link"
        link.symlink_to(real)
        rows = list_projects_tool(tq, allowed_base=str(link))["projects"]
        assert len(rows) == 1
        assert rows[0]["path"] == str((real / "alpha").resolve())

    def test_empty_allowed_base_returns_empty(self, tq):
        assert list_projects_tool(tq, allowed_base="") == {"projects": []}

    def test_nonexistent_allowed_base_returns_empty(self, tmp_path, tq):
        result = list_projects_tool(tq, allowed_base=str(tmp_path / "no-such-dir"))
        assert result == {"projects": []}

    def test_projects_sorted_by_name(self, tmp_path, tq):
        for n in ("zulu", "alpha", "mike"):
            make_git_dir(tmp_path, n)
        names = [p["name"] for p in list_projects_tool(tq, allowed_base=str(tmp_path))["projects"]]
        assert names == ["alpha", "mike", "zulu"]


class TestAgentStatusField:
    """``agent_status`` is always present in each row. Reads from chat_db
    when supplied; defaults to ``"absent"`` when caller didn't pass one
    so the wire shape stays stable for legacy/test callers."""

    def test_absent_when_no_chat_db_passed(self, tmp_path, tq):
        make_git_dir(tmp_path, "alpha")
        row = list_projects_tool(tq, allowed_base=str(tmp_path))["projects"][0]
        assert row["agent_status"] == "absent"

    def test_connected_when_live_agent_registered(self, tmp_path, tq):
        proj = make_git_dir(tmp_path, "alpha")
        cdb = ChatDB(str(tmp_path / "x.db"))  # same DB the tq fixture made
        cdb.register_agent("agent-alpha", str(proj.resolve()))
        row = list_projects_tool(
            tq, allowed_base=str(tmp_path), chat_db=cdb,
        )["projects"][0]
        assert row["agent_status"] == "connected"

    def test_absent_when_chat_db_has_no_row(self, tmp_path, tq):
        make_git_dir(tmp_path, "alpha")
        cdb = ChatDB(str(tmp_path / "x.db"))
        row = list_projects_tool(
            tq, allowed_base=str(tmp_path), chat_db=cdb,
        )["projects"][0]
        assert row["agent_status"] == "absent"

    def test_per_project_state_is_independent(self, tmp_path, tq):
        proj_a = make_git_dir(tmp_path, "alpha")
        make_git_dir(tmp_path, "beta")  # no agent registered
        cdb = ChatDB(str(tmp_path / "x.db"))
        cdb.register_agent("agent-alpha", str(proj_a.resolve()))
        rows = list_projects_tool(
            tq, allowed_base=str(tmp_path), chat_db=cdb,
        )["projects"]
        by_name = {r["name"]: r for r in rows}
        assert by_name["alpha"]["agent_status"] == "connected"
        assert by_name["beta"]["agent_status"] == "absent"
