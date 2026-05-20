> Part of [claude agents --json Integration Plan](../2026-05-20-claude-agents-json-integration.md)

- [ ] **Step 2: Run tests to confirm they fail**

```
.venv/bin/pytest tests/test_chat_stop_hook_skip.py tests/test_chat_stop_hook_emission.py tests/test_chat_stop_hook_fail_open.py -v
```

Expected: `FileNotFoundError` — script doesn't exist yet.

- [ ] **Step 3: Create `scripts/chat-stop-hook.py`**

```python
#!/usr/bin/env python3
"""Stop hook: log a flow event when the session stops with pending work.

Reads the Stop hook payload from stdin. If background_tasks or session_crons
are present (and non-empty), logs a hook_stop_pending_work flow event so the
dashboard shows the session stopped with unfinished work. Best-effort
telemetry — always exits 0.
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

try:
    from dotenv import load_dotenv
    load_dotenv(ROOT / ".env")
except ImportError:
    pass

from src.chat_db import ChatDB  # noqa: E402
from src.hook_utils import caller_name, read_hook_payload, resolved_db_path  # noqa: E402


def main() -> int:
    payload = read_hook_payload()
    if payload.get("agent_id"):
        return 0
    background_tasks = payload.get("background_tasks") or []
    session_crons = payload.get("session_crons") or []
    if not background_tasks and not session_crons:
        return 0
    try:
        db_path = resolved_db_path(ROOT)
    except RuntimeError as exc:
        print(f"chat-stop-hook: {exc}", file=sys.stderr)
        return 0
    if not db_path.exists():
        print(f"chat-stop-hook: DB {db_path} does not exist", file=sys.stderr)
        return 0
    try:
        db = ChatDB(str(db_path))
    except Exception as exc:  # noqa: BLE001
        print(f"chat-stop-hook: cannot open DB: {exc}", file=sys.stderr)
        return 0
    summary = f"background_tasks={len(background_tasks)} session_crons={len(session_crons)}"
    try:
        db._log_event(caller_name(), "hook_stop_pending_work", summary)
    except Exception as exc:  # noqa: BLE001
        print(f"chat-stop-hook: log event failed: {exc}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 4: Make it executable**

```bash
chmod +x scripts/chat-stop-hook.py
```

- [ ] **Step 5: Run the stop hook tests**

```
.venv/bin/pytest tests/test_chat_stop_hook_skip.py tests/test_chat_stop_hook_emission.py tests/test_chat_stop_hook_fail_open.py -v
```

Expected: all pass.

- [ ] **Step 6: Run full suite**

```
.venv/bin/pytest tests/ -q
```

- [ ] **Step 7: Commit**

```bash
git add scripts/chat-stop-hook.py \
    tests/test_chat_stop_hook_skip.py \
    tests/test_chat_stop_hook_emission.py \
    tests/test_chat_stop_hook_fail_open.py
git commit -m "feat(hooks): add chat-stop-hook.py — log pending work on session exit"
```
