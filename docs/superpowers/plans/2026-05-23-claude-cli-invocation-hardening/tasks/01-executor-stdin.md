# Task 1: `executor.py` — explicit `stdin=DEVNULL`

**Files:**
- Modify: `src/executor.py:56-64`
- Modify: `tests/test_executor_execute.py:18-26`
- Review/modify: `tests/test_executor_model_effort_budget.py` if it exact-asserts `subprocess.run(...)` kwargs

- [ ] **Step 1: Update the existing happy-path test to expect the new kwarg**

Edit `tests/test_executor_execute.py:18-26` so the `assert_called_once_with` block carries `stdin=subprocess.DEVNULL`. Add `import subprocess` at module scope if the file does not already import it:

```python
mock_run.assert_called_once_with(
    ["claude", "--print", "hello"],
    capture_output=True,
    text=True,
    timeout=30,
    shell=False,
    cwd=None,
    env=None,
    stdin=subprocess.DEVNULL,
)
```

- [ ] **Step 2: Update any other exact executor subprocess expectations**

If `tests/test_executor_model_effort_budget.py` exact-asserts `subprocess.run(...)` kwargs, add `stdin=subprocess.DEVNULL` there too. Do not change model/effort/budget assertions.

- [ ] **Step 3: Run the focused test to confirm it now fails**

Run: `.venv/bin/pytest tests/test_executor_execute.py::TestExecuteCommand::test_successful_execution -v`
Expected: FAIL — `Expected call ... Actual call ...` diff on `stdin`.

- [ ] **Step 4: Add the kwarg to `executor.py`**

Edit `src/executor.py:56-64` (the `subprocess.run(...)` call) to include `stdin=subprocess.DEVNULL`:

```python
result = subprocess.run(
    argv,
    capture_output=True,
    text=True,
    timeout=timeout,
    shell=False,
    cwd=cwd,
    env=env,
    stdin=subprocess.DEVNULL,
)
```

- [ ] **Step 5: Run all executor tests**

Run: `.venv/bin/pytest tests/test_executor_execute.py tests/test_executor_model_effort_budget.py -v`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add src/executor.py tests/test_executor_execute.py tests/test_executor_model_effort_budget.py
git commit -m "fix(executor): pin stdin=DEVNULL on claude --print subprocess"
```
