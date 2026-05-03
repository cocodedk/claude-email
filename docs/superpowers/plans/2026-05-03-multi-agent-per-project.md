# Multi-Agent Per Project Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Allow N concurrent Claude sessions in the same project directory, each with its own bus identity (agent name) and live PID attribution, so message delivery doesn't fall through to wake-spawn or get marked failed.

**Architecture:** Thread a single new env var, `CLAUDE_AGENT_NAME`, through three name-deriving sites — the SessionStart hook, `src/spawner.py`, and `src/proc_reconcile.py`. Drop the application-layer `AgentProjectTaken` raise so multiple live PIDs can share a `project_path`. Extend the email `spawn` command with an `as <agent-name>` clause so users can request distinct names. No DB migration; no MCP tool surface change.

**Tech Stack:** Python 3.12, sqlite3 (WAL mode), pytest. Existing helpers: `is_alive`, `find_ancestor_pid_matching`, `validate_project_path`, `build_agent_name`.

---

## File Structure

| File | Status | Responsibility |
|---|---|---|
| `src/agent_name.py` | **new** | Single source of truth for the agent-name regex. Exports `validated_agent_name(raw, fallback)`. |
| `src/spawn_args.py` | **new** | Parser for the email `spawn` meta-command args. Keeps `chat_handlers.py` under the 200-line cap. Exports `parse_spawn_args(raw) -> (project_dir, agent_name, instruction)`. |
| `tests/test_agent_name.py` | **new** | Tests for `validated_agent_name`. |
| `tests/test_spawn_args.py` | **new** | Tests for `parse_spawn_args`. |
| `src/agent_registry.py` | modify | Remove the `AgentProjectTaken` conflict-scan block (lines 72-81). Multiple live PIDs sharing `project_path` is now legal. |
| `scripts/chat-register-self.py` | modify | Honor `CLAUDE_AGENT_NAME` via `validated_agent_name` before falling back to `agent-<basename(cwd)>`. |
| `src/spawner.py` | modify | Add optional `agent_name` kwarg. Always inject `CLAUDE_AGENT_NAME` into the spawned child env so the hook + proc-scan see a consistent identity. |
| `src/proc_reconcile.py` | modify | Read `/proc/<pid>/environ` for `CLAUDE_AGENT_NAME` before falling back to cwd-basename derivation. |
| `src/chat_handlers.py` | modify | Use `parse_spawn_args` and pass `agent_name` through to `spawn_agent`. |
| `tests/test_chat_db.py` | modify | Flip the `test_register_different_name_same_project_live_pid_raises` test (line 124-132) — both registrations now succeed. |
| `tests/test_chat_register_self.py` | modify | Add tests for env-var override path. |
| `tests/test_spawner.py` (or wherever spawner is tested) | modify | Add tests for `agent_name` kwarg + env injection. |
| `tests/test_proc_reconcile.py` | modify | Add test for `/proc/<pid>/environ` name attribution. |
| `tests/test_chat_handlers.py` | modify | Add tests for `spawn ... as <name>` syntax. |
| `README.md` | modify | Document the new `spawn ... as <name>` syntax and the `CLAUDE_AGENT_NAME` env var. |
| `website/index.html` | modify | Mirror the README changes. |
| `website/fa/index.html` | modify | Mirror the README changes (Farsi). |

---

## Task 1: Create `validated_agent_name` helper

**Files:**
- Create: `src/agent_name.py`
- Test: `tests/test_agent_name.py`

- [ ] **Step 1.1: Write the failing test**

Create `tests/test_agent_name.py`:

```python
"""Tests for src/agent_name.py — central validator for bus identities."""
import pytest

from src.agent_name import validated_agent_name


class TestValidatedAgentName:
    def test_valid_passes_through(self):
        assert validated_agent_name("agent-foo", "agent-fallback") == "agent-foo"

    def test_valid_with_hyphens_and_underscores(self):
        assert validated_agent_name("agent-foo_bar-baz", "agent-fb") == "agent-foo_bar-baz"

    def test_none_returns_fallback(self):
        assert validated_agent_name(None, "agent-fb") == "agent-fb"

    def test_empty_string_returns_fallback(self):
        assert validated_agent_name("", "agent-fb") == "agent-fb"

    def test_missing_prefix_falls_back_with_warning(self, capsys):
        assert validated_agent_name("foo", "agent-fb") == "agent-fb"
        assert "rejecting invalid name 'foo'" in capsys.readouterr().err

    def test_uppercase_falls_back(self, capsys):
        assert validated_agent_name("agent-FOO", "agent-fb") == "agent-fb"
        assert "rejecting invalid name" in capsys.readouterr().err

    def test_starts_with_hyphen_after_prefix_falls_back(self, capsys):
        assert validated_agent_name("agent--foo", "agent-fb") == "agent-fb"
        assert "rejecting invalid name" in capsys.readouterr().err

    def test_too_long_falls_back(self, capsys):
        long = "agent-" + "a" * 63  # 6 + 63 = 69 chars > 64 max
        assert validated_agent_name(long, "agent-fb") == "agent-fb"
        assert "rejecting invalid name" in capsys.readouterr().err

    def test_max_length_passes(self):
        # agent- (6) + alphanumeric start (1) + 57 of [a-z0-9_-] = 64
        name = "agent-" + "a" + "b" * 57
        assert len(name) == 64
        assert validated_agent_name(name, "agent-fb") == name

    def test_disallowed_char_falls_back(self, capsys):
        assert validated_agent_name("agent-foo bar", "agent-fb") == "agent-fb"
        assert "rejecting invalid name" in capsys.readouterr().err
```

- [ ] **Step 1.2: Run the test to verify it fails**

```bash
.venv/bin/pytest tests/test_agent_name.py -v
```

