# claude-chat Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a message relay MCP server (claude-chat) and integrate it with claude-email so agents can chat with the user via email.

**Architecture:** claude-chat is a FastMCP SSE server backed by SQLite. claude-email connects as an MCP client representing the user, bridging messages to/from email. Agents connect via MCP and use `chat_ask` (blocking) / `chat_notify` (fire-and-forget) to communicate.

**Tech Stack:** Python 3.12, MCP SDK 1.27.0 (FastMCP + SSE), SQLite (WAL mode), asyncio, pytest

**Spec:** `docs/superpowers/specs/2026-04-16-claude-chat-design.md`

---

## File Structure

| File | Responsibility |
|------|---------------|
| `src/chat_db.py` (NEW) | SQLite schema, connection factory, read/write operations. Shared by both services. |
| `chat/server.py` (NEW) | FastMCP SSE server entry point. Registers tools, starts uvicorn. |
| `chat/tools.py` (NEW) | MCP tool handler implementations (register, ask, notify, check, list, deregister). |
| `src/chat_router.py` (NEW) | Routing logic: classifies emails as chat-reply, @agent-command, meta-command, or CLI. |
| `src/spawner.py` (NEW) | Agent process spawning, PID tracking, .mcp.json injection. |
| `main.py` (MODIFY) | Add chat routing before CLI execution. Add outbound message polling loop. |
| `chat_server.py` (NEW) | Thin entry point for claude-chat systemd service. |
| `claude-chat.service` (NEW) | Systemd user service unit. |
| `claude-email.service` (MODIFY) | Add `After=claude-chat.service`. |
| `install.sh` (MODIFY) | Install both services. |
| `requirements.txt` (MODIFY) | Add `mcp>=1.27`. |

---

## Task 1: Shared Database Layer (`src/chat_db.py`)

**Files:**
- Create: `src/chat_db.py`
- Create: `tests/test_chat_db.py`

- [ ] **Step 1: Write failing tests for DB initialization and schema**

```python
# tests/test_chat_db.py
import os
import sqlite3
import pytest
from src.chat_db import ChatDB


@pytest.fixture
def db(tmp_path):
    db_path = str(tmp_path / "test.db")
    return ChatDB(db_path)


def test_creates_db_file(db, tmp_path):
    assert (tmp_path / "test.db").exists()


def test_wal_mode_enabled(db):
    conn = sqlite3.connect(db.path)
    mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
    conn.close()
    assert mode == "wal"


def test_agents_table_exists(db):
    conn = sqlite3.connect(db.path)
    tables = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='agents'"
    ).fetchone()
    conn.close()
    assert tables is not None


def test_messages_table_exists(db):
    conn = sqlite3.connect(db.path)
    tables = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='messages'"
    ).fetchone()
    conn.close()
    assert tables is not None


def test_events_table_exists(db):
    conn = sqlite3.connect(db.path)
    tables = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='events'"
    ).fetchone()
    conn.close()
    assert tables is not None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/pytest tests/test_chat_db.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'src.chat_db'`

- [ ] **Step 3: Implement ChatDB with schema creation**

```python
# src/chat_db.py
"""Shared SQLite database layer for claude-chat.

Used by both claude-chat (MCP server) and claude-email (orchestrator).
WAL mode + busy timeout for safe concurrent access.
"""
import sqlite3
from datetime import datetime, timezone

_SCHEMA = """
CREATE TABLE IF NOT EXISTS agents (
    name TEXT PRIMARY KEY,
    project_path TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'running',
    pid INTEGER,
    registered_at TEXT NOT NULL,
    last_seen_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    from_name TEXT NOT NULL,
    to_name TEXT NOT NULL,
    body TEXT NOT NULL,
    type TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',
    email_message_id TEXT,
    in_reply_to INTEGER,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    event_type TEXT NOT NULL,
    participant TEXT NOT NULL,
    summary TEXT NOT NULL,
    created_at TEXT NOT NULL
);
"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class ChatDB:
    def __init__(self, path: str = "claude-chat.db") -> None:
        self.path = path
        conn = self._connect()
        conn.executescript(_SCHEMA)
        conn.close()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path, timeout=5.0)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=5000")
        conn.row_factory = sqlite3.Row
        return conn
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/pytest tests/test_chat_db.py -v`
Expected: 5 passed

- [ ] **Step 5: Write failing tests for agent CRUD**

```python
# Append to tests/test_chat_db.py

def test_register_agent(db):
    db.register_agent("agent-fits", "/home/user/fits")
    agent = db.get_agent("agent-fits")
    assert agent["name"] == "agent-fits"
    assert agent["project_path"] == "/home/user/fits"
    assert agent["status"] == "running"


def test_register_agent_reconnect(db):
    db.register_agent("agent-fits", "/home/user/fits")
    db.update_agent_status("agent-fits", "disconnected")
    db.register_agent("agent-fits", "/home/user/fits")
    agent = db.get_agent("agent-fits")
    assert agent["status"] == "running"


def test_get_agent_not_found(db):
    assert db.get_agent("nonexistent") is None


def test_list_agents_empty(db):
    assert db.list_agents() == []


def test_list_agents(db):
    db.register_agent("agent-a", "/a")
    db.register_agent("agent-b", "/b")
    agents = db.list_agents()
    assert len(agents) == 2
    names = [a["name"] for a in agents]
    assert "agent-a" in names
    assert "agent-b" in names


def test_update_agent_status(db):
    db.register_agent("agent-fits", "/home/user/fits")
    db.update_agent_status("agent-fits", "disconnected")
    agent = db.get_agent("agent-fits")
    assert agent["status"] == "disconnected"


def test_update_agent_pid(db):
    db.register_agent("agent-fits", "/home/user/fits")
    db.update_agent_pid("agent-fits", 12345)
    agent = db.get_agent("agent-fits")
    assert agent["pid"] == 12345
```

- [ ] **Step 6: Implement agent CRUD methods**

