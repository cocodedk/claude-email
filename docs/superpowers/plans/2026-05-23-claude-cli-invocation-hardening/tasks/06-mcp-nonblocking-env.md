# Task 6: `MCP_CONNECTION_NONBLOCKING=true` env injection (opt-in)

**Files:**
- Modify: `src/config.py` (add key `claude_mcp_nonblocking`, default `False`)
- Modify: `src/executor.py` (accept kwarg; inject env var)
- Modify: `src/project_worker.py` + `WorkerConfig` + `_cfg_from_env` (add the knob)
- Modify: `main.py` (forward config into `execute_command`)
- Modify: `tests/test_executor_extra_flags.py` (add tests)
- Modify: `tests/test_project_worker_run_task.py` (add test asserting env var)
- Create: `tests/test_config_claude_knobs.py` (cover both new keys)

- [ ] **Step 1: Write failing executor test**

Append to `tests/test_executor_extra_flags.py`:

```python
def test_mcp_nonblocking_default_off(mocker, monkeypatch):
    monkeypatch.delenv("MCP_CONNECTION_NONBLOCKING", raising=False)
    mock_run = mocker.patch("subprocess.run")
    mock_run.return_value = subprocess.CompletedProcess(
        args=[], returncode=0, stdout="", stderr="",
    )
    execute_command("hi", extra_env={"FOO": "1"})
    env = mock_run.call_args.kwargs["env"] or {}
    assert "MCP_CONNECTION_NONBLOCKING" not in env


def test_mcp_nonblocking_enabled(mocker):
    mock_run = mocker.patch("subprocess.run")
    mock_run.return_value = subprocess.CompletedProcess(
        args=[], returncode=0, stdout="", stderr="",
    )
    execute_command("hi", mcp_nonblocking=True)
    env = mock_run.call_args.kwargs["env"]
    assert env["MCP_CONNECTION_NONBLOCKING"] == "true"
```

- [ ] **Step 2: Run, confirm failure**

Run: `.venv/bin/pytest tests/test_executor_extra_flags.py -v -k mcp_nonblocking`
Expected: FAIL — `TypeError: unexpected keyword argument 'mcp_nonblocking'`.

- [ ] **Step 3: Add the parameter in `src/executor.py`**

Add `mcp_nonblocking: bool = False` to the signature. Where `env` is built, preserve existing `extra_env` behavior and let the explicit knob win if both set the same key:

```python
env_overlay = dict(extra_env or {})
if mcp_nonblocking:
    env_overlay["MCP_CONNECTION_NONBLOCKING"] = "true"
env = {**os.environ, **env_overlay} if env_overlay else None
```

(Replaces the existing one-liner `env = {**os.environ, **extra_env} if extra_env else None`.)

Do not add CLI-argv detection here; the env var is harmless when no `--mcp-config` is present, and centralizing the knob keeps call sites simple.

- [ ] **Step 4: Write failing project_worker test**

Append to `tests/test_project_worker_run_task.py`:

```python
    def test_popen_injects_mcp_nonblocking_env_when_enabled(self, tq, cfg, mocker):
        cfg.mcp_nonblocking = True
        tq.enqueue(cfg.project_path, "x")
        claimed = tq.claim_next(cfg.project_path)
        proc = _mock_proc(mocker, pid=1, returncode=0)
        popen = mocker.patch("src.project_worker.subprocess.Popen", return_value=proc)
        run_task(tq, claimed, cfg)
        env = popen.call_args.kwargs.get("env") or {}
        assert env.get("MCP_CONNECTION_NONBLOCKING") == "true"
```

- [ ] **Step 5: Run, confirm failure**

Run: `.venv/bin/pytest tests/test_project_worker_run_task.py -v -k mcp_nonblocking`
Expected: FAIL — `WorkerConfig has no attribute 'mcp_nonblocking'` or env not present.

- [ ] **Step 6: Add the knob to `project_worker.py`**

In the `WorkerConfig` dataclass, add `mcp_nonblocking: bool = False`. Add `import os` if `project_worker.py` does not already import it. In `run_task`, where `subprocess.Popen` is called, build env without adding an `env` kwarg in the default-off case:

