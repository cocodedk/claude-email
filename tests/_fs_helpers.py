"""Shared filesystem helpers for tests. NOT a test file (leading
underscore keeps pytest from collecting it)."""
from pathlib import Path


def make_git_dir(parent: Path, name: str) -> Path:
    """Create ``parent/name/.git/`` so it satisfies the ``(path /
    ".git").exists()`` predicate without the cost of a real ``git init``."""
    d = parent / name
    d.mkdir()
    (d / ".git").mkdir()
    return d