Expected: ImportError or ModuleNotFoundError — `src.agent_name` doesn't exist yet.

- [ ] **Step 1.3: Write the implementation**

Create `src/agent_name.py`:

```python
"""Central validator for bus agent names.

A single source of truth for the regex governing how agent names look
across the bus — the SessionStart hook, the spawner, and the proc-scan
all read CLAUDE_AGENT_NAME and must agree on what's accepted.
"""
import re
import sys

_AGENT_NAME_RE = re.compile(r"^agent-[a-z0-9][a-z0-9_-]{0,57}$")


def validated_agent_name(raw: str | None, fallback: str) -> str:
    """Return ``raw`` if it's a valid agent name; otherwise ``fallback``.

    Empty / None returns ``fallback`` silently. A non-empty but malformed
    value emits a stderr warning so misconfiguration is visible without
    breaking the session.
    """
    if not raw:
        return fallback
    if _AGENT_NAME_RE.match(raw):
        return raw
    print(
        f"validated_agent_name: rejecting invalid name {raw!r} — "
        f"falling back to {fallback!r}",
        file=sys.stderr,
    )
    return fallback
```

Note on the regex: `agent-` is 6 chars, plus 1 alphanumeric start, plus up to 57 more chars from `[a-z0-9_-]` → max length 64.

- [ ] **Step 1.4: Run the test to verify it passes**

```bash
.venv/bin/pytest tests/test_agent_name.py -v
```

Expected: all 9 tests PASS.

- [ ] **Step 1.5: Commit**

```bash
git add src/agent_name.py tests/test_agent_name.py
git commit -m "feat(agent-name): central validator for bus identities

Adds src/agent_name.py with validated_agent_name() — single source of
truth for the regex governing agent names. Used by the SessionStart hook,
the spawner, and proc_reconcile in subsequent commits to honor a
CLAUDE_AGENT_NAME env var consistently."
```

---

## Task 2: Drop `AgentProjectTaken` raise in `agent_registry.py`

This is the structural unblock. Once removed, multiple live PIDs sharing `project_path` is legal.

**Files:**
- Modify: `src/agent_registry.py:72-81`
- Modify: `tests/test_chat_db.py:124-132` (flip assertion)

- [ ] **Step 2.1: Write the failing test (flip the existing one)**

In `tests/test_chat_db.py`, replace the existing `test_register_different_name_same_project_live_pid_raises` (around line 124-132) with this test:

```python
    def test_register_different_name_same_project_live_pid_allowed(self, db):
        """Multiple agents may live in the same project directory."""
        db.register_agent("agent-one", "/shared/project", pid=os.getpid())
        # Different name, same project, both live → must succeed.
        db.register_agent(
            "agent-two", "/shared/project", pid=os.getpid(),
        )
        assert db.get_agent("agent-one")["pid"] == os.getpid()
        assert db.get_agent("agent-two")["pid"] == os.getpid()
        assert db.get_agent("agent-one")["project_path"] == "/shared/project"
        assert db.get_agent("agent-two")["project_path"] == "/shared/project"
```

Also remove the `AgentProjectTaken` import-line usage if `test_chat_db.py` no longer references the symbol elsewhere. Check `grep -n AgentProjectTaken tests/test_chat_db.py` — if the flipped test was the only user, drop the symbol from the import on line 6:

```python
from src.chat_db import ChatDB, AgentNameTaken
```

- [ ] **Step 2.2: Run the test to verify it fails**

```bash
.venv/bin/pytest tests/test_chat_db.py::TestRegisterAgent::test_register_different_name_same_project_live_pid_allowed -v
```

Expected: FAIL with `AgentProjectTaken: project '/shared/project' already owned by agent 'agent-one'`.

- [ ] **Step 2.3: Modify `src/agent_registry.py`**

In `src/agent_registry.py`, delete lines 72-81 (the conflict-scan block that raises `AgentProjectTaken`). The surrounding context before:

```python
            try:
                existing = self.get_agent(name)
                if (
                    existing
                    and existing["pid"] is not None
                    and existing["pid"] != pid
                    and is_alive(existing["pid"])
                ):
                    raise AgentNameTaken(name, existing["pid"])
                conflicts = self._conn.execute(
                    "SELECT name, pid FROM agents "
                    "WHERE project_path=? AND name!=? AND pid IS NOT NULL",
                    (project_path, name),
                ).fetchall()
                for conflict in conflicts:
                    if is_alive(conflict["pid"]):
                        raise AgentProjectTaken(
                            project_path, conflict["name"], conflict["pid"],
                        )
                self._conn.execute(insert_sql, insert_args)
                self._conn.commit()
```

After the change:

```python
            try:
                existing = self.get_agent(name)
                if (
                    existing
                    and existing["pid"] is not None
                    and existing["pid"] != pid
                    and is_alive(existing["pid"])
                ):
                    raise AgentNameTaken(name, existing["pid"])
                self._conn.execute(insert_sql, insert_args)
                self._conn.commit()
```

Also remove the now-unused import on line 9. Change:

```python
from src.chat_errors import AgentNameTaken, AgentProjectTaken
```

to:

```python
from src.chat_errors import AgentNameTaken
```

And update the docstring at lines 31-37 to reflect the new contract:

```python
        """Register or take over an agent slot.

        When pid is provided, enforces at-most-one-live-owner per name:
        if another live process holds the name, raise AgentNameTaken.
        Stale (dead-pid) rows are transparently taken over. Multiple live
        agents may share the same project_path — each must have a
        distinct name.

        The liveness check and the upsert run inside a single
        IMMEDIATE transaction so a concurrent register_agent cannot
        squeeze a conflicting row in between our SELECT and INSERT.
        """
```

- [ ] **Step 2.4: Run the flipped test to verify it passes**