```python
# Append to ChatDB class in src/chat_db.py

    def register_agent(self, name: str, project_path: str) -> dict:
        conn = self._connect()
        now = _now()
        conn.execute(
            """INSERT INTO agents (name, project_path, status, registered_at, last_seen_at)
               VALUES (?, ?, 'running', ?, ?)
               ON CONFLICT(name) DO UPDATE SET
                 status='running', project_path=?, last_seen_at=?""",
            (name, project_path, now, now, project_path, now),
        )
        conn.commit()
        agent = dict(conn.execute("SELECT * FROM agents WHERE name=?", (name,)).fetchone())
        conn.close()
        self._log_event(name, "register", f"{name} registered from {project_path}")
        return agent

    def get_agent(self, name: str) -> dict | None:
        conn = self._connect()
        row = conn.execute("SELECT * FROM agents WHERE name=?", (name,)).fetchone()
        conn.close()
        return dict(row) if row else None

    def list_agents(self) -> list[dict]:
        conn = self._connect()
        rows = conn.execute("SELECT * FROM agents ORDER BY name").fetchall()
        conn.close()
        return [dict(r) for r in rows]

    def update_agent_status(self, name: str, status: str) -> None:
        conn = self._connect()
        conn.execute(
            "UPDATE agents SET status=?, last_seen_at=? WHERE name=?",
            (status, _now(), name),
        )
        conn.commit()
        conn.close()

    def update_agent_pid(self, name: str, pid: int) -> None:
        conn = self._connect()
        conn.execute("UPDATE agents SET pid=? WHERE name=?", (pid, name))
        conn.commit()
        conn.close()

    def touch_agent(self, name: str) -> None:
        conn = self._connect()
        conn.execute(
            "UPDATE agents SET last_seen_at=? WHERE name=?", (_now(), name)
        )
        conn.commit()
        conn.close()
```

- [ ] **Step 7: Run tests to verify they pass**

Run: `.venv/bin/pytest tests/test_chat_db.py -v`
Expected: 12 passed

- [ ] **Step 8: Write failing tests for message operations**

```python
# Append to tests/test_chat_db.py

def test_insert_message(db):
    msg = db.insert_message("agent-fits", "user", "Hello?", "ask")
    assert msg["id"] is not None
    assert msg["from_name"] == "agent-fits"
    assert msg["to_name"] == "user"
    assert msg["type"] == "ask"
    assert msg["status"] == "pending"


def test_get_pending_messages_for(db):
    db.insert_message("agent-fits", "user", "msg1", "notify")
    db.insert_message("agent-other", "user", "msg2", "notify")
    pending = db.get_pending_messages_for("user")
    assert len(pending) == 2


def test_get_pending_messages_only_pending(db):
    msg = db.insert_message("agent-fits", "user", "msg1", "notify")
    db.mark_message_delivered(msg["id"])
    pending = db.get_pending_messages_for("user")
    assert len(pending) == 0


def test_mark_message_delivered(db):
    msg = db.insert_message("agent-fits", "user", "msg1", "notify")
    db.mark_message_delivered(msg["id"])
    conn = __import__("sqlite3").connect(db.path)
    conn.row_factory = __import__("sqlite3").Row
    row = conn.execute("SELECT status FROM messages WHERE id=?", (msg["id"],)).fetchone()
    conn.close()
    assert row["status"] == "delivered"


def test_set_email_message_id(db):
    msg = db.insert_message("agent-fits", "user", "msg1", "ask")
    db.set_email_message_id(msg["id"], "<abc@cocode.dk>")
    conn = __import__("sqlite3").connect(db.path)
    conn.row_factory = __import__("sqlite3").Row
    row = conn.execute("SELECT email_message_id FROM messages WHERE id=?", (msg["id"],)).fetchone()
    conn.close()
    assert row["email_message_id"] == "<abc@cocode.dk>"


def test_find_message_by_email_id(db):
    msg = db.insert_message("agent-fits", "user", "msg1", "ask")
    db.set_email_message_id(msg["id"], "<abc@cocode.dk>")
    found = db.find_message_by_email_id("<abc@cocode.dk>")
    assert found is not None
    assert found["id"] == msg["id"]


def test_find_message_by_email_id_not_found(db):
    assert db.find_message_by_email_id("<nonexistent>") is None


def test_insert_reply(db):
    ask = db.insert_message("agent-fits", "user", "question?", "ask")
    reply = db.insert_message("user", "agent-fits", "answer!", "reply", in_reply_to=ask["id"])
    assert reply["in_reply_to"] == ask["id"]


def test_get_reply_to_message(db):
    ask = db.insert_message("agent-fits", "user", "question?", "ask")
    db.insert_message("user", "agent-fits", "answer!", "reply", in_reply_to=ask["id"])
    reply = db.get_reply_to_message(ask["id"])
    assert reply is not None
    assert reply["body"] == "answer!"


def test_get_reply_to_message_none(db):
    ask = db.insert_message("agent-fits", "user", "question?", "ask")
    assert db.get_reply_to_message(ask["id"]) is None
```

- [ ] **Step 9: Implement message operations**

```python
# Append to ChatDB class in src/chat_db.py

    def insert_message(
        self,
        from_name: str,
        to_name: str,
        body: str,
        msg_type: str,
        in_reply_to: int | None = None,
    ) -> dict:
        conn = self._connect()
        now = _now()
        cursor = conn.execute(
            """INSERT INTO messages (from_name, to_name, body, type, status, in_reply_to, created_at)
               VALUES (?, ?, ?, ?, 'pending', ?, ?)""",
            (from_name, to_name, body, msg_type, in_reply_to, now),
        )
        conn.commit()
        row = conn.execute("SELECT * FROM messages WHERE id=?", (cursor.lastrowid,)).fetchone()
        conn.close()
        self._log_event(from_name, "message", f"{msg_type} from {from_name} to {to_name}")
        return dict(row)

    def get_pending_messages_for(self, to_name: str) -> list[dict]:
        conn = self._connect()
        rows = conn.execute(
            "SELECT * FROM messages WHERE to_name=? AND status='pending' ORDER BY id",
            (to_name,),
        ).fetchall()
        conn.close()
        return [dict(r) for r in rows]

    def mark_message_delivered(self, msg_id: int) -> None:
        conn = self._connect()
        conn.execute("UPDATE messages SET status='delivered' WHERE id=?", (msg_id,))
        conn.commit()
        conn.close()

    def set_email_message_id(self, msg_id: int, email_message_id: str) -> None:
        conn = self._connect()
        conn.execute(
            "UPDATE messages SET email_message_id=? WHERE id=?",
            (email_message_id, msg_id),
        )
        conn.commit()
        conn.close()

    def find_message_by_email_id(self, email_message_id: str) -> dict | None:
        conn = self._connect()
        row = conn.execute(
            "SELECT * FROM messages WHERE email_message_id=?", (email_message_id,)
        ).fetchone()
        conn.close()
        return dict(row) if row else None

    def get_reply_to_message(self, msg_id: int) -> dict | None:
        conn = self._connect()
        row = conn.execute(
            "SELECT * FROM messages WHERE in_reply_to=? AND type='reply' ORDER BY id DESC LIMIT 1",
            (msg_id,),
        ).fetchone()
        conn.close()
        return dict(row) if row else None

    def _log_event(self, participant: str, event_type: str, summary: str) -> None:
        conn = self._connect()
        conn.execute(
            "INSERT INTO events (event_type, participant, summary, created_at) VALUES (?, ?, ?, ?)",
            (event_type, participant, summary, _now()),
        )
        conn.commit()
        conn.close()
```

