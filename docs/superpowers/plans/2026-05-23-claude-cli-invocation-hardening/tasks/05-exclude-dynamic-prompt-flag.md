# Task 5: `--exclude-dynamic-system-prompt-sections` flag (config-gated, default on)

**Files:**
- Modify: `src/config.py` (add key `claude_exclude_dynamic_prompt`)
- Modify: `src/executor.py` (accept the kwarg, conditionally append flag)
- Modify: `main.py` (forward config value into `execute_command(...)`)
- Modify: `tests/test_executor_execute.py` (existing happy path now includes the flag)
- Review/modify: `tests/test_executor_model_effort_budget.py` if it exact-asserts executor argv
- Create: `tests/test_executor_extra_flags.py` (new dedicated tests)

> Note: the config-key smoke test (`tests/test_config_claude_knobs.py`) is created in Task 6, which covers both `claude_exclude_dynamic_prompt` and `claude_mcp_nonblocking`. Don't create or touch that file from this task.

- [ ] **Step 1: Write the failing test for the executor flag**

Create `tests/test_executor_extra_flags.py`:

```python
"""Tests for executor flags introduced by claude-code 2.1.115+."""
import subprocess
from src.executor import execute_command


def test_exclude_dynamic_prompt_default_on(mocker):
    mock_run = mocker.patch("subprocess.run")
    mock_run.return_value = subprocess.CompletedProcess(
        args=[], returncode=0, stdout="", stderr="",
    )
    execute_command("hi")
    cmd = mock_run.call_args.args[0]
    assert "--exclude-dynamic-system-prompt-sections" in cmd


def test_exclude_dynamic_prompt_disabled(mocker):
    mock_run = mocker.patch("subprocess.run")
    mock_run.return_value = subprocess.CompletedProcess(
        args=[], returncode=0, stdout="", stderr="",
    )
    execute_command("hi", exclude_dynamic_prompt=False)
    cmd = mock_run.call_args.args[0]
    assert "--exclude-dynamic-system-prompt-sections" not in cmd
```

- [ ] **Step 2: Run, confirm failure**

Run: `.venv/bin/pytest tests/test_executor_extra_flags.py -v`
Expected: FAIL — `TypeError: execute_command() got an unexpected keyword argument 'exclude_dynamic_prompt'`.

- [ ] **Step 3: Add the parameter + argv emission in `src/executor.py`**

Add `exclude_dynamic_prompt: bool = True` to the signature, and inside the argv builder (after the `system_prompt` branch, before `--print`):

```python
if exclude_dynamic_prompt:
    argv.append("--exclude-dynamic-system-prompt-sections")
```

Do not emit this flag for any future branch that uses `--system-prompt`; it is intended for the current `--append-system-prompt`/default-system-prompt flow and must stay before `--print` and the prompt text.

- [ ] **Step 4: Update the existing `test_successful_execution` to expect the flag in argv**

Edit `tests/test_executor_execute.py:18-26` argv list to:

```python
["claude", "--exclude-dynamic-system-prompt-sections", "--print", "hello"],
```

- [ ] **Step 5: Add config + main.py wiring**

In `src/config.py`, inside the `return {...}` dict, add:

```python
"claude_exclude_dynamic_prompt": os.environ.get(
    "CLAUDE_EMAIL_EXCLUDE_DYNAMIC_PROMPT", "1",
) != "0",
```

Only the literal value `"0"` disables the flag; unset and all other values keep the default-on behavior.

In `main.py`, in `process_email` where `execute_command(...)` is called, add the kwarg:

```python
exclude_dynamic_prompt=config.get("claude_exclude_dynamic_prompt", True),
```

- [ ] **Step 6: Update any other exact executor argv expectations**

If `tests/test_executor_model_effort_budget.py` exact-asserts the `claude` argv, insert `--exclude-dynamic-system-prompt-sections` before `--print` there as well. Keep the existing model, effort, budget, timeout, and cwd expectations unchanged.

- [ ] **Step 7: Run executor tests**

Run: `.venv/bin/pytest tests/test_executor_execute.py tests/test_executor_extra_flags.py tests/test_executor_model_effort_budget.py -v`
Expected: PASS.

- [ ] **Step 8: Commit**

```bash
git add src/executor.py src/config.py main.py tests/test_executor_execute.py tests/test_executor_model_effort_budget.py tests/test_executor_extra_flags.py
git commit -m "feat(executor): emit --exclude-dynamic-system-prompt-sections by default"
```