```bash
.venv/bin/pytest tests/test_chat_db.py::TestRegisterAgent::test_register_different_name_same_project_live_pid_allowed -v
```

Expected: PASS.

- [ ] **Step 2.5: Run the full chat_db test file**

```bash
.venv/bin/pytest tests/test_chat_db.py -v
```

Expected: all PASS. Pay attention to `test_register_different_name_same_project_dead_pid_allowed` (around line 134-137) — it should still pass unchanged since the dead-pid path was already legal.

- [ ] **Step 2.6: Run the full suite**

```bash
.venv/bin/pytest tests/ -q
```

Expected: all PASS. Other tests that imported `AgentProjectTaken` should still work because the symbol still exists in `src/chat_errors.py` and re-exported from `src/chat_db.py`; we just stopped raising it. If any test was *asserting the raise* and we missed flipping it, fix the assertion.

- [ ] **Step 2.7: Commit**

```bash
git add src/agent_registry.py tests/test_chat_db.py
git commit -m "refactor(registry): drop AgentProjectTaken raise

Multiple live agents may now share a project_path as long as they have
distinct names. The application-layer per-project uniqueness check was
the load-bearing constraint blocking multi-agent-per-project; the DB
schema (name PRIMARY KEY only) already allowed it.

AgentProjectTaken is no longer raised but the class symbol stays in
src/chat_errors.py so existing 'except (AgentNameTaken, AgentProjectTaken)'
catch blocks remain safe."
```

---

## Task 3: Hook honors `CLAUDE_AGENT_NAME`

**Files:**
- Modify: `scripts/chat-register-self.py:92-93`
- Modify: `tests/test_chat_register_self.py` (add tests for env var path)

- [ ] **Step 3.1: Write the failing test**

Append to `tests/test_chat_register_self.py` a new test class. The exact location: after the existing test classes, before the bottom of the file.

```python
class TestEnvAgentName:
    """CLAUDE_AGENT_NAME overrides the cwd-derived default."""

    def test_env_var_overrides_cwd_default(
        self, reg_mod, monkeypatch, tmp_path,
    ):
        db_path = tmp_path / "chat.db"
        # Initialize DB schema by opening it once via ChatDB.
        ChatDB(str(db_path)).close() if hasattr(ChatDB(str(db_path)), "close") else None
        monkeypatch.setenv("CHAT_DB_PATH", str(db_path))
        monkeypatch.setenv("CLAUDE_AGENT_NAME", "agent-custom")
        monkeypatch.chdir(tmp_path)
        # Stub stdin to look like a non-subagent SessionStart payload.
        monkeypatch.setattr(reg_mod.sys, "stdin", _FakeStdin(""))

        rc = reg_mod.main()
        assert rc == 0
        agent = ChatDB(str(db_path)).get_agent("agent-custom")
        assert agent is not None
        assert agent["project_path"] == str(tmp_path)

    def test_invalid_env_falls_back_to_cwd_default(
        self, reg_mod, monkeypatch, tmp_path, capsys,
    ):
        db_path = tmp_path / "chat.db"
        ChatDB(str(db_path)).close() if hasattr(ChatDB(str(db_path)), "close") else None
        monkeypatch.setenv("CHAT_DB_PATH", str(db_path))
        monkeypatch.setenv("CLAUDE_AGENT_NAME", "Not Valid")
        monkeypatch.chdir(tmp_path)
        monkeypatch.setattr(reg_mod.sys, "stdin", _FakeStdin(""))

        rc = reg_mod.main()
        assert rc == 0
        # Falls back to agent-<basename(tmp_path)>
        expected_fallback = f"agent-{tmp_path.name}"
        agent = ChatDB(str(db_path)).get_agent(expected_fallback)
        assert agent is not None
        assert "rejecting invalid name 'Not Valid'" in capsys.readouterr().err

    def test_unset_env_uses_cwd_default(
        self, reg_mod, monkeypatch, tmp_path,
    ):
        db_path = tmp_path / "chat.db"
        ChatDB(str(db_path)).close() if hasattr(ChatDB(str(db_path)), "close") else None
        monkeypatch.setenv("CHAT_DB_PATH", str(db_path))
        monkeypatch.delenv("CLAUDE_AGENT_NAME", raising=False)
        monkeypatch.chdir(tmp_path)
        monkeypatch.setattr(reg_mod.sys, "stdin", _FakeStdin(""))

        rc = reg_mod.main()
        assert rc == 0
        expected = f"agent-{tmp_path.name}"
        agent = ChatDB(str(db_path)).get_agent(expected)
        assert agent is not None


class _FakeStdin:
    def __init__(self, data: str) -> None:
        self._data = data

    def isatty(self) -> bool:
        return False

    def read(self) -> str:
        return self._data
```

If `_FakeStdin` already exists in this file, reuse the existing helper instead of redeclaring.

- [ ] **Step 3.2: Run the test to verify it fails**

```bash
.venv/bin/pytest tests/test_chat_register_self.py::TestEnvAgentName -v
```

Expected: tests FAIL — the env var is ignored today; `agent-custom` row never created.

- [ ] **Step 3.3: Modify `scripts/chat-register-self.py`**

Add the import near the other `src.*` imports (after line 33):

```python
from src.agent_name import validated_agent_name  # noqa: E402
```

Replace lines 92-93 (currently inside `main()`):

```python
    cwd = os.getcwd()
    name = "agent-" + PurePosixPath(cwd).name
```

with:

```python
    cwd = os.getcwd()
    fallback = "agent-" + PurePosixPath(cwd).name
    name = validated_agent_name(os.environ.get("CLAUDE_AGENT_NAME"), fallback)
```

- [ ] **Step 3.4: Run the test to verify it passes**

```bash
.venv/bin/pytest tests/test_chat_register_self.py -v
```

