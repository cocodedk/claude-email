"""Tests for src/git_ops.py — branch helpers and validation."""
import pytest
from tests._git_ops_helpers import _init_repo, _init_repo_with_branch


class TestBranchExists:
    def test_returns_true_for_existing_branch(self, tmp_path):
        from src.git_ops import branch_exists
        _init_repo_with_branch(tmp_path, "feature/x")
        assert branch_exists(str(tmp_path), "feature/x") is True

    def test_returns_false_for_missing_branch(self, tmp_path):
        from src.git_ops import branch_exists
        _init_repo(tmp_path)
        assert branch_exists(str(tmp_path), "nonexistent") is False


class TestCheckoutExistingBranch:
    def test_switches_to_existing(self, tmp_path):
        from src.git_ops import checkout_existing_branch, current_branch
        _init_repo_with_branch(tmp_path, "feature/x")
        ok, err = checkout_existing_branch(str(tmp_path), "feature/x")
        assert ok is True and err == ""
        assert current_branch(str(tmp_path)) == "feature/x"

    def test_returns_error_for_missing(self, tmp_path):
        from src.git_ops import checkout_existing_branch
        _init_repo(tmp_path)
        ok, err = checkout_existing_branch(str(tmp_path), "nope")
        assert ok is False
        assert err  # non-empty stderr


class TestIsValidTaskBranch:
    @pytest.mark.parametrize("name", [
        "claude/task-1-foo",
        "claude/task-42-also-add-docs",
        "claude/task-9999-some-long-slug-here",
    ])
    def test_valid(self, name):
        from src.git_ops import is_valid_task_branch
        assert is_valid_task_branch(name) is True

    @pytest.mark.parametrize("name", [
        "",
        "main",
        "feature/x",
        "claude/task-",
        "claude/task-abc-foo",
        "../escape",
        "claude/task-1; rm -rf /",
    ])
    def test_invalid(self, name):
        from src.git_ops import is_valid_task_branch
        assert is_valid_task_branch(name) is False


class TestCurrentBranchDetachedHEAD:
    """Round-3 reviewer catch: `git rev-parse --abbrev-ref HEAD` prints
    the literal string 'HEAD' when detached, NOT an empty string. The
    matrix in branch_prep relies on `current == prior` for the safe-to-
    continue cell, so an un-normalized 'HEAD' return would compare
    incorrectly. Real-git test, not just a mocked return."""

    def test_returns_branch_name_on_branch(self, tmp_path):
        from src.git_ops import current_branch
        _init_repo(tmp_path)
        assert current_branch(str(tmp_path)) == "main"

    def test_returns_empty_string_on_detached_head(self, tmp_path):
        from src.git_ops import current_branch
        import subprocess as sp
        _init_repo(tmp_path)
        sha = sp.run(
            ["git", "rev-parse", "HEAD"], cwd=tmp_path,
            check=True, capture_output=True, text=True,
        ).stdout.strip()
        sp.run(
            ["git", "checkout", "--detach", sha], cwd=tmp_path,
            check=True, capture_output=True,
        )
        assert current_branch(str(tmp_path)) == ""

    def test_returns_empty_string_outside_repo(self, tmp_path):
        from src.git_ops import current_branch
        assert current_branch(str(tmp_path)) == ""
