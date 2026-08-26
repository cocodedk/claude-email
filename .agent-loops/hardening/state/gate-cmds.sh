#!/usr/bin/env bash
# Repo gate. $1 = this slice's test target. Frozen + hash-checked.
set -eu

# A worktree pre-commit hook exports an ABSOLUTE GIT_DIR pointing into the real
# repository (.git/worktrees/<name>), and GIT_DIR overrides cwd — so a test that
# shells out to git against a tmp_path repo gets silently redirected onto the real
# one, rewriting its HEAD, index and refs. In a normal checkout GIT_DIR is unset,
# so this hazard is introduced by running the loop in a worktree. Scrub it here,
# at the gate, rather than depending on any individual test remembering to.
unset GIT_DIR GIT_WORK_TREE GIT_INDEX_FILE GIT_COMMON_DIR \
      GIT_OBJECT_DIRECTORY GIT_ALTERNATE_OBJECT_DIRECTORIES GIT_PREFIX

scripts/check-line-limit.sh
.venv/bin/pytest tests/ -q
.venv/bin/pytest "$1" -q
