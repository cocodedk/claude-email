# Task 3: `project_worker.py` — explicit `stdin=DEVNULL`

**Files:**
- Modify: `src/project_worker.py:72-75`
- Modify: `tests/test_project_worker_run_task.py` (any happy-path test asserting `popen.call_args`)

- [ ] **Step 1: Add a focused stdin test to `tests/test_project_worker_run_task.py`**

After `test_happy_path_marks_done`, append:

```python
    def test_popen_uses_stdin_devnull(self, tq, cfg, mocker):
        import subprocess
        tq.enqueue(cfg.project_path, "x")
        claimed = tq.claim_next(cfg.project_path)
        proc = _mock_proc(mocker, pid=1, returncode=0)
        popen = mocker.patch("src.project_worker.subprocess.Popen", return_value=proc)
        run_task(tq, claimed, cfg)
        assert popen.call_args.kwargs["stdin"] == subprocess.DEVNULL
```

- [ ] **Step 2: Run the new test, expect failure**

Run: `.venv/bin/pytest tests/test_project_worker_run_task.py::TestRunTask::test_popen_uses_stdin_devnull -v`
Expected: FAIL — `KeyError: 'stdin'`.

- [ ] **Step 3: Add the kwarg to `project_worker.py`**

Edit `src/project_worker.py:72-75`:

```python
proc = subprocess.Popen(
    argv, cwd=cfg.project_path, shell=False,
    stdin=subprocess.DEVNULL,
    stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
)
```

- [ ] **Step 4: Run the project_worker test module**

Run: `.venv/bin/pytest tests/test_project_worker_run_task.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/project_worker.py tests/test_project_worker_run_task.py
git commit -m "fix(project_worker): pin stdin=DEVNULL on Popen"
```