Expected: all PASS, including the three new tests.

- [ ] **Step 3.5: Commit**

```bash
git add scripts/chat-register-self.py tests/test_chat_register_self.py
git commit -m "feat(hook): honor CLAUDE_AGENT_NAME in SessionStart pre-register

When CLAUDE_AGENT_NAME is set in the environment of the calling Claude
session, the SessionStart hook registers the agent under that name
instead of the cwd-derived default. Invalid names fall back to the
default with a stderr warning.

This is the canonical path for a second agent in a project directory:
the env var distinguishes its bus identity from the first agent's
default name. PID computation is unchanged."
```

---

## Task 4: Spawner `agent_name` kwarg + env injection

**Files:**
- Modify: `src/spawner.py`
- Modify or create: `tests/test_spawner.py`

- [ ] **Step 4.1: Locate or create the spawner test file**

Run:

```bash
ls tests/test_spawner*.py 2>/dev/null
```

If a spawner test file exists, append the new test class there. Otherwise create `tests/test_spawner.py` with the boilerplate below. (Most likely existing tests for `spawn_agent` live in `tests/test_chat_handlers.py` or a dedicated file — check first.)

- [ ] **Step 4.2: Write the failing tests**

Append to the spawner test file:

```python
import os
from unittest.mock import MagicMock, patch

from src.spawner import spawn_agent


class TestSpawnerAgentName:
    """spawn_agent honors agent_name kwarg and injects CLAUDE_AGENT_NAME."""

    def _stub_db(self):
        db = MagicMock()
        db.get_agent.return_value = None
        return db

    def test_agent_name_kwarg_overrides_basename(self, tmp_path):
        with patch("src.spawner.subprocess.Popen") as popen, \
             patch("src.spawner.inject_mcp_config"), \
             patch("src.spawner.inject_session_start_hook"), \
             patch("src.spawner.approve_mcp_server_for_project"):
            popen.return_value = MagicMock(pid=12345)
            db = self._stub_db()
            name, pid = spawn_agent(
                db, str(tmp_path), "http://chat", agent_name="agent-custom",
            )
            assert name == "agent-custom"
            db.register_agent.assert_called_with("agent-custom", str(tmp_path))

    def test_env_var_injected_into_child(self, tmp_path):
        with patch("src.spawner.subprocess.Popen") as popen, \
             patch("src.spawner.inject_mcp_config"), \
             patch("src.spawner.inject_session_start_hook"), \
             patch("src.spawner.approve_mcp_server_for_project"):
            popen.return_value = MagicMock(pid=12345)
            db = self._stub_db()
            spawn_agent(
                db, str(tmp_path), "http://chat", agent_name="agent-custom",
            )
            kwargs = popen.call_args.kwargs
            assert kwargs["env"] is not None
            assert kwargs["env"]["CLAUDE_AGENT_NAME"] == "agent-custom"

    def test_env_var_injected_with_default_name(self, tmp_path):
        """Always set CLAUDE_AGENT_NAME so proc-scan can attribute reliably."""
        with patch("src.spawner.subprocess.Popen") as popen, \
             patch("src.spawner.inject_mcp_config"), \
             patch("src.spawner.inject_session_start_hook"), \
             patch("src.spawner.approve_mcp_server_for_project"):
            popen.return_value = MagicMock(pid=12345)
            db = self._stub_db()
            name, _ = spawn_agent(db, str(tmp_path), "http://chat")
            kwargs = popen.call_args.kwargs
            assert kwargs["env"]["CLAUDE_AGENT_NAME"] == name

    def test_invalid_agent_name_falls_back_to_default(self, tmp_path, capsys):
        with patch("src.spawner.subprocess.Popen") as popen, \
             patch("src.spawner.inject_mcp_config"), \
             patch("src.spawner.inject_session_start_hook"), \
             patch("src.spawner.approve_mcp_server_for_project"):
            popen.return_value = MagicMock(pid=12345)
            db = self._stub_db()
            name, _ = spawn_agent(
                db, str(tmp_path), "http://chat", agent_name="Not Valid",
            )
            assert name == f"agent-{tmp_path.name}"
            assert "rejecting invalid name" in capsys.readouterr().err
```

- [ ] **Step 4.3: Run tests to verify they fail**

```bash
.venv/bin/pytest tests/test_spawner.py::TestSpawnerAgentName -v
```

(Adjust path if you appended to a different file.)

Expected: FAIL — `spawn_agent` doesn't accept `agent_name` kwarg.

- [ ] **Step 4.4: Modify `src/spawner.py`**

Add the import at the top (after the existing `from src.agent_bootstrap import ...`):

```python
from src.agent_name import validated_agent_name
```

Modify the `spawn_agent` signature (line 60-72) — add `agent_name: str | None = None` as the last kwarg:

```python
def spawn_agent(
    db,
    project_dir: str,
    chat_url: str,
    instruction: str = "",
    claude_bin: str = "claude",
    allowed_base: str | None = None,
    yolo: bool = False,
    extra_env: dict[str, str] | None = None,
    model: str | None = None,
    effort: str | None = None,
    max_budget_usd: str | None = None,
    agent_name: str | None = None,
) -> tuple[str, int]:
```

Modify the body — replace lines 80-81:

```python
    project_dir = validate_project_path(project_dir, allowed_base)
    name = build_agent_name(project_dir)
```

with:

```python
    project_dir = validate_project_path(project_dir, allowed_base)
    default_name = build_agent_name(project_dir)
    name = validated_agent_name(agent_name, default_name)
```

Modify the env construction (line 120) so `CLAUDE_AGENT_NAME` is always injected. Replace:

```python
    env = {**os.environ, **extra_env} if extra_env else None
```

with:

```python
    child_env = {**os.environ, **(extra_env or {}), "CLAUDE_AGENT_NAME": name}
```

