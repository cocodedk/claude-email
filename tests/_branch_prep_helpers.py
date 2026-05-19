"""Shared fixtures and factory for the branch_prep matrix tests.

Underscore prefix → pytest skips collecting this module. Test files
import `queue` and `_task` from here to keep each split file under the
200-line cap without duplicating the helpers.
"""
import pytest


@pytest.fixture
def queue(mocker):
    q = mocker.MagicMock()
    q.mark_failed = mocker.MagicMock()
    q.set_branch = mocker.MagicMock()
    return q


def _task(tid=1, body="do X", branch_name=None, mutates_repo=None):
    return {
        "id": tid, "body": body,
        "branch_name": branch_name, "mutates_repo": mutates_repo,
    }
