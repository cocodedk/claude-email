# File map

Every source and test file this plan touches.

| File | Action | Responsibility |
|------|--------|----------------|
| `src/executor.py` | Modify | Add `stdin=DEVNULL`; emit `--exclude-dynamic-system-prompt-sections` when knob on; inject `MCP_CONNECTION_NONBLOCKING=true` when knob on |
| `src/spawner.py` | Modify | Add `stdin=DEVNULL` on `Popen` call |
| `src/project_worker.py` | Modify | Add `stdin=DEVNULL`; inject `MCP_CONNECTION_NONBLOCKING=true` when knob on |
| `src/process_liveness.py` | Modify | Add `stdin=DEVNULL` to `claude agents --json` invocation |
| `src/config.py` | Modify | New keys `claude_exclude_dynamic_prompt` (default True) and `claude_mcp_nonblocking` (default False) |
| `main.py` | Modify | Forward both new knobs into `execute_command(...)` kwargs |
| `tests/test_executor_execute.py` | Modify | Existing `assert_called_once_with` expands to include `stdin=DEVNULL` and the new default flag |
| `tests/test_executor_model_effort_budget.py` | Review/modify | Update any exact executor argv/kwargs expectations for `stdin=DEVNULL` and the new default flag |
| `tests/test_executor_extra_flags.py` | Create | Tests for new flag + env-var injection in executor |
| `tests/test_spawner_spawn.py` | Modify | Extend `test_spawn_agent_uses_devnull` to also assert stdin |
| `tests/test_project_worker_run_task.py` | Modify | Add `stdin=DEVNULL` assertion + `MCP_CONNECTION_NONBLOCKING` env assertion |
| `tests/test_process_liveness_session_pid.py` | Modify | Assert `stdin=DEVNULL` in mocked subprocess.run kwargs |
| `tests/test_config_claude_knobs.py` | Create | Tests for the two new config keys (defaults + env override) |
| `README.md` | Modify | Bump test count; one-line note about new env knobs |
| `CLAUDE.md` | Modify | Bump test count; mirror the new env-knob note if this file carries project overview/operator guidance |