And update the `Popen` call at line 121-125 to pass `env=child_env`:

```python
    proc = subprocess.Popen(
        cmd, cwd=project_dir, shell=False,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        env=child_env,
    )
```

- [ ] **Step 4.5: Run tests to verify they pass**

```bash
.venv/bin/pytest tests/test_spawner.py -v
```

Expected: all PASS.

- [ ] **Step 4.6: Run the broader suite**

```bash
.venv/bin/pytest tests/ -q
```

Expected: all PASS. If any other test stubbed `Popen` and asserted `env=None`, update it to assert `env={...with CLAUDE_AGENT_NAME...}`.

- [ ] **Step 4.7: Check the line limit**

```bash
scripts/check-line-limit.sh
```

Expected: PASS. `src/spawner.py` should be ≤200 lines.

- [ ] **Step 4.8: Commit**

```bash
git add src/spawner.py tests/test_spawner.py
git commit -m "feat(spawner): agent_name kwarg + always-inject CLAUDE_AGENT_NAME

spawn_agent now accepts an explicit agent_name and unconditionally
injects CLAUDE_AGENT_NAME into the spawned child's environment. This
gives the SessionStart hook and proc_reconcile a reliable identity
signal even when the agent name doesn't match agent-<basename(cwd)>."
```

---

## Task 5: `proc_reconcile` reads `/proc/<pid>/environ`

**Files:**
- Modify: `src/proc_reconcile.py`
- Modify: `tests/test_proc_reconcile.py`

- [ ] **Step 5.1: Write the failing test**

Append to `tests/test_proc_reconcile.py`:

```python
class TestEnvironNameAttribution:
    """proc_reconcile reads CLAUDE_AGENT_NAME from /proc/<pid>/environ."""

    def test_environ_name_overrides_basename(self, tmp_path, monkeypatch):
        from src.chat_db import ChatDB
        from src.proc_reconcile import reconcile_live_agents

        db = ChatDB(str(tmp_path / "chat.db"))

        # Stub the proc-scan helpers to return a synthetic PID with our env.
        monkeypatch.setattr(
            "src.proc_reconcile._iter_claude_pids", lambda marker="claude": [4242],
        )
        monkeypatch.setattr(
            "src.proc_reconcile._cwd_of", lambda pid: str(tmp_path),
        )
        monkeypatch.setattr(
            "src.proc_reconcile._read_agent_name_from_environ",
            lambda pid: "agent-custom",
        )

        touched = reconcile_live_agents(db)
        assert touched == ["agent-custom"]
        assert db.get_agent("agent-custom")["pid"] == 4242

    def test_missing_environ_falls_back_to_basename(self, tmp_path, monkeypatch):
        from src.chat_db import ChatDB
        from src.proc_reconcile import reconcile_live_agents

        db = ChatDB(str(tmp_path / "chat.db"))

        monkeypatch.setattr(
            "src.proc_reconcile._iter_claude_pids", lambda marker="claude": [4242],
        )
        monkeypatch.setattr(
            "src.proc_reconcile._cwd_of", lambda pid: str(tmp_path),
        )
        monkeypatch.setattr(
            "src.proc_reconcile._read_agent_name_from_environ", lambda pid: None,
        )

        touched = reconcile_live_agents(db)
        expected = f"agent-{tmp_path.name}"
        assert touched == [expected]
        assert db.get_agent(expected)["pid"] == 4242

    def test_invalid_environ_value_falls_back(self, tmp_path, monkeypatch):
        from src.chat_db import ChatDB
        from src.proc_reconcile import reconcile_live_agents

        db = ChatDB(str(tmp_path / "chat.db"))

        monkeypatch.setattr(
            "src.proc_reconcile._iter_claude_pids", lambda marker="claude": [4242],
        )
        monkeypatch.setattr(
            "src.proc_reconcile._cwd_of", lambda pid: str(tmp_path),
        )
        monkeypatch.setattr(
            "src.proc_reconcile._read_agent_name_from_environ",
            lambda pid: "Not Valid",
        )

        touched = reconcile_live_agents(db)
        # validated_agent_name falls back; since basename(tmp_path) is
        # the fallback, that's the row that gets created.
        expected = f"agent-{tmp_path.name}"
        assert touched == [expected]


class TestReadAgentNameFromEnviron:
    """The environ-parsing helper handles real /proc data shape."""

    def test_parses_env_var_from_environ(self, tmp_path, monkeypatch):
        from src.proc_reconcile import _read_agent_name_from_environ

        # Build a fake /proc/<pid>/environ — null-separated key=value pairs.
        environ_data = b"PATH=/usr/bin\x00CLAUDE_AGENT_NAME=agent-foo\x00HOME=/h\x00"
        env_path = tmp_path / "environ"
        env_path.write_bytes(environ_data)

        # Patch the open path used internally — easiest via a wrapper helper.
        import src.proc_reconcile as pr
        orig_open = pr.open if hasattr(pr, "open") else open
        monkeypatch.setattr(
            "builtins.open",
            lambda p, mode="r": orig_open(env_path, mode) if "environ" in str(p) else orig_open(p, mode),
        )
        assert _read_agent_name_from_environ(4242) == "agent-foo"

    def test_returns_none_when_var_missing(self, tmp_path, monkeypatch):
        from src.proc_reconcile import _read_agent_name_from_environ

        environ_data = b"PATH=/usr/bin\x00HOME=/h\x00"
        env_path = tmp_path / "environ"
        env_path.write_bytes(environ_data)
        orig_open = open
        monkeypatch.setattr(
            "builtins.open",
            lambda p, mode="r": orig_open(env_path, mode) if "environ" in str(p) else orig_open(p, mode),
        )
        assert _read_agent_name_from_environ(4242) is None

    def test_returns_none_when_proc_missing(self):
        from src.proc_reconcile import _read_agent_name_from_environ
        # PID that won't have a /proc entry
        assert _read_agent_name_from_environ(99_999_999) is None
```