```python
popen_kwargs = {
    "cwd": cfg.project_path,
    "shell": False,
    "stdin": subprocess.DEVNULL,
    "stdout": subprocess.PIPE,
    "stderr": subprocess.STDOUT,
    "text": True,
}
if cfg.mcp_nonblocking:
    popen_kwargs["env"] = {**os.environ, "MCP_CONNECTION_NONBLOCKING": "true"}
proc = subprocess.Popen(argv, **popen_kwargs)
```

If `run_task` already has an env overlay by implementation time, merge into that overlay first, then set `"MCP_CONNECTION_NONBLOCKING": "true"` so this knob does not discard existing environment additions.

In `_cfg_from_env`, read the knob:

```python
mcp_nonblocking=os.environ.get("CLAUDE_EMAIL_MCP_NONBLOCKING", "") == "1",
```

- [ ] **Step 7: Add config + main.py wiring**

In `src/config.py`, add:

```python
"claude_mcp_nonblocking": os.environ.get("CLAUDE_EMAIL_MCP_NONBLOCKING", "") == "1",
```

Only the literal value `"1"` enables nonblocking MCP; unset and all other values remain default-off.

In `main.py` `process_email`, forward the kwarg into `execute_command(...)`:

```python
mcp_nonblocking=config.get("claude_mcp_nonblocking", False),
```

- [ ] **Step 8: Create `tests/test_config_claude_knobs.py`**

```python
"""Tests for the new claude_* config knobs added in 2026-05 hardening."""
import pytest


@pytest.fixture
def required_env(monkeypatch):
    base = {
        "IMAP_HOST": "x", "IMAP_PORT": "993",
        "SMTP_HOST": "x", "SMTP_PORT": "465",
        "EMAIL_ADDRESS": "a@b", "EMAIL_PASSWORD": "p",
        "AUTHORIZED_SENDER": "u@b", "GPG_FINGERPRINT": "F",
        "POLL_INTERVAL": "30", "CLAUDE_TIMEOUT": "60",
        "CLAUDE_BIN": "claude", "CLAUDE_CWD": "/tmp",
        "STATE_FILE": "/tmp/s.json", "EMAIL_DOMAIN": "b",
        "CHAT_DB_PATH": "/tmp/c.db", "CHAT_URL": "http://x",
        "SERVICE_NAME_EMAIL": "claude-email",
        "SERVICE_NAME_CHAT": "claude-chat",
    }
    for k, v in base.items():
        monkeypatch.setenv(k, v)
    monkeypatch.delenv("CLAUDE_EMAIL_EXCLUDE_DYNAMIC_PROMPT", raising=False)
    monkeypatch.delenv("CLAUDE_EMAIL_MCP_NONBLOCKING", raising=False)
    return monkeypatch


def test_exclude_dynamic_prompt_default_true(required_env):
    from src.config import build_config
    cfg = build_config()
    assert cfg["claude_exclude_dynamic_prompt"] is True


def test_exclude_dynamic_prompt_off(required_env):
    required_env.setenv("CLAUDE_EMAIL_EXCLUDE_DYNAMIC_PROMPT", "0")
    from src.config import build_config
    cfg = build_config()
    assert cfg["claude_exclude_dynamic_prompt"] is False


def test_mcp_nonblocking_default_false(required_env):
    from src.config import build_config
    cfg = build_config()
    assert cfg["claude_mcp_nonblocking"] is False


def test_mcp_nonblocking_on(required_env):
    required_env.setenv("CLAUDE_EMAIL_MCP_NONBLOCKING", "1")
    from src.config import build_config
    cfg = build_config()
    assert cfg["claude_mcp_nonblocking"] is True
```

- [ ] **Step 9: Run the full new test set**

Run: `.venv/bin/pytest tests/test_executor_extra_flags.py tests/test_config_claude_knobs.py tests/test_project_worker_run_task.py -v`
Expected: PASS.

- [ ] **Step 10: Commit**

```bash
git add src/executor.py src/project_worker.py src/config.py main.py tests/test_executor_extra_flags.py tests/test_config_claude_knobs.py tests/test_project_worker_run_task.py
git commit -m "feat: opt-in MCP_CONNECTION_NONBLOCKING via claude_mcp_nonblocking knob"
```
