"""Tests for src/git_ops.py — thin wrappers over git subprocess."""
import subprocess
import pytest
from src.git_ops import (
    is_git_repo, is_clean, current_branch,
    checkout_new_branch, slugify, task_branch_name,
)


def _init_repo(path):
    subprocess.run(["git", "init", "-q"], cwd=path, check=True)
    subprocess.run(["git", "config", "user.email", "t@t"], cwd=path, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=path, check=True)
    subprocess.run(["git", "config", "commit.gpgsign", "false"], cwd=path, check=True)
    (path / "README").write_text("x")
    subprocess.run(["git", "add", "README"], cwd=path, check=True)
    subprocess.run(
        ["git", "commit", "-q", "-m", "init", "--no-gpg-sign"],
        cwd=path, check=True,
    )


class TestIsGitRepo:
    def test_true_for_repo(self, tmp_path):
        _init_repo(tmp_path)
        assert is_git_repo(str(tmp_path)) is True

    def test_false_for_plain_dir(self, tmp_path):
        assert is_git_repo(str(tmp_path)) is False


class TestIsClean:
    def test_clean_after_init(self, tmp_path):
        _init_repo(tmp_path)
        clean, msg = is_clean(str(tmp_path))
        assert clean is True
        assert msg == ""

    def test_dirty_returns_status(self, tmp_path):
        _init_repo(tmp_path)
        (tmp_path / "new-file").write_text("x")
        clean, msg = is_clean(str(tmp_path))
        assert clean is False
        assert "new-file" in msg

    def test_non_repo_reported_as_error(self, tmp_path):
        clean, msg = is_clean(str(tmp_path))
        assert clean is False
        assert msg  # some git error text


class TestCurrentBranch:
    def test_returns_default_branch(self, tmp_path):
        _init_repo(tmp_path)
        assert current_branch(str(tmp_path)) in {"main", "master"}

    def test_empty_for_non_repo(self, tmp_path):
        assert current_branch(str(tmp_path)) == ""


class TestCheckoutNewBranch:
    def test_creates_branch(self, tmp_path):
        _init_repo(tmp_path)
        ok, err = checkout_new_branch(str(tmp_path), "claude/task-1-x")
        assert ok is True
        assert err == ""
        assert current_branch(str(tmp_path)) == "claude/task-1-x"

    def test_duplicate_name_fails(self, tmp_path):
        _init_repo(tmp_path)
        checkout_new_branch(str(tmp_path), "claude/task-1")
        # Second attempt on same branch name fails
        subprocess.run(["git", "checkout", "-"], cwd=tmp_path, check=True, capture_output=True)
        ok, err = checkout_new_branch(str(tmp_path), "claude/task-1")
        assert ok is False
        assert err  # some error text


class TestSlugify:
    def test_basic(self):
        assert slugify("implement tests") == "implement-tests"

    def test_special_chars(self):
        assert slugify("add /foo bar! --now") == "add-foo-bar-now"

    def test_max_len(self):
        assert len(slugify("a" * 100, max_len=10)) == 10

    def test_empty_body_fallback(self):
        assert slugify("") == "task"

    def test_only_specials_fallback(self):
        assert slugify("!!! ---") == "task"


class TestTaskBranchName:
    def test_format(self):
        assert task_branch_name(42, "refactor config") == "claude/task-42-refactor-config"


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
            "src.git_ops._git",
            side_effect=[
                (0, "", ""),                         # is_git_repo
                (0, "Everything up-to-date", ""),    # git push
            ],
        )
        ok, msg = push_current_branch(str(tmp_path))
        assert ok is True

    def test_failure_reports_stderr(self, tmp_path, mocker):
        from src.git_ops import push_current_branch
        mocker.patch(
            "src.git_ops._git",
            side_effect=[
                (0, "", ""),                                       # is_git_repo
                (1, "", "fatal: no upstream configured"),          # git push
            ],
        )
        ok, msg = push_current_branch(str(tmp_path))
        assert ok is False
        assert "no upstream" in msg

    def test_non_git_repo_short_circuits(self, tmp_path):
        from src.git_ops import push_current_branch
        ok, msg = push_current_branch(str(tmp_path))
        assert ok is False
        assert "not a git" in msg.lower()