- [ ] **Step 5.2: Run tests to verify they fail**

```bash
.venv/bin/pytest tests/test_proc_reconcile.py -v -k "Environ or read_agent_name"
```

Expected: FAIL — `_read_agent_name_from_environ` doesn't exist; reconcile doesn't read environ.

- [ ] **Step 5.3: Modify `src/proc_reconcile.py`**

Add the import after line 16:

```python
from src.agent_name import validated_agent_name
```

Add the helper function after `_cwd_of` (around line 53):

```python
def _read_agent_name_from_environ(pid: int) -> str | None:
    """Return the value of CLAUDE_AGENT_NAME from /proc/<pid>/environ, if any.

    /proc/<pid>/environ is a null-separated bytes blob of KEY=VALUE pairs.
    Returns None when the file is unreadable, the variable is absent, or
    the value can't be decoded as UTF-8. Validation is the caller's job —
    this helper just extracts."""
    try:
        with open(f"/proc/{pid}/environ", "rb") as f:
            data = f.read()
    except (FileNotFoundError, PermissionError, ProcessLookupError, OSError):
        return None
    for entry in data.split(b"\x00"):
        if entry.startswith(b"CLAUDE_AGENT_NAME="):
            try:
                return entry.split(b"=", 1)[1].decode("utf-8")
            except UnicodeDecodeError:
                return None
    return None
```

Modify `reconcile_live_agents` — replace line 84:

```python
        name = "agent-" + PurePosixPath(cwd).name
```

with:

```python
        fallback = "agent-" + PurePosixPath(cwd).name
        env_name = _read_agent_name_from_environ(pid)
        name = validated_agent_name(env_name, fallback)
```

- [ ] **Step 5.4: Run tests to verify they pass**

```bash
.venv/bin/pytest tests/test_proc_reconcile.py -v
```

Expected: all PASS.

- [ ] **Step 5.5: Run full suite + line check**

```bash
.venv/bin/pytest tests/ -q && scripts/check-line-limit.sh
```

Expected: PASS. `src/proc_reconcile.py` should be ≤200 lines (currently 113, will grow ~20).

- [ ] **Step 5.6: Commit**

```bash
git add src/proc_reconcile.py tests/test_proc_reconcile.py
git commit -m "feat(proc-reconcile): read CLAUDE_AGENT_NAME from /proc/<pid>/environ

After a claude-chat restart, the proc-scan reconciler now reads each
live Claude process's environment for CLAUDE_AGENT_NAME and uses that
as the agent name when present. This lets two or more agents in the
same project directory each be re-attributed to the correct row,
rather than only the first one matching agent-<basename(cwd)>."
```

---

## Task 6: Email `spawn ... as <name>` syntax

**Files:**
- Create: `src/spawn_args.py`
- Create: `tests/test_spawn_args.py`
- Modify: `src/chat_handlers.py:139-148`
- Modify: `tests/test_chat_handlers.py`

- [ ] **Step 6.1: Write the failing test for the parser**

Create `tests/test_spawn_args.py`:

```python
"""Tests for src/spawn_args.py — meta-command argument parser for `spawn`."""
from src.spawn_args import parse_spawn_args


class TestParseSpawnArgs:
    def test_path_only(self):
        assert parse_spawn_args("/some/path") == ("/some/path", None, "")

    def test_path_and_instruction(self):
        assert parse_spawn_args("/some/path do something now") == (
            "/some/path", None, "do something now",
        )

    def test_path_as_name(self):
        assert parse_spawn_args("/some/path as agent-foo") == (
            "/some/path", "agent-foo", "",
        )

    def test_path_as_name_and_instruction(self):
        assert parse_spawn_args("/some/path as agent-foo do something") == (
            "/some/path", "agent-foo", "do something",
        )

    def test_empty_returns_empty_path(self):
        assert parse_spawn_args("") == ("", None, "")

    def test_only_whitespace_returns_empty_path(self):
        assert parse_spawn_args("   ") == ("", None, "")

    def test_as_keyword_only_recognized_at_position_two(self):
        # 'as' as part of an instruction should not trigger name parsing.
        assert parse_spawn_args("/path do as previously") == (
            "/path", None, "do as previously",
        )

    def test_as_without_following_token_treats_as_instruction(self):
        # If 'as' is the last token, there's no name to extract — fall
        # back to treating everything-after-path as instruction.
        assert parse_spawn_args("/path as") == ("/path", None, "as")
```

- [ ] **Step 6.2: Run the test to verify it fails**

```bash
.venv/bin/pytest tests/test_spawn_args.py -v
```

Expected: ImportError.

- [ ] **Step 6.3: Write the parser**

Create `src/spawn_args.py`:

```python
"""Parse the email `spawn` meta-command's argument string.

Supported syntax:
    spawn <path>
    spawn <path> <instruction>
    spawn <path> as <agent-name>
    spawn <path> as <agent-name> <instruction>

Returns a (project_dir, agent_name, instruction) triple. agent_name is
None when the `as <name>` clause is absent. The caller is responsible
for validating the name format via src.agent_name.validated_agent_name.
"""


def parse_spawn_args(raw: str) -> tuple[str, str | None, str]:
    tokens = raw.split()
    if not tokens:
        return "", None, ""
    project_dir = tokens[0]
    if len(tokens) >= 3 and tokens[1] == "as":
        agent_name = tokens[2]
        instruction = " ".join(tokens[3:])
    else:
        agent_name = None
        instruction = " ".join(tokens[1:])
    return project_dir, agent_name, instruction
```

- [ ] **Step 6.4: Run the test to verify it passes**

