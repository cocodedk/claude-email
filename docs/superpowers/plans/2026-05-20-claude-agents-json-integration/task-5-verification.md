> Part of [claude agents --json Integration Plan](../2026-05-20-claude-agents-json-integration.md)

## Task 5: Line-limit check + final verification

- [ ] **Step 1: Check line limits**

```bash
scripts/check-line-limit.sh
```

Expected: no violations. (`process_liveness.py` goes from 119 to ~148 lines; `agent_bootstrap.py` from 179 to ~193 lines — both under 200.)

- [ ] **Step 2: Run full suite one final time**

```
.venv/bin/pytest tests/ -q
```

Note the final test count and confirm 100% pass.

- [ ] **Step 3: Final commit if anything outstanding**

```bash
git status
```

If clean, nothing to do. Otherwise:

```bash
git add <any remaining files>
git commit -m "chore: final cleanup for claude-agents-json integration"
```