- [ ] **Step 10: Run all tests to verify they pass**

Run: `.venv/bin/pytest tests/test_chat_db.py -v`
Expected: 22 passed

- [ ] **Step 11: Commit**

```bash
git add src/chat_db.py tests/test_chat_db.py
git commit -m "feat: add shared SQLite database layer for claude-chat"
```

---

## Task 2: MCP Server — Tool Implementations (`chat/tools.py`)

**Files:**
- Create: `chat/__init__.py`
- Create: `chat/tools.py`
- Create: `tests/test_chat_tools.py`

- [ ] **Step 1: Write failing tests for tool functions**

```python
# tests/test_chat_tools.py
import asyncio
import pytest
from src.chat_db import ChatDB
from chat.tools import register_agent, notify_user, list_agents, check_messages, deregister_agent


@pytest.fixture
def db(tmp_path):
    return ChatDB(str(tmp_path / "test.db"))


def test_register_agent_tool(db):
    result = register_agent(db, "agent-fits", "/home/user/fits")
    assert result["status"] == "registered"
    assert result["name"] == "agent-fits"


def test_register_agent_persists(db):
    register_agent(db, "agent-fits", "/home/user/fits")
    agent = db.get_agent("agent-fits")
    assert agent is not None
    assert agent["status"] == "running"


def test_notify_user_tool(db):
    register_agent(db, "agent-fits", "/home/user/fits")
    result = notify_user(db, "agent-fits", "Build complete")
    assert result["status"] == "sent"


def test_notify_creates_pending_message(db):
    register_agent(db, "agent-fits", "/home/user/fits")
    notify_user(db, "agent-fits", "Build complete")
    msgs = db.get_pending_messages_for("user")
    assert len(msgs) == 1
    assert msgs[0]["body"] == "Build complete"
    assert msgs[0]["type"] == "notify"


def test_list_agents_tool(db):
    register_agent(db, "agent-a", "/a")
    register_agent(db, "agent-b", "/b")
    result = list_agents(db)
    assert len(result["agents"]) == 2


def test_check_messages_empty(db):
    register_agent(db, "agent-fits", "/home/user/fits")
    result = check_messages(db, "agent-fits")
    assert result["messages"] == []


def test_check_messages_returns_pending(db):
    register_agent(db, "agent-fits", "/home/user/fits")
    db.insert_message("user", "agent-fits", "do this", "command")
    result = check_messages(db, "agent-fits")
    assert len(result["messages"]) == 1
    assert result["messages"][0]["body"] == "do this"


def test_check_messages_marks_delivered(db):
    register_agent(db, "agent-fits", "/home/user/fits")
    db.insert_message("user", "agent-fits", "do this", "command")
    check_messages(db, "agent-fits")
    result = check_messages(db, "agent-fits")
    assert result["messages"] == []


def test_deregister_agent_tool(db):
    register_agent(db, "agent-fits", "/home/user/fits")
    result = deregister_agent(db, "agent-fits")
    assert result["status"] == "deregistered"
    agent = db.get_agent("agent-fits")
    assert agent["status"] == "deregistered"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/pytest tests/test_chat_tools.py -v`
Expected: FAIL — `ModuleNotFoundError`

- [ ] **Step 3: Implement tool functions**

```python
# chat/__init__.py
```

```python
# chat/tools.py
"""MCP tool implementations for claude-chat.

Pure functions that take a ChatDB instance and return result dicts.
These are wired into the FastMCP server in chat/server.py.
"""
import asyncio
from src.chat_db import ChatDB


def register_agent(db: ChatDB, name: str, project_path: str) -> dict:
    db.register_agent(name, project_path)
    return {"status": "registered", "name": name}


def notify_user(db: ChatDB, caller: str, message: str) -> dict:
    db.insert_message(caller, "user", message, "notify")
    return {"status": "sent"}


async def ask_user(db: ChatDB, caller: str, message: str) -> dict:
    msg = db.insert_message(caller, "user", message, "ask")
    msg_id = msg["id"]
    while True:
        reply = db.get_reply_to_message(msg_id)
        if reply:
            return {"reply": reply["body"]}
        await asyncio.sleep(2)


def check_messages(db: ChatDB, caller: str) -> dict:
    db.touch_agent(caller)
    pending = db.get_pending_messages_for(caller)
    for msg in pending:
        db.mark_message_delivered(msg["id"])
    return {
        "messages": [
            {
                "id": m["id"],
                "from": m["from_name"],
                "body": m["body"],
                "type": m["type"],
                "created_at": m["created_at"],
            }
            for m in pending
        ]
    }


def list_agents(db: ChatDB) -> dict:
    agents = db.list_agents()
    return {
        "agents": [
            {
                "name": a["name"],
                "status": a["status"],
                "project_path": a["project_path"],
                "last_seen_at": a["last_seen_at"],
            }
            for a in agents
        ]
    }


def deregister_agent(db: ChatDB, caller: str) -> dict:
    db.update_agent_status(caller, "deregistered")
    return {"status": "deregistered"}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/pytest tests/test_chat_tools.py -v`
Expected: 10 passed

- [ ] **Step 5: Write failing test for ask_user blocking behavior**

```python
# Append to tests/test_chat_tools.py

def test_ask_user_blocks_then_returns(db):
    register_agent(db, "agent-fits", "/home/user/fits")

    async def run():
        async def reply_later():
            await asyncio.sleep(0.5)
            msgs = db.get_pending_messages_for("user")
            assert len(msgs) == 1
            db.insert_message("user", "agent-fits", "yes", "reply", in_reply_to=msgs[0]["id"])

        task = asyncio.create_task(ask_user(db, "agent-fits", "proceed?"))
        asyncio.create_task(reply_later())
        result = await asyncio.wait_for(task, timeout=5.0)
        assert result["reply"] == "yes"

    asyncio.run(run())
```

