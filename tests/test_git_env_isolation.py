"""The test suite must be immune to inherited GIT_* environment variables.

A pre-commit hook running inside a *linked worktree* is invoked by git with an
absolute ``GIT_DIR`` (``<main>/.git/worktrees/<name>``) and ``GIT_INDEX_FILE``
already exported. ``.githooks/pre-commit`` then runs pytest, pytest inherits
both, and ``GIT_DIR`` *overrides cwd* for every ``git`` subprocess — so a test
that shells out to git against its own ``tmp_path`` repo is silently redirected
onto the real repository and rewrites its config, index and refs.

These tests pin the guarantee end to end: with all three variables pointing at
a foreign repository, the full suite still passes and that repository's
``config``, ``HEAD``, ``packed-refs`` and ``refs/**`` are byte-identical
afterwards.
"""
import os
import subprocess
import sys
from pathlib import Path

from tests.conftest import SCRUBBED_GIT_VARS

REPO_ROOT = Path(__file__).resolve().parents[1]
SELF = f"tests/{Path(__file__).name}"


def _snapshot(git_dir: Path) -> dict[str, bytes]:
    """Byte-exact picture of the state the acceptance criterion names."""
    snap: dict[str, bytes] = {}
    for name in ("config", "HEAD", "packed-refs"):
        path = git_dir / name
        if path.exists():
            snap[name] = path.read_bytes()
    refs = git_dir / "refs"
    for path in sorted(refs.rglob("*")):
        if path.is_file():
            snap[str(path.relative_to(git_dir))] = path.read_bytes()
    return snap


def _make_foreign_repo(root: Path) -> Path:
    """A throwaway stand-in for the real repository the hook would point at."""
    root.mkdir(parents=True, exist_ok=True)
    run = lambda *a: subprocess.run(a, cwd=root, check=True,  # noqa: E731
                                    capture_output=True)
    run("git", "init", "-q", "-b", "main", ".")
    run("git", "config", "user.name", "foreign")
    run("git", "config", "user.email", "foreign@example.invalid")
    run("git", "config", "commit.gpgsign", "false")
    (root / "canary.txt").write_text("do not touch\n")
    run("git", "add", "canary.txt")
    run("git", "commit", "-qm", "foreign seed", "--no-gpg-sign")
    return root / ".git"


def _poisoned_env(git_dir: Path) -> dict[str, str]:
    return {
        **os.environ,
        "GIT_DIR": str(git_dir),
        "GIT_WORK_TREE": str(git_dir.parent),
        "GIT_INDEX_FILE": str(git_dir / "index"),
    }


def test_git_env_is_scrubbed_from_the_running_interpreter():
    """The conftest scrub has already run by the time any test executes."""
    leaked = [name for name in SCRUBBED_GIT_VARS if name in os.environ]
    assert leaked == [], f"GIT_* leaked into the test process: {leaked}"


def test_scrub_list_covers_the_redirecting_variables():
    for name in ("GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE"):
        assert name in SCRUBBED_GIT_VARS


def test_author_identity_vars_are_not_scrubbed():
    """Tests set GIT_AUTHOR_*/GIT_COMMITTER_* deliberately — leave them alone."""
    for name in ("GIT_AUTHOR_NAME", "GIT_COMMITTER_NAME",
                 "GIT_AUTHOR_EMAIL", "GIT_COMMITTER_EMAIL"):
        assert name not in SCRUBBED_GIT_VARS


def test_git_subprocess_resolves_to_cwd_not_an_inherited_git_dir(tmp_path):
    """A plain ``git`` call in a tmp repo must resolve to that repo."""
    sandbox = tmp_path / "sandbox"
    sandbox.mkdir()
    subprocess.run(["git", "init", "-q", "-b", "main", "."],
                   cwd=sandbox, check=True, capture_output=True)
    out = subprocess.run(["git", "rev-parse", "--absolute-git-dir"],
                         cwd=sandbox, check=True, capture_output=True, text=True)
    assert Path(out.stdout.strip()) == (sandbox / ".git").resolve()


def test_git_env_helper_drops_redirecting_vars(monkeypatch, tmp_path):
    """``_git_env()`` is passed as ``env=`` — it must not re-introduce them."""
    from tests._git_ops_helpers import _git_env
    for name in SCRUBBED_GIT_VARS:
        monkeypatch.setenv(name, str(tmp_path / "poison"))
    env = _git_env()
    assert [n for n in SCRUBBED_GIT_VARS if n in env] == []
    assert env["GIT_AUTHOR_NAME"] == "t"


def test_full_suite_leaves_a_foreign_repo_byte_identical(tmp_path):
    """The acceptance criterion, end to end.

    The inner run excludes only *this* file, purely to stop it recursing into
    itself — nothing is skipped: the outer gate always runs this file, and the
    inner run is the full suite minus one module that would otherwise re-spawn
    the suite forever.
    """
    git_dir = _make_foreign_repo(tmp_path / "foreign")
    before = _snapshot(git_dir)
    assert before, "snapshot must not be empty"

    proc = subprocess.run(
        [sys.executable, "-m", "pytest", "tests/", "-q",
         f"--ignore={SELF}", "-p", "no:cacheprovider"],
        cwd=REPO_ROOT, env=_poisoned_env(git_dir),
        capture_output=True, text=True, timeout=900,
    )

    after = _snapshot(git_dir)
    assert after == before, (
        "the foreign repository was mutated by the test suite:\n"
        f"changed={sorted(set(before) ^ set(after)) or [k for k in before if before[k] != after.get(k)]}"
    )
    assert proc.returncode == 0, proc.stdout[-4000:] + proc.stderr[-2000:]
