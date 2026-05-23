# Task 7: Verification

- [ ] **Step 1: Run the full test suite**

Run: `.venv/bin/pytest tests/ -q`
Expected: PASS, count >= 1623 (1612 baseline + 11 new tests: Task 3 +1, Task 4 +1, Task 5 +2, Task 6 +3 executor/worker +4 config-knobs). If the real count is higher because existing exact-assertion tests were split or extended, use the actual passing count in docs.

- [ ] **Step 2: Coverage report**

Run: `.venv/bin/pytest tests/ --cov=src --cov=main --cov-report=term-missing -q`
Expected: 100% on `src/executor.py`, `src/spawner.py`, `src/project_worker.py`, `src/process_liveness.py`, `src/config.py`, `main.py`.

- [ ] **Step 3: Line-limit check**

Run: `scripts/check-line-limit.sh`
Expected: no file exceeds 200 lines. (`src/executor.py` and `src/config.py` will grow ~6 lines each — still well under.)

- [ ] **Step 4: Smoke test the live CLI**

Run (in this checkout): `claude --print --exclude-dynamic-system-prompt-sections "echo claude-email-hardening-smoke"`
Expected: command exits 0, response includes the smoke string. (Verifies the flag is honored by 2.1.150.)

- [ ] **Step 5: Update README and CLAUDE.md test counts**

Edit the sections of `README.md` and `CLAUDE.md` that say "1582 tests" / "1612 tests" to the final passing count from Step 1. In `CLAUDE.md`, the top "Project Overview" section may still read "1582 tests, 100% coverage"; update that too. Also add one short line under "Engineering Principles" or a new "Optional knobs" subsection naming `CLAUDE_EMAIL_EXCLUDE_DYNAMIC_PROMPT` and `CLAUDE_EMAIL_MCP_NONBLOCKING` with a one-sentence description of each.

- [ ] **Step 6: Final commit**

```bash
git add README.md CLAUDE.md
git commit -m "docs: refresh test count; document new claude_* env knobs"
```
