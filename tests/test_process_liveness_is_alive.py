"""Tests for src.process_liveness.is_alive."""
import os
import subprocess
import time

import pytest
from src.process_liveness import is_alive


def test_is_alive_true_for_live_child():
    child = subprocess.Popen(["sleep", "2"])
    try:
        assert is_alive(child.pid) is True
    finally:
        child.kill()
        child.wait()


def test_is_alive_false_for_nonexistent_pid():
    # PIDs are 22-bit on Linux by default; 99_999_999 is safely unused.
    assert is_alive(99_999_999) is False


def test_is_alive_reaps_zombie_and_returns_false():
    child = subprocess.Popen(["true"])
    pid = child.pid
    for _ in range(100):
        time.sleep(0.01)
        try:
            state = open(f"/proc/{pid}/stat").read().split()[2]
        except FileNotFoundError:
            pytest.fail("child disappeared before we could observe zombie state")
        if state == "Z":
            break
    else:
        pytest.fail("child never became a zombie")
    assert is_alive(pid) is False
    assert not os.path.exists(f"/proc/{pid}"), "zombie not reaped"


@pytest.mark.parametrize("bad_pid", [0, -1, -99])
def test_is_alive_rejects_non_positive_pids(bad_pid):
    """Guard against waitpid/kill's special semantics for pid<=0.

    pid=0 means "any child in this process group"; negative pids address
    process groups. If the DB ever stored a corrupted 0 or negative pid,
    a naive probe could inadvertently reap or signal unrelated processes.
    """
    assert is_alive(bad_pid) is False