```bash
.venv/bin/pytest tests/test_spawn_args.py -v
```

Expected: all PASS.

- [ ] **Step 6.5: Write the failing test for `chat_handlers.py` integration**

Append to `tests/test_chat_handlers.py` (find the existing spawn-test class or add a new one):

```python
class TestSpawnAsName:
    """`spawn <path> as <name>` routes the name through to spawn_agent."""

    def _setup(self, tmp_path):
        # Mirror existing test fixtures in this file. If they use a
        # different setup, adapt accordingly.
        from src.chat_db import ChatDB
        db = ChatDB(str(tmp_path / "chat.db"))
        return db

    def test_as_name_passes_agent_name_to_spawner(self, tmp_path, monkeypatch):
        from src import chat_handlers

        db = self._setup(tmp_path)
        captured = {}

        def fake_spawn(*args, **kwargs):
            captured["agent_name"] = kwargs.get("agent_name")
            return ("agent-custom", 42)

        monkeypatch.setattr(chat_handlers, "spawn_agent", fake_spawn)
        monkeypatch.setattr(chat_handlers, "send_threaded_reply", lambda *a, **k: None)

        # Build a synthetic route + message + config matching what existing
        # spawn tests in this file use. Replace the placeholders with the
        # actual fixtures the existing tests rely on.
        route = _make_meta_route("spawn", f"{tmp_path} as agent-custom")
        message = _make_message()
        config = _make_config(tmp_path)

        chat_handlers.handle_chat_message(db, message, route, config)
        assert captured["agent_name"] == "agent-custom"

    def test_invalid_name_rejected_with_error_reply(self, tmp_path, monkeypatch):
        from src import chat_handlers

        db = self._setup(tmp_path)
        replies = []

        monkeypatch.setattr(
            chat_handlers, "send_threaded_reply",
            lambda config, message, body, **kw: replies.append((kw.get("tag"), body)),
        )
        monkeypatch.setattr(
            chat_handlers, "spawn_agent",
            lambda *a, **k: pytest.fail("spawn_agent should not be called"),
        )

        route = _make_meta_route("spawn", f"{tmp_path} as Not-Valid")
        message = _make_message()
        config = _make_config(tmp_path)

        chat_handlers.handle_chat_message(db, message, route, config)
        assert any(tag == "Error" and "invalid agent name" in body for tag, body in replies)
```

The exact dispatch entry point and helpers (`_make_meta_route`, `_make_message`, `_make_config`, `handle_chat_message`) depend on the existing test conventions in `tests/test_chat_handlers.py`. Read the top of that file before writing this test — copy the fixtures and structure used by the existing `spawn` tests there.

- [ ] **Step 6.6: Run the test to verify it fails**

```bash
.venv/bin/pytest tests/test_chat_handlers.py::TestSpawnAsName -v
```

Expected: FAIL — handler doesn't parse `as <name>` yet.

- [ ] **Step 6.7: Modify `src/chat_handlers.py`**

Add imports near the top (with the other `src.` imports):

```python
from src.agent_name import validated_agent_name
from src.spawn_args import parse_spawn_args
```

Replace lines 139-148 (the `spawn` branch) with:

```python
    elif route.meta_command == "spawn":
        project_dir, agent_name, instruction = parse_spawn_args(route.meta_args)
        if not project_dir:
            send_threaded_reply(
                config, message,
                "Usage: spawn <name-or-path> [as <agent-name>] [instruction]",
                tag="Error", chat_db=chat_db, kind="error",
            )
            return
        if agent_name is not None and validated_agent_name(agent_name, "") != agent_name:
            send_threaded_reply(
                config, message,
                f"Spawn rejected: invalid agent name {agent_name!r}",
                tag="Error", chat_db=chat_db, kind="error",
            )
            return
```

And update the `spawn_agent(...)` call below it to pass `agent_name=agent_name`. Find the existing call (around line 150-158) and add the kwarg:

```python
            name, pid = spawn_agent(
                chat_db, project_dir, config["chat_url"], instruction=instruction,
                claude_bin=config["claude_bin"],
                allowed_base=config.get("claude_cwd"),
                yolo=config.get("claude_yolo", False),
                extra_env=config.get("claude_extra_env") or None,
                model=config.get("claude_model"), effort=config.get("claude_effort"),
                max_budget_usd=config.get("claude_max_budget_usd"),
                agent_name=agent_name,
            )
```

- [ ] **Step 6.8: Run tests + line check**

```bash
.venv/bin/pytest tests/test_chat_handlers.py tests/test_spawn_args.py -v
scripts/check-line-limit.sh
```

Expected: all PASS. `src/chat_handlers.py` should still be ≤200 lines (currently 188; the change adds ~6 net lines after extracting parsing into `spawn_args.py`).

- [ ] **Step 6.9: Run full suite**

```bash
.venv/bin/pytest tests/ -q
```

Expected: all PASS.

- [ ] **Step 6.10: Commit**

```bash
git add src/spawn_args.py src/chat_handlers.py tests/test_spawn_args.py tests/test_chat_handlers.py
git commit -m "feat(spawn): support 'spawn <path> as <agent-name>' syntax

Email command 'spawn' now accepts an optional 'as <name>' clause to
request a non-default agent name. Parsing is split out to
src/spawn_args.py to keep chat_handlers.py under the 200-line cap.
Invalid names are rejected with a clear Error reply."
```

---

## Task 7: Documentation

**Files:**
- Modify: `README.md`
- Modify: `website/index.html`
- Modify: `website/fa/index.html`

- [ ] **Step 7.1: Read each file to find the right insertion points**

```bash
grep -n "spawn" README.md website/index.html website/fa/index.html
```

The README documents email commands; the website mirrors that section. Insertion point: wherever the existing `spawn <path>` documentation lives.