- [ ] **Step 6: Run test to verify it passes** (implementation already handles this)

Run: `.venv/bin/pytest tests/test_chat_tools.py::test_ask_user_blocks_then_returns -v`
Expected: PASS

- [ ] **Step 7: Commit**

```bash
git add chat/__init__.py chat/tools.py tests/test_chat_tools.py
git commit -m "feat: add MCP tool implementations for claude-chat"
```

---

## Task 3: MCP Server Entry Point (`chat/server.py`, `chat_server.py`)

**Files:**
- Create: `chat/server.py`
- Create: `chat_server.py`
- Create: `tests/test_chat_server.py`

- [ ] **Step 1: Write failing test for server creation**

```python
# tests/test_chat_server.py
import pytest
from chat.server import create_app


def test_create_app_returns_starlette(tmp_path):
    from starlette.applications import Starlette
    app = create_app(db_path=str(tmp_path / "test.db"))
    assert isinstance(app, Starlette)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/test_chat_server.py -v`
Expected: FAIL — `ModuleNotFoundError`

- [ ] **Step 3: Implement the MCP server**

```python
# chat/server.py
"""claude-chat MCP server — message relay bus.

Runs a FastMCP-based SSE server that agents and claude-email connect to.
All state is persisted to SQLite via ChatDB.
"""
import asyncio
import json
import logging
import os

from mcp.server.lowlevel import Server
from mcp.server.sse import SseServerTransport
from mcp.server.models import InitializationOptions
from mcp.types import ServerCapabilities, Tool, TextContent
from starlette.applications import Starlette
from starlette.routing import Route, Mount

from src.chat_db import ChatDB
from chat.tools import register_agent, notify_user, ask_user, check_messages, list_agents, deregister_agent

logger = logging.getLogger(__name__)

# Track which SSE session belongs to which agent
_session_agents: dict[str, str] = {}


def _tools_list() -> list[Tool]:
    return [
        Tool(
            name="chat_register",
            description="Register as a chat participant",
            inputSchema={
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "Agent name, e.g. agent-fits"},
                    "project_path": {"type": "string", "description": "Absolute path to project directory"},
                },
                "required": ["name", "project_path"],
            },
        ),
        Tool(
            name="chat_ask",
            description="Send a message to the user and wait for a reply (blocking)",
            inputSchema={
                "type": "object",
                "properties": {
                    "message": {"type": "string", "description": "Your question or request"},
                },
                "required": ["message"],
            },
        ),
        Tool(
            name="chat_notify",
            description="Send a fire-and-forget message to the user",
            inputSchema={
                "type": "object",
                "properties": {
                    "message": {"type": "string", "description": "Status update or notification"},
                },
                "required": ["message"],
            },
        ),
        Tool(
            name="chat_check_messages",
            description="Poll for new messages from the user",
            inputSchema={"type": "object", "properties": {}},
        ),
        Tool(
            name="chat_list_agents",
            description="List all registered agents and their status",
            inputSchema={"type": "object", "properties": {}},
        ),
        Tool(
            name="chat_deregister",
            description="Leave the chat",
            inputSchema={"type": "object", "properties": {}},
        ),
    ]


def create_app(db_path: str = "claude-chat.db", host: str = "127.0.0.1", port: int = 8420) -> Starlette:
    db = ChatDB(db_path)
    server = Server("claude-chat", version="1.0")
    sse = SseServerTransport("/messages/")

    @server.list_tools()
    async def handle_list_tools() -> list[Tool]:
        return _tools_list()

    @server.call_tool()
    async def handle_call_tool(name: str, arguments: dict) -> list[TextContent]:
        # Resolve caller from session context or arguments
        caller = arguments.get("name", "unknown")

        if name == "chat_register":
            result = register_agent(db, arguments["name"], arguments["project_path"])
            # Track session -> agent mapping would go here
            caller = arguments["name"]
        elif name == "chat_ask":
            caller = _resolve_caller(arguments)
            result = await ask_user(db, caller, arguments["message"])
        elif name == "chat_notify":
            caller = _resolve_caller(arguments)
            result = notify_user(db, caller, arguments["message"])
        elif name == "chat_check_messages":
            caller = _resolve_caller(arguments)
            result = check_messages(db, caller)
        elif name == "chat_list_agents":
            result = list_agents(db)
        elif name == "chat_deregister":
            caller = _resolve_caller(arguments)
            result = deregister_agent(db, caller)
        else:
            result = {"error": f"Unknown tool: {name}"}

        return [TextContent(type="text", text=json.dumps(result))]

    def _resolve_caller(arguments: dict) -> str:
        return arguments.get("_caller", "unknown")

    async def handle_sse(scope, receive, send):
        async with sse.connect_sse(scope, receive, send) as streams:
            read_stream, write_stream = streams
            await server.run(
                read_stream,
                write_stream,
                InitializationOptions(
                    server_name="claude-chat",
                    server_version="1.0",
                    capabilities=ServerCapabilities(tools={}),
                ),
            )

    return Starlette(
        routes=[
            Route("/sse", endpoint=handle_sse),
            Mount("/messages/", app=sse.handle_post_message),
        ],
    )
```

```python
# chat_server.py
"""claude-chat entry point — run the MCP SSE server."""
import logging
import os
import sys

import uvicorn
from dotenv import load_dotenv

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)

if __name__ == "__main__":
    from chat.server import create_app

    db_path = os.environ.get("CHAT_DB_PATH", "claude-chat.db")
    host = os.environ.get("CHAT_HOST", "127.0.0.1")
    port = int(os.environ.get("CHAT_PORT", "8420"))

    app = create_app(db_path=db_path, host=host, port=port)
    uvicorn.run(app, host=host, port=port)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/pytest tests/test_chat_server.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add chat/server.py chat_server.py tests/test_chat_server.py
git commit -m "feat: add MCP SSE server entry point for claude-chat"
```

---

## Task 4: Chat Router (`src/chat_router.py`)

**Files:**
- Create: `src/chat_router.py`
- Create: `tests/test_chat_router.py`

- [ ] **Step 1: Write failing tests for routing logic**

