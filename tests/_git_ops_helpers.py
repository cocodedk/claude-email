"""Shared helpers for git_ops test modules (underscore prefix → pytest skips collection)."""


def _git_env():
    import os
    return {
        **os.environ,
        "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@x",
        "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@x",
    }


def _init_repo(path):
    import subprocess as sp
    sp.run(["git", "init", "-q", "-b", "main"], cwd=path, check=True)
    # Persist identity + gpg-off on the repo so later commits via
    # src.git_ops.commit_all succeed on CI runners that lack a global
    # ~/.gitconfig. _git_env() alone only covers the seed commit.
    sp.run(["git", "config", "user.name", "t"], cwd=path, check=True)
    sp.run(["git", "config", "user.email", "t@x"], cwd=path, check=True)
    sp.run(["git", "config", "commit.gpgsign", "false"], cwd=path, check=True)
    sp.run(["git", "commit", "--allow-empty", "-m", "init", "--no-gpg-sign"],
           cwd=path, check=True, env=_git_env())


def _init_repo_with_branch(path, branch):
    import subprocess as sp
    _init_repo(path)
    sp.run(["git", "branch", branch], cwd=path, check=True)
