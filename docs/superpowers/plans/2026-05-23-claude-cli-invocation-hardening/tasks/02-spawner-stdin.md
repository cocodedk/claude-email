# Task 2: `spawner.py` — explicit `stdin=DEVNULL`

**Files:**
- Modify: `src/spawner.py:125-129`
- Modify: `tests/test_spawner_spawn.py:72-87`

- [ ] **Step 1: Extend the existing devnull test to also assert stdin**

Edit `tests/test_spawner_spawn.py:72-87`, adding one line after the existing stderr assertion. Add `import subprocess` at module scope if the file does not already import it:

```python
assert kwargs["stdin"] == subprocess.DEVNULL
```

- [ ] **Step 2: Run the test, expect failure**

Run: `.venv/bin/pytest tests/test_spawner_spawn.py::TestSpawnAgent::test_spawn_agent_uses_devnull -v`
Expected: FAIL — `KeyError: 'stdin'`.

- [ ] **Step 3: Add the kwarg to `spawner.py`**

Edit `src/spawner.py:125-129`:

```python
proc = subprocess.Popen(
    cmd, cwd=project_dir, shell=False,
    stdin=subprocess.DEVNULL,
    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    env=child_env,
)
```

- [ ] **Step 4: Run all spawner tests**

Run: `.venv/bin/pytest tests/test_spawner_spawn.py tests/test_spawner_spawn_model.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/spawner.py tests/test_spawner_spawn.py
git commit -m "fix(spawner): pin stdin=DEVNULL on spawn_agent Popen"
```