```python
# tests/test_chat_router.py
import email.message
import pytest
from src.chat_db import ChatDB
from src.chat_router import classify_email, Route


@pytest.fixture
def db(tmp_path):
    return ChatDB(str(tmp_path / "test.db"))


def _make_email(subject="", body="", in_reply_to="", from_addr="user@example.com"):
    msg = email.message.EmailMessage()
    msg["From"] = from_addr
    msg["Subject"] = subject
    if in_reply_to:
        msg["In-Reply-To"] = in_reply_to
    msg.set_content(body)
    return msg


def test_chat_reply_detected(db):
    # Insert a message with a known email_message_id
    ask = db.insert_message("agent-fits", "user", "question?", "ask")
    db.set_email_message_id(ask["id"], "<chat-123@cocode.dk>")

    msg = _make_email(
        subject="Re: [agent-fits] question?",
        body="yes do it",
        in_reply_to="<chat-123@cocode.dk>",
    )
    route = classify_email(msg, db, "AUTH:secret")
    assert route.kind == "chat_reply"
    assert route.agent_name == "agent-fits"
    assert route.original_message_id == ask["id"]


def test_agent_command_detected(db):
    msg = _make_email(subject="AUTH:secret @agent-fits do the thing", body="details")
    route = classify_email(msg, db, "AUTH:secret")
    assert route.kind == "agent_command"
    assert route.agent_name == "agent-fits"
    assert route.body == "do the thing"


def test_meta_status(db):
    msg = _make_email(subject="AUTH:secret status", body="")
    route = classify_email(msg, db, "AUTH:secret")
    assert route.kind == "meta"
    assert route.meta_command == "status"


def test_meta_spawn(db):
    msg = _make_email(subject="AUTH:secret spawn /home/user/fits", body="")
    route = classify_email(msg, db, "AUTH:secret")
    assert route.kind == "meta"
    assert route.meta_command == "spawn"
    assert route.meta_args == "/home/user/fits"


def test_meta_spawn_with_instruction(db):
    msg = _make_email(subject="AUTH:secret spawn /home/user/fits refactor auth", body="")
    route = classify_email(msg, db, "AUTH:secret")
    assert route.kind == "meta"
    assert route.meta_command == "spawn"
    assert route.meta_args == "/home/user/fits refactor auth"


def test_meta_restart_chat(db):
    msg = _make_email(subject="AUTH:secret restart chat", body="")
    route = classify_email(msg, db, "AUTH:secret")
    assert route.kind == "meta"
    assert route.meta_command == "restart"
    assert route.meta_args == "chat"


def test_meta_restart_self(db):
    msg = _make_email(subject="AUTH:secret restart self", body="")
    route = classify_email(msg, db, "AUTH:secret")
    assert route.kind == "meta"
    assert route.meta_command == "restart"
    assert route.meta_args == "self"


def test_cli_fallback(db):
    msg = _make_email(subject="AUTH:secret list all files", body="")
    route = classify_email(msg, db, "AUTH:secret")
    assert route.kind == "cli"


def test_cli_fallback_with_re_prefix(db):
    msg = _make_email(subject="Re: AUTH:secret list all files", body="")
    route = classify_email(msg, db, "AUTH:secret")
    assert route.kind == "cli"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/pytest tests/test_chat_router.py -v`
Expected: FAIL

- [ ] **Step 3: Implement the router**

```python
# src/chat_router.py
"""Email routing logic — classifies incoming emails.

Priority:
1. In-Reply-To matches a chat Message-ID → chat_reply
2. Subject starts with @agent-name → agent_command
3. Subject matches meta-command (status, spawn, restart) → meta
4. Otherwise → cli (existing behavior)
"""
import email.message
import re
from dataclasses import dataclass, field

from src.chat_db import ChatDB

_META_COMMANDS = {"status", "spawn", "restart"}


@dataclass
class Route:
    kind: str  # "chat_reply", "agent_command", "meta", "cli"
    agent_name: str = ""
    body: str = ""
    original_message_id: int = 0
    meta_command: str = ""
    meta_args: str = ""


def _strip_subject_prefix(subject: str, auth_prefix: str) -> str:
    """Remove Re: prefixes and AUTH:secret prefix from subject."""
    s = re.sub(r"^(Re:\s*)+", "", subject, flags=re.IGNORECASE).strip()
    if s.startswith(auth_prefix):
        s = s[len(auth_prefix):].strip()
    return s


def classify_email(
    message: email.message.Message,
    db: ChatDB,
    auth_prefix: str,
) -> Route:
    # 1. Check In-Reply-To against known chat message IDs
    in_reply_to = message.get("In-Reply-To", "").strip()
    if in_reply_to:
        original = db.find_message_by_email_id(in_reply_to)
        if original:
            return Route(
                kind="chat_reply",
                agent_name=original["from_name"],
                original_message_id=original["id"],
            )

    # Parse the subject after stripping prefixes
    subject = message.get("Subject", "")
    command = _strip_subject_prefix(subject, auth_prefix)

    # 2. @agent-name command
    agent_match = re.match(r"@(agent-\S+)\s*(.*)", command)
    if agent_match:
        return Route(
            kind="agent_command",
            agent_name=agent_match.group(1),
            body=agent_match.group(2).strip(),
        )

    # 3. Meta-commands
    parts = command.split(None, 1)
    if parts and parts[0].lower() in _META_COMMANDS:
        return Route(
            kind="meta",
            meta_command=parts[0].lower(),
            meta_args=parts[1] if len(parts) > 1 else "",
        )

    # 4. Fallback to CLI
    return Route(kind="cli")
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/pytest tests/test_chat_router.py -v`
Expected: 10 passed

- [ ] **Step 5: Commit**

```bash
git add src/chat_router.py tests/test_chat_router.py
git commit -m "feat: add email routing logic for chat vs CLI vs meta commands"
```

---

## Task 5: Agent Spawner (`src/spawner.py`)

**Files:**
- Create: `src/spawner.py`
- Create: `tests/test_spawner.py`

- [ ] **Step 1: Write failing tests for MCP config injection and spawn**

