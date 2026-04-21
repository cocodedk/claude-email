# Dashboard — Event Sourcing + SSE

## Goal

A live UI showing what is happening inside claude-email and the agents it spawns:
agent status, task queue, worker lifecycle, message bus activity.

---

## The data is already there

`claude-chat.db` already has agents, tasks, messages, and wake sessions.
`journalctl` has service logs. The gap: worker lifecycle events (spawned,
idle-exited, crashed) and service restarts live only in the journal, not the DB.

---

## Event Sourcing

Instead of storing only current state (mutable rows), store every event that
led to it. State becomes a derived view computed by replaying the log.

**Current approach (mutable):**
```sql
UPDATE tasks SET status='running', pid=98059 WHERE id=14
UPDATE tasks SET status='completed', output='...' WHERE id=14
```

**Event-sourced (append-only):**
```sql
INSERT INTO events (task_id, kind, data) VALUES (14, 'task_claimed',    '{"pid":98059}')
INSERT INTO events (task_id, kind, data) VALUES (14, 'task_completed',  '{"output":"..."}')
```

Current state = replay `SELECT * FROM events WHERE task_id=14 ORDER BY ts`.

### Natural event types for claude-email

| Category | Events |
|----------|--------|
| Email | `email_received`, `auth_passed`, `auth_failed` |
| Tasks | `task_enqueued`, `task_claimed`, `task_completed`, `task_failed` |
| Workers | `worker_spawned`, `worker_idle_exited`, `worker_crashed` |
| Agents | `agent_registered`, `agent_message_sent`, `agent_deregistered` |
| Wake | `wake_nudge_fired` |

### Cost

Need a read-model layer to answer "current status of task 14?" without replaying
10k events every time — a simple in-memory projection or a materialized view.
Aligns well with SQLite WAL mode (append-only writes).

---

## SSE (Server-Sent Events)

A simple HTTP protocol where the server pushes a stream of text events to the
browser over a single long-lived connection.

**Browser side:**
```javascript
const es = new EventSource('/events')
es.onmessage = e => console.log(JSON.parse(e.data))
```

**Server side** — keeps connection open, sends chunks on each event:
```
data: {"kind":"task_completed","task_id":14}

data: {"kind":"agent_registered","name":"agent-test-01"}

```

Why simpler than WebSockets:
- Plain HTTP — works through proxies, no upgrade handshake
- One direction only (server → client), which is all a dashboard needs
- Browser auto-reconnects on disconnect
- Already used in this project — MCP chat server uses SSE as its transport

---

## Proposed Implementation

### New file: `chat/dashboard.py`

Mounted as a Starlette sub-app on `chat/server.py`. Keeps concerns separate
and avoids pushing `server.py` past the 200-line limit.

**Routes:**
- `GET /dashboard` — serves the single-page HTML/JS UI (inline, no build step)
- `GET /api/agents` — agent list + status from DB
- `GET /api/tasks` — recent tasks per project (status, output, errors)
- `GET /events` — SSE stream fed by the existing `ChatDB` nudge event

### DB changes

Two read-only query methods on `ChatDB`:
- `get_agents_summary()` — name, status, last_seen
- `get_tasks_summary(limit=50)` — id, project, status, created_at, output, error

Optionally: add an `events` table (append-only) as the event-sourcing foundation.

### Dashboard then becomes

SSE stream of the events table → browser projects current state locally.
Trivial to build, trivial to replay history, trivial to debug
("show me everything that happened to task 14").

---

## Estimated scope

~200 lines total across `chat/dashboard.py` + DB query methods + inline HTML.
No new systemd service needed — piggybacks on `claude-chat.service`.
