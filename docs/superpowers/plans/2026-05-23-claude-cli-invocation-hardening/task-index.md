# Task index

| Task | Files | Description |
|------|-------|-------------|
| [Task 1](tasks/01-executor-stdin.md) | executor.py + tests | Add `stdin=DEVNULL` to `execute_command`'s `subprocess.run` |
| [Task 2](tasks/02-spawner-stdin.md) | spawner.py + test | Add `stdin=DEVNULL` to `spawn_agent`'s `subprocess.Popen` |
| [Task 3](tasks/03-project-worker-stdin.md) | project_worker.py + test | Add `stdin=DEVNULL` to `run_task`'s `subprocess.Popen` |
| [Task 4](tasks/04-process-liveness-stdin.md) | process_liveness.py + test | Add `stdin=DEVNULL` to `find_session_pid_for_cwd`'s `subprocess.run` |
| [Task 5](tasks/05-exclude-dynamic-prompt-flag.md) | config.py + executor.py + main.py + tests | Add `claude_exclude_dynamic_prompt` knob, emit `--exclude-dynamic-system-prompt-sections` |
| [Task 6](tasks/06-mcp-nonblocking-env.md) | config.py + executor.py + project_worker.py + main.py + tests | Add `claude_mcp_nonblocking` knob, inject `MCP_CONNECTION_NONBLOCKING=true` |
| [Task 7](tasks/07-verification.md) | full suite + README + CLAUDE.md | Verification: 100% coverage, line limit, documentation test count, smoke test |