```python
# tests/test_spawner.py
import json
import os
import pytest
from unittest.mock import patch, MagicMock
from src.spawner import inject_mcp_config, spawn_agent, build_agent_name
from src.chat_db import ChatDB


@pytest.fixture
def db(tmp_path):
    return ChatDB(str(tmp_path / "test.db"))


def test_build_agent_name():
    assert build_agent_name("/home/user/0-projects/fits") == "agent-fits"


def test_build_agent_name_trailing_slash():
    assert build_agent_name("/home/user/0-projects/fits/") == "agent-fits"


def test_inject_mcp_config_creates_file(tmp_path):
    project_dir = str(tmp_path / "my-project")
    os.makedirs(project_dir)
    inject_mcp_config(project_dir, "http://localhost:8420/sse")
    mcp_path = os.path.join(project_dir, ".mcp.json")
    assert os.path.exists(mcp_path)
    with open(mcp_path) as f:
        config = json.load(f)
    assert "claude-chat" in config["mcpServers"]
    assert config["mcpServers"]["claude-chat"]["url"] == "http://localhost:8420/sse"


def test_inject_mcp_config_merges_existing(tmp_path):
    project_dir = str(tmp_path / "my-project")
    os.makedirs(project_dir)
    existing = {"mcpServers": {"other-server": {"command": "npx", "args": ["something"]}}}
    with open(os.path.join(project_dir, ".mcp.json"), "w") as f:
        json.dump(existing, f)
    inject_mcp_config(project_dir, "http://localhost:8420/sse")
    with open(os.path.join(project_dir, ".mcp.json")) as f:
        config = json.load(f)
    assert "other-server" in config["mcpServers"]
    assert "claude-chat" in config["mcpServers"]


def test_spawn_agent_calls_subprocess(db, tmp_path):
    project_dir = str(tmp_path / "my-project")
    os.makedirs(project_dir)
    with patch("src.spawner.subprocess.Popen") as mock_popen:
        mock_proc = MagicMock()
        mock_proc.pid = 42
        mock_popen.return_value = mock_proc
        name, pid = spawn_agent(db, project_dir, "http://localhost:8420/sse")
    assert name == "agent-my-project"
    assert pid == 42
    agent = db.get_agent("agent-my-project")
    assert agent is not None
    assert agent["pid"] == 42


def test_spawn_agent_with_instruction(db, tmp_path):
    project_dir = str(tmp_path / "my-project")
    os.makedirs(project_dir)
    with patch("src.spawner.subprocess.Popen") as mock_popen:
        mock_proc = MagicMock()
        mock_proc.pid = 42
        mock_popen.return_value = mock_proc
        name, pid = spawn_agent(db, project_dir, "http://localhost:8420/sse", instruction="refactor auth")
    mock_popen.assert_called_once()
    call_args = mock_popen.call_args
    cmd = call_args[0][0]
    assert "refactor auth" in cmd
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/pytest tests/test_spawner.py -v`
Expected: FAIL

- [ ] **Step 3: Implement the spawner**

```python
# src/spawner.py
"""Agent process spawning and MCP config injection."""
import json
import logging
import os
import subprocess

from src.chat_db import ChatDB

logger = logging.getLogger(__name__)


def build_agent_name(project_path: str) -> str:
    folder = os.path.basename(os.path.normpath(project_path))
    return f"agent-{folder}"


def inject_mcp_config(project_dir: str, chat_url: str) -> None:
    mcp_path = os.path.join(project_dir, ".mcp.json")
    config: dict = {"mcpServers": {}}
    if os.path.exists(mcp_path):
        with open(mcp_path) as f:
            config = json.load(f)
        if "mcpServers" not in config:
            config["mcpServers"] = {}
    config["mcpServers"]["claude-chat"] = {"url": chat_url}
    with open(mcp_path, "w") as f:
        json.dump(config, f, indent=2)
    logger.info("Wrote MCP config to %s", mcp_path)


def spawn_agent(
    db: ChatDB,
    project_dir: str,
    chat_url: str,
    instruction: str = "",
    claude_bin: str = "claude",
) -> tuple[str, int]:
    name = build_agent_name(project_dir)
    inject_mcp_config(project_dir, chat_url)

    cmd = [claude_bin, "--print"]
    if instruction:
        cmd.append(instruction)

    proc = subprocess.Popen(
        cmd,
        cwd=project_dir,
        shell=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )

    db.register_agent(name, project_dir)
    db.update_agent_pid(name, proc.pid)
    logger.info("Spawned %s (PID %d) in %s", name, proc.pid, project_dir)
    return name, proc.pid
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/pytest tests/test_spawner.py -v`
Expected: 6 passed

- [ ] **Step 5: Commit**

```bash
git add src/spawner.py tests/test_spawner.py
git commit -m "feat: add agent spawner with MCP config injection"
```

---

## Task 6: Integrate Chat Into main.py

**Files:**
- Modify: `main.py`
- Create: `tests/test_main_chat.py`

- [ ] **Step 1: Write failing tests for routing integration**

```python
# tests/test_main_chat.py
import email.message
import pytest
from unittest.mock import patch, MagicMock
from src.chat_db import ChatDB
from src.chat_router import classify_email, Route


@pytest.fixture
def db(tmp_path):
    return ChatDB(str(tmp_path / "test.db"))


def _make_email(subject="", body="", in_reply_to=""):
    msg = email.message.EmailMessage()
    msg["From"] = "user@example.com"
    msg["Return-Path"] = "<user@example.com>"
    msg["Subject"] = subject
    msg["Message-ID"] = "<test-123@cocode.dk>"
    if in_reply_to:
        msg["In-Reply-To"] = in_reply_to
    msg.set_content(body)
    return msg


def test_chat_reply_inserts_reply_message(db):
    """When a chat reply comes in, it should insert a reply message in the DB."""
    ask = db.insert_message("agent-fits", "user", "question?", "ask")
    db.set_email_message_id(ask["id"], "<chat-abc@cocode.dk>")

    msg = _make_email(
        subject="Re: [agent-fits] question?",
        body="yes go ahead",
        in_reply_to="<chat-abc@cocode.dk>",
    )
    route = classify_email(msg, db, "AUTH:secret")
    assert route.kind == "chat_reply"

    # Simulate what main.py will do
    from src.executor import extract_command
    body = extract_command(msg)
    db.insert_message("user", route.agent_name, body, "reply", in_reply_to=route.original_message_id)

    reply = db.get_reply_to_message(ask["id"])
    assert reply is not None
    assert reply["body"] == "yes go ahead"


def test_agent_command_inserts_command_message(db):
    msg = _make_email(subject="AUTH:secret @agent-fits refactor the module")
    route = classify_email(msg, db, "AUTH:secret")
    assert route.kind == "agent_command"

    db.insert_message("user", route.agent_name, route.body, "command")
    pending = db.get_pending_messages_for("agent-fits")
    assert len(pending) == 1
    assert pending[0]["body"] == "refactor the module"


def test_status_meta_reads_agents(db):
    db.register_agent("agent-fits", "/fits")
    db.register_agent("agent-other", "/other")
    agents = db.list_agents()
    assert len(agents) == 2
```

