"""Root conftest — scrubs inherited GIT_* redirection variables. Nothing else.

The repo deliberately has no root conftest (see ``tests/json_handler/conftest.py``
on why fixtures stay folder- or module-scoped, and why shared helpers live in
underscore-prefixed modules instead). This file is the one sanctioned exception,
and it defines **no fixtures** so that convention stands.

Why it has to be here: git invokes a pre-commit hook with ``GIT_DIR`` and
``GIT_INDEX_FILE`` already exported — absolute, and pointing at
``<main>/.git/worktrees/<name>`` when the hook runs inside a linked worktree.
``.githooks/pre-commit`` runs ``.venv/bin/pytest tests/ -q``, pytest inherits
them, and ``GIT_DIR`` takes precedence over the ``cwd=`` of every ``git``
subprocess. Tests that build a repo in ``tmp_path`` are then redirected onto the
real repository and rewrite its config, HEAD and refs — observed damage:
``core.bare`` flipped to true on master and ``git status`` refusing to run.

Scrubbing in a wrapper script cannot cover this: git spawns the hook itself, so
the earliest thing under our control is pytest's own import of this conftest,
which happens before any test module is imported.

Only the *redirection* variables are removed. ``GIT_AUTHOR_*`` and
``GIT_COMMITTER_*`` are set deliberately by tests and are left untouched.
"""
import os

#: Variables that repoint git away from a subprocess's ``cwd``.
SCRUBBED_GIT_VARS = (
    "GIT_DIR",
    "GIT_WORK_TREE",
    "GIT_INDEX_FILE",
    "GIT_COMMON_DIR",
    "GIT_OBJECT_DIRECTORY",
    "GIT_ALTERNATE_OBJECT_DIRECTORIES",
    "GIT_PREFIX",
)


def scrub_git_env(env: dict | None = None) -> dict:
    """Remove every redirection variable from ``env`` (default: ``os.environ``)."""
    target = os.environ if env is None else env
    for name in SCRUBBED_GIT_VARS:
        target.pop(name, None)
    return target


# Runs at import time — before pytest imports any test module, and therefore
# before any test can shell out to git.
scrub_git_env()
