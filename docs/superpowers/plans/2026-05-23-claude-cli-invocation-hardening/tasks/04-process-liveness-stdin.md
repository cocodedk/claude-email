# Task 4: `process_liveness.py` — explicit `stdin=DEVNULL` on `claude agents --json`

**Files:**
- Modify: `src/process_liveness.py:112-117`
- Modify: `tests/test_process_liveness_session_pid.py`

- [ ] **Step 1: Append a dedicated stdin test to `tests/test_process_liveness_session_pid.py`**

The existing tests use `monkeypatch` with a lambda that ignores kwargs. Add a new test method to `class TestFindSessionPidForCwd` that uses `mocker.patch` so kwargs are inspectable:

```python
    def test_subprocess_run_uses_stdin_devnull(self, mocker, tmp_path):
        import subprocess
        import src.process_liveness as pl
        mock_run = mocker.patch.object(
            pl.subprocess, "run",
            return_value=type("R", (), {"stdout": "[]", "returncode": 0})(),
        )
        pl.find_session_pid_for_cwd(str(tmp_path))
        assert mock_run.call_args.kwargs["stdin"] == subprocess.DEVNULL
```

- [ ] **Step 2: Run the test, expect failure**

Run: `.venv/bin/pytest tests/test_process_liveness_session_pid.py::TestFindSessionPidForCwd::test_subprocess_run_uses_stdin_devnull -v`
Expected: FAIL — `KeyError: 'stdin'`.

- [ ] **Step 3: Add the kwarg in `process_liveness.py`**

Edit `src/process_liveness.py:112-117`:

```python
result = subprocess.run(
    [claude_bin, "agents", "--json"],
    capture_output=True, text=True, timeout=5, shell=False,
    stdin=subprocess.DEVNULL,
)
```

- [ ] **Step 4: Run all process_liveness tests**

Run: `.venv/bin/pytest tests/ -k "process_liveness or session_pid" -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/process_liveness.py tests/test_process_liveness_session_pid.py
git commit -m "fix(process_liveness): pin stdin=DEVNULL on claude agents --json probe"
```