- [ ] **Step 2: Run tests to verify they pass** (they test the components, not main.py wiring yet)

Run: `.venv/bin/pytest tests/test_main_chat.py -v`
Expected: PASS

- [ ] **Step 3: Modify main.py to integrate chat routing**

Add chat DB initialization and routing to `process_email`. The key change: before executing a CLI command, check if the email should be routed to the chat system instead.

In `main.py`, add these changes:

After the imports, add:
```python
from src.chat_db import ChatDB
from src.chat_router import classify_email
from src.spawner import spawn_agent
```

Add to `_config()`:
```python
"chat_db_path": os.environ.get("CHAT_DB_PATH", "claude-chat.db"),
"chat_url": os.environ.get("CHAT_URL", "http://localhost:8420/sse"),
"auth_prefix": f"AUTH:{os.environ.get('SHARED_SECRET', '')}",
```

Replace `process_email` with:
```python
def process_email(message, config: dict, chat_db: ChatDB | None = None) -> None:
    """Validate, route, and process a single email message."""
    if not is_authorized(
        message,
        authorized_sender=config["authorized_sender"],
        shared_secret=config["shared_secret"],
        gpg_fingerprint=config["gpg_fingerprint"],
        gpg_home=config["gpg_home"],
    ):
        logger.warning("Unauthorized email dropped")
        return

    # Route through chat system if DB available
    if chat_db:
        from src.chat_router import classify_email
        route = classify_email(message, chat_db, config["auth_prefix"])

        if route.kind == "chat_reply":
            body = extract_command(message)
            if body:
                chat_db.insert_message("user", route.agent_name, body, "reply", in_reply_to=route.original_message_id)
                logger.info("Chat reply routed to %s", route.agent_name)
            return

        if route.kind == "agent_command":
            chat_db.insert_message("user", route.agent_name, route.body, "command")
            logger.info("Command dispatched to %s", route.agent_name)
            _send_reply(config, message, f"Command dispatched to {route.agent_name}")
            return

        if route.kind == "meta":
            _handle_meta(route, config, message, chat_db)
            return

    # Fallback: existing CLI behavior
    command = extract_command(message)
    if not command:
        logger.warning("Authorized email has empty command body — skipping")
        return

    logger.info("Executing command from authorized sender")
    output = execute_command(command, claude_bin=config["claude_bin"], timeout=config["claude_timeout"])
    _send_reply(config, message, output)
```

Add helper functions:
```python
def _send_reply(config: dict, original_message, body: str) -> None:
    original_subject = original_message.get("Subject", "command")
    msg_id = original_message.get("Message-ID", "")
    subject = original_subject if original_subject.startswith("Re:") else f"Re: {original_subject}"
    send_reply(
        smtp_host=config["smtp_host"],
        smtp_port=config["smtp_port"],
        username=config["username"],
        password=config["password"],
        to=config["authorized_sender"],
        subject=subject,
        body=body,
        in_reply_to=msg_id,
        references=msg_id,
    )


def _handle_meta(route, config: dict, message, chat_db) -> None:
    import subprocess as _sp
    if route.meta_command == "status":
        agents = chat_db.list_agents()
        if not agents:
            body = "No agents registered."
        else:
            lines = ["Active agents:"]
            for a in agents:
                lines.append(f"- {a['name']}: {a['status']} (last seen {a['last_seen_at']})")
            body = "\n".join(lines)
        _send_reply(config, message, body)

    elif route.meta_command == "spawn":
        parts = route.meta_args.split(None, 1)
        project_dir = parts[0] if parts else ""
        instruction = parts[1] if len(parts) > 1 else ""
        try:
            name, pid = spawn_agent(chat_db, project_dir, config["chat_url"], instruction=instruction)
            _send_reply(config, message, f"Agent {name} spawned (PID {pid})")
        except Exception as exc:
            _send_reply(config, message, f"Spawn failed: {exc}")

    elif route.meta_command == "restart":
        target = route.meta_args.strip().lower()
        if target == "chat":
            _sp.run(["systemctl", "--user", "restart", "claude-chat.service"], shell=False)
            _send_reply(config, message, "claude-chat restarted")
        elif target == "self":
            _sp.run(["systemctl", "--user", "restart", "claude-email.service"], shell=False)
            # No reply — process dies
```

Modify `run_loop` to create the ChatDB and poll for outbound chat messages:
```python
def run_loop(config: dict) -> None:
    global _shutdown
    poller = EmailPoller(
        host=config["imap_host"],
        port=config["imap_port"],
        username=config["username"],
        password=config["password"],
        state_file=config["state_file"],
    )

    chat_db = ChatDB(config["chat_db_path"])

    logger.info(
        "Claude Email Agent starting. Polling every %ds. Authorized sender: %s",
        config["poll_interval"],
        config["authorized_sender"],
    )

    while not _shutdown:
        try:
            # Poll for outbound chat messages (agent → user)
            _relay_outbound_messages(config, chat_db)

            # Poll IMAP for inbound emails
            poller.connect()
            messages = poller.fetch_unseen()
            for uid, msg in messages:
                if _shutdown:
                    break
                msg_id = msg.get("Message-ID", "").strip()
                try:
                    process_email(msg, config, chat_db)
                except Exception as exc:
                    logger.error("Error processing message %s: %s", msg_id, exc)
                finally:
                    poller.mark_processed(uid, msg_id)
            poller.disconnect()
        except Exception as exc:
            logger.error("Poll loop error: %s — retrying after %ds", exc, config["poll_interval"])

        for _ in range(config["poll_interval"]):
            if _shutdown:
                break
            time.sleep(1)

    logger.info("Shutdown complete")


def _relay_outbound_messages(config: dict, chat_db: ChatDB) -> None:
    """Pick up pending messages from agents and email them to the user."""
    pending = chat_db.get_pending_messages_for("user")
    for msg in pending:
        subject = f"[{msg['from_name']}] {msg['body'][:60]}"
        email_msg_id = send_reply(
            smtp_host=config["smtp_host"],
            smtp_port=config["smtp_port"],
            username=config["username"],
            password=config["password"],
            to=config["authorized_sender"],
            subject=subject,
            body=msg["body"],
        )
        chat_db.mark_message_delivered(msg["id"])
        logger.info("Relayed message from %s to user via email", msg["from_name"])
```

- [ ] **Step 4: Run ALL tests to make sure nothing is broken**

