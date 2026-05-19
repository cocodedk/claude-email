"""Tests for src/git_ops.py — commit_all and push_current_branch."""
from src.git_ops import is_clean
from tests._git_ops_helpers import _init_repo


class TestCommitAll:
    def test_commits_tracked_and_untracked(self, tmp_path):
        from src.git_ops import commit_all
        _init_repo(tmp_path)
        (tmp_path / "README").write_text("changed")
        (tmp_path / "new-file.txt").write_text("hello")
        ok, sha = commit_all(str(tmp_path), "WIP from email")
        assert ok is True
        assert len(sha) >= 7  # short sha
        clean, _ = is_clean(str(tmp_path))
        assert clean is True

    def test_clean_repo_refuses(self, tmp_path):
        from src.git_ops import commit_all
        _init_repo(tmp_path)
        ok, msg = commit_all(str(tmp_path), "noop")
        assert ok is False
        assert "nothing to commit" in msg.lower()

    def test_non_git_repo_refuses(self, tmp_path):
        from src.git_ops import commit_all
        ok, msg = commit_all(str(tmp_path), "wat")
        assert ok is False
        assert "not a git" in msg.lower()

    def test_add_failure_reported(self, tmp_path, mocker):
        from src.git_ops import commit_all
        _init_repo(tmp_path)
        (tmp_path / "x").write_text("x")
        mocker.patch(
            "src.git_ops._git",
            side_effect=[
                (0, "", ""),           # is_git_repo
                (1, "", "add-blew"),   # git add
            ],
        )
        ok, msg = commit_all(str(tmp_path), "x")
        assert ok is False
        assert "add-blew" in msg

    def test_commit_failure_reported(self, tmp_path, mocker):
        from src.git_ops import commit_all
        _init_repo(tmp_path)
        (tmp_path / "x").write_text("x")
        mocker.patch(
            "src.git_ops._git",
            side_effect=[
                (0, "", ""),                     # is_git_repo
                (0, "", ""),                     # git add
                (1, "", ""),                     # diff --cached --quiet (1 = changes staged)
                (1, "", "hooks failed"),         # git commit
            ],
        )
        ok, msg = commit_all(str(tmp_path), "x")
        assert ok is False
        assert "hooks failed" in msg

    def test_rev_parse_failure_still_reports_success(self, tmp_path, mocker):
        from src.git_ops import commit_all
        _init_repo(tmp_path)
        (tmp_path / "x").write_text("x")
        mocker.patch(
            "src.git_ops._git",
            side_effect=[
                (0, "", ""),                     # is_git_repo
                (0, "", ""),                     # git add
                (1, "", ""),                     # diff --cached --quiet → 1 = staged
                (0, "", ""),                     # git commit succeeded
                (1, "", ""),                     # rev-parse failed somehow
            ],
        )
        ok, msg = commit_all(str(tmp_path), "x")
        assert ok is False  # (rc == 0) is False because rc was 1
        assert "sha lookup failed" in msg


class TestPushCurrentBranch:
    def test_success(self, tmp_path, mocker):
        from src.git_ops import push_current_branch
        mocker.patch(
            "src.git_ops._git", return_value=(0, "Everything up-to-date", ""),
        )
        ok, _ = push_current_branch(str(tmp_path))
        assert ok is True

    def test_failure_reports_stderr(self, tmp_path, mocker):
        from src.git_ops import push_current_branch
        mocker.patch(
            "src.git_ops._git",
            return_value=(1, "", "fatal: no upstream configured"),
        )
        ok, msg = push_current_branch(str(tmp_path))
        assert ok is False
        assert "no upstream" in msg

    def test_non_git_repo_surfaces_git_error(self, tmp_path):
        """git push itself reports the error — we just pass it through."""
        from src.git_ops import push_current_branch
        ok, msg = push_current_branch(str(tmp_path))
        assert ok is False
        assert "not a git" in msg.lower()
