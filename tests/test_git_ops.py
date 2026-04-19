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