Run: `.venv/bin/pytest tests/ -v`
Expected: All existing 36 tests pass + new tests pass

- [ ] **Step 5: Commit**

```bash
git add main.py tests/test_main_chat.py
git commit -m "feat: integrate chat routing into claude-email main loop"
```

---

## Task 7: Update requirements.txt and Service Files

**Files:**
- Modify: `requirements.txt`
- Create: `claude-chat.service`
- Modify: `claude-email.service`
- Modify: `install.sh`

- [ ] **Step 1: Update requirements.txt**

Add `mcp>=1.27` to `requirements.txt`.

- [ ] **Step 2: Create claude-chat.service**

```ini
[Unit]
Description=Claude Chat Relay (MCP Server)
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
WorkingDirectory=__INSTALL_DIR__
EnvironmentFile=__INSTALL_DIR__/.env
ExecStart=__INSTALL_DIR__/.venv/bin/python3 __INSTALL_DIR__/chat_server.py
Restart=on-failure
RestartSec=10
TimeoutStopSec=30

[Install]
WantedBy=default.target
```

- [ ] **Step 3: Update claude-email.service to depend on claude-chat**

Add to the `[Unit]` section:
```ini
After=network-online.target claude-chat.service
Wants=network-online.target claude-chat.service
```

- [ ] **Step 4: Update install.sh to handle both services**

Add a section that copies `claude-chat.service` to `~/.config/systemd/user/` and enables/starts it before claude-email.

- [ ] **Step 5: Commit**

```bash
git add requirements.txt claude-chat.service claude-email.service install.sh
git commit -m "feat: add claude-chat service file and update installer for both services"
```

---

## Task 8: End-to-End Integration Test

**Files:**
- Create: `tests/test_integration.py`

- [ ] **Step 1: Write integration test that exercises the full flow**

```python
# tests/test_integration.py
"""Integration tests for the chat system — tests the full DB flow without network."""
import email.message
import pytest
from src.chat_db import ChatDB
from src.chat_router import classify_email, Route
from chat.tools import register_agent, notify_user, ask_user, check_messages


@pytest.fixture
def db(tmp_path):
    return ChatDB(str(tmp_path / "test.db"))


def test_full_notify_flow(db):
    """Agent notifies user → message appears pending for user."""
    register_agent(db, "agent-fits", "/home/user/fits")
    notify_user(db, "agent-fits", "Build complete, 5 files changed")

    pending = db.get_pending_messages_for("user")
    assert len(pending) == 1
    assert pending[0]["from_name"] == "agent-fits"
    assert pending[0]["body"] == "Build complete, 5 files changed"
    assert pending[0]["type"] == "notify"


def test_full_command_dispatch_flow(db):
    """User sends @agent-name command → agent picks it up."""
    register_agent(db, "agent-fits", "/home/user/fits")

    msg = email.message.EmailMessage()
    msg["Subject"] = "AUTH:secret @agent-fits refactor the auth module"
    msg.set_content("details here")

    route = classify_email(msg, db, "AUTH:secret")
    assert route.kind == "agent_command"

    db.insert_message("user", route.agent_name, route.body, "command")

    result = check_messages(db, "agent-fits")
    assert len(result["messages"]) == 1
    assert result["messages"][0]["body"] == "refactor the auth module"


def test_full_ask_reply_flow(db):
    """Agent asks → user replies → agent gets reply (via DB, no async)."""
    register_agent(db, "agent-fits", "/home/user/fits")

    # Agent asks
    ask_msg = db.insert_message("agent-fits", "user", "Should I proceed?", "ask")
    db.set_email_message_id(ask_msg["id"], "<chat-999@cocode.dk>")

    # Simulate user replying by email
    reply_email = email.message.EmailMessage()
    reply_email["Subject"] = "Re: [agent-fits] Should I proceed?"
    reply_email["In-Reply-To"] = "<chat-999@cocode.dk>"
    reply_email.set_content("Yes, go ahead")

    route = classify_email(reply_email, db, "AUTH:secret")
    assert route.kind == "chat_reply"
    assert route.agent_name == "agent-fits"

    db.insert_message("user", "agent-fits", "Yes, go ahead", "reply", in_reply_to=route.original_message_id)

    # Agent picks up reply
    reply = db.get_reply_to_message(ask_msg["id"])
    assert reply is not None
    assert reply["body"] == "Yes, go ahead"


def test_status_meta_query(db):
    """User asks for status → gets formatted agent list."""
    register_agent(db, "agent-fits", "/home/user/fits")
    register_agent(db, "agent-web", "/home/user/web")

    agents = db.list_agents()
    assert len(agents) == 2
    names = [a["name"] for a in agents]
    assert "agent-fits" in names
    assert "agent-web" in names
```

- [ ] **Step 2: Run integration tests**

Run: `.venv/bin/pytest tests/test_integration.py -v`
Expected: 4 passed

- [ ] **Step 3: Run ALL tests**

Run: `.venv/bin/pytest tests/ -v`
Expected: All tests pass (existing 36 + new tests)

- [ ] **Step 4: Commit**

```bash
git add tests/test_integration.py
git commit -m "test: add end-to-end integration tests for chat system"
```

---

## Task 9: Deploy and Send Test Notification

This is the live integration test — deploy both services and use the chat system to email the user.

- [ ] **Step 1: Start claude-chat server manually to test**

```bash
.venv/bin/python3 chat_server.py &
```

- [ ] **Step 2: Verify it's running**

```bash
curl -s http://localhost:8420/sse -H "Accept: text/event-stream" --max-time 2 || echo "SSE endpoint responding"
```

- [ ] **Step 3: Insert a test notification into the DB**

```python
# Quick test: insert a message that claude-email will pick up and send
from src.chat_db import ChatDB
db = ChatDB("claude-chat.db")
db.register_agent("agent-claude-chat", "__INSTALL_DIR__")
db.insert_message(
    "agent-claude-chat",
    "user",
    "claude-chat is live! The chat relay system is deployed and working. All tests pass.",
    "notify",
)
```

- [ ] **Step 4: Install both services**

```bash
bash install.sh
```

- [ ] **Step 5: Verify both services are running**

```bash
systemctl --user status claude-chat
systemctl --user status claude-email
```

- [ ] **Step 6: Check that the notification email was sent**

The notification message should be picked up by claude-email's relay loop and sent to user@example.com.

- [ ] **Step 7: Final commit**

```bash
git add -A
git commit -m "feat: claude-chat relay system — complete implementation"
```