- [ ] **Step 7.2: Update `README.md`**

Find the existing `spawn` documentation and extend it. Replace any line documenting just `spawn <path>` with the full syntax. Example pattern:

````markdown
- `spawn <path>` — start a Claude Code agent in the given project directory. The agent's bus name defaults to `agent-<basename(path)>`.
- `spawn <path> as <agent-name> [instruction]` — same, but register the agent under an explicit name. Use this to run **multiple agents in the same project** (e.g. one main, one optimizer). Names must match `^agent-[a-z0-9][a-z0-9_-]{0,57}$`.

The new agent inherits `CLAUDE_AGENT_NAME` from its parent process, so the SessionStart hook and proc-reconcile both see the same identity.
````

Then bump the test count if your README references it (search for `1108` and update to the new total after Tasks 1-6).

- [ ] **Step 7.3: Update `website/index.html`**

Find the same content in the English website and update it analogously. The website file mirrors README content, so paste the same prose with the project's HTML conventions.

- [ ] **Step 7.4: Update `website/fa/index.html` (Farsi)**

Translate the same addition. If the existing Farsi spawn documentation uses specific terminology, mirror it. Suggested wording:

```
- `spawn <path>` — یک عامل کلود کد در مسیر داده‌شده راه‌اندازی می‌کند. نام پیش‌فرض عامل: `agent-<نام-پوشه>`.
- `spawn <path> as <agent-name> [instruction]` — مانند بالا، اما عامل را با نامی مشخص ثبت می‌کند. برای **چند عامل در یک پروژه** کاربرد دارد. نام باید با الگوی `^agent-[a-z0-9][a-z0-9_-]{0,57}$` مطابقت داشته باشد.
```

If your project has an established Farsi style, defer to it — the substance is what matters.

- [ ] **Step 7.5: Run line-limit check on docs**

```bash
scripts/check-line-limit.sh
```

Expected: PASS. (HTML files often exceed 200 lines; the check-line-limit script likely exempts them — confirm by reading the script if uncertain.)

- [ ] **Step 7.6: Commit**

```bash
git add README.md website/index.html website/fa/index.html
git commit -m "docs: 'spawn ... as <agent-name>' for multi-agent projects

Documents the new email syntax and notes that CLAUDE_AGENT_NAME is the
underlying mechanism. Mirrors the addition across README and the
English/Farsi website pages per the project's lockstep policy."
```

---

## Task 8: Final verification

- [ ] **Step 8.1: Run the full test suite**

```bash
.venv/bin/pytest tests/ -q
```

Expected: all PASS. New total ≈ 1108 + ~30 new tests.

- [ ] **Step 8.2: Verify 100% coverage**

```bash
.venv/bin/pytest tests/ --cov=src --cov=chat --cov=scripts --cov-report=term-missing
```

Expected: 100% on production code (per `.coveragerc`).

- [ ] **Step 8.3: Line-limit check**

```bash
scripts/check-line-limit.sh
```

Expected: PASS.

- [ ] **Step 8.4: Manual smoke test**

In one terminal, set `CLAUDE_AGENT_NAME=agent-test-a` and start a Claude session in some project directory. In a second terminal, set `CLAUDE_AGENT_NAME=agent-test-b` and start another session in the same directory. Then:

```bash
.venv/bin/python -c "
import sqlite3
con = sqlite3.connect('claude-chat.db')
con.row_factory = sqlite3.Row
for r in con.execute(\"SELECT name, pid, project_path FROM agents WHERE name LIKE 'agent-test-%'\"):
    print(dict(r))
"
```

Expected: both rows present with distinct PIDs.

- [ ] **Step 8.5: Coordinate with frontend**

Send a heads-up to `agent-Claude-Email-App` per CLAUDE.md (the bus contract is unchanged, but the dashboard may now show >1 row per `project_path`):

```
Multi-agent-per-project landed in claude-email. Bus tool surface unchanged;
the dashboard may now show multiple rows for the same project_path. Please
verify rendering doesn't assume project_path uniqueness.
```

- [ ] **Step 8.6: Open the PR**

Run `/simplify` on the diff first per the saved feedback rule, then open the PR.

---

## Self-Review

**1. Spec coverage:**
- ✅ `validated_agent_name` helper → Task 1.
- ✅ Hook honors `CLAUDE_AGENT_NAME` → Task 3.
- ✅ Spawner kwarg + env injection → Task 4.
- ✅ proc_reconcile reads /proc/<pid>/environ → Task 5.
- ✅ Drop AgentProjectTaken raise → Task 2.
- ✅ Email `spawn ... as <name>` → Task 6.
- ✅ MCP `chat_register` no-change → covered by omission (no task touches it).
- ✅ Documentation mirror across README + en + fa → Task 7.
- ✅ Frontend coordination → Task 8.5.
- ✅ Test/coverage/line-limit verification → Task 8.

**2. Placeholder scan:** No "TBD"/"TODO" strings; every code step has concrete code; every command has expected output. The `tests/test_chat_handlers.py` test mentions `_make_meta_route`, `_make_message`, `_make_config` as fixtures-to-mirror-from-existing-tests rather than inventing them; that's a deliberate "follow the existing pattern" instruction, not a placeholder, because we don't know the exact fixture names without reading the file.

**3. Type consistency:**
- `validated_agent_name(raw: str | None, fallback: str) -> str` — same signature in Tasks 1, 3, 4, 5, 6.
- `parse_spawn_args(raw: str) -> tuple[str, str | None, str]` — same in Tasks 6.
- `_read_agent_name_from_environ(pid: int) -> str | None` — same in Task 5.
- Spawner kwarg name: `agent_name` — same in Tasks 4 and 6.
- Env var name: `CLAUDE_AGENT_NAME` — same everywhere.

No drift.
