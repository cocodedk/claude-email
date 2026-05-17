# Phase F — Reply routing with branch reuse and honest ACK

One task. Folds three reviewer corrections into `apply_reply`:

1. **Project/agent mismatch guard** in `_prior_branch` — verify the prior task lives in the same project and the outbound row's sender_agent matches the reply's agent_name, so a misrouted reply can't inherit a branch from another project.
2. **Outcome-accurate ACK text** — three sentences, picked based on whether we reused a prior branch / queued read-only / planned a new branch.
3. **Pass `In-Reply-To` from `chat_handlers._handle_reply`** so `apply_reply` has the header to walk back.

The classifier (Phase B.4) and the queue extension (Phase C.6) are the prerequisites.

---

## Task F.10: `apply_reply` walks outbound → prior task → branch (with guards)

**Files:**
- Modify: `src/reply_router.py` (extend imports + `apply_reply` body)
- Modify: `src/chat_handlers.py:115-130` (thread `In-Reply-To` through)
- Modify: `tests/test_reply_router.py` (adapt `_FakeTaskQueue`)
- Create: `tests/test_apply_reply_branch_reuse.py`

### Lookup chain

```
inbound message
  → In-Reply-To header
  → chat_db.find_outbound_email(header)
  → outbound.task_id present?
  → outbound.sender_agent == agent_name? (strict eq, NULL fails)  [guard 1]
  → task_queue.get(outbound.task_id)
  → prior.project_path == decision.project_path?  [guard 2]
  → prior.branch_name
```

Any failed link → no prior branch → caller falls back to today's "fresh branch" behavior. No exceptions, no warnings — this is a best-effort enrichment.

**Round-3 reviewer note on guard 1:** strict equality (not "set-and-different"). A row with `task_id` set but `sender_agent=NULL` must NOT inherit the prior branch — fail closed. `task_id` is a new column so legitimate old rows shouldn't have it, but if one slips through with NULL sender we still refuse to inherit.

### ACK selection

```python
if prior_branch:
    "Queued as task #N for AGENT to continue prior branch `B` (worker pid P)."
elif mutates is False:
    "Queued as task #N for AGENT as a read-only task (no branch will be created; worker pid P)."
else:
    "Queued as task #N for AGENT on planned branch `B` (worker pid P)."
```

The middle sentence is new — v1's ACK lied for this case.

**Round-3 reviewer note on wording:** "continue prior branch" instead of "existing branch". The prior branch can be deleted between enqueue and worker run; the matrix in Phase E.9 falls through to a fresh new branch in that case, so the ACK would lie if it promised "existing". "Continue prior" stays accurate either way without duplicating matrix logic in `apply_reply` (which would also have a TOCTOU window).

### Steps

- [ ] **Step 1: Write the failing tests**

`tests/test_apply_reply_branch_reuse.py`:

```python
"""Tests for the branch-reuse + guards path in src/reply_router.apply_reply.

Lookup chain: In-Reply-To → outbound_emails.task_id → tasks row →
branch_name + mutates_repo. Guards: project_path must match, and
outbound.sender_agent must match agent_name."""
import pytest

from src.chat_db import ChatDB
from src.reply_router import apply_reply
from src.task_queue import TaskQueue


@pytest.fixture
def db_path(tmp_path):
    path = str(tmp_path / "db")
    ChatDB(path)
    return path


@pytest.fixture
def db(db_path):
    return ChatDB(db_path)


@pytest.fixture
def tq(db_path):
    return TaskQueue(db_path)


class _StubWM:
    def __init__(self, pid=111):
        self.pid = pid

    def ensure_worker(self, _path):
        return self.pid


def _project_dir(tmp_path, name="p"):
    p = tmp_path / name
    p.mkdir()
    return str(p.resolve())


def _seed_prior_task(
    db, tq, project_path, branch_name, agent_name="agent-p", mutating=True,
):
    """Insert a completed prior task + a relayed outbound email pointing
    to it. Returns (task_id, outbound Message-ID)."""
    tid = tq.enqueue(
        project_path, "implement X",
        branch_name=branch_name,
        mutates_repo=mutating,
        origin_message_id="<orig@x>",
        origin_from="user@example.org",
    )
    tq.mark_done(tid)
    db.insert_message(agent_name, "user", "done", "notify", task_id=tid)
    out_id = f"<sent-{tid}@x>"
    db.record_outbound_email(
        out_id, kind="result", sender_agent=agent_name, task_id=tid,
    )
    return tid, out_id


def _latest(tq):
    """Return the most recently inserted task across all projects."""
    row = tq._conn.execute(
        "SELECT * FROM tasks ORDER BY id DESC LIMIT 1"
    ).fetchone()
    return dict(row)


class TestBranchReuseFromOutbound:
    def test_reuses_prior_branch_for_mutating_followup(
        self, db, tq, tmp_path,
    ):
        proj = _project_dir(tmp_path)
        db.register_agent("agent-p", proj)
        prior_id, out_id = _seed_prior_task(
            db, tq, proj, "claude/task-17-fix-bus", mutating=True,
        )
        original = db.insert_message(
            "agent-p", "user", "done", "notify", task_id=prior_id,
        )
        ack, _tag = apply_reply(
            db, tq, _StubWM(pid=222),
            agent_name="agent-p", original_message_id=original["id"],
            body="also add docs",
            allowed_base=str(tmp_path),
            original_email_message_id=out_id,
        )
        new = _latest(tq)
        assert new["branch_name"] == "claude/task-17-fix-bus"
        assert new["mutates_repo"] == 1
        # ACK reflects reuse (round-3 wording change)
        assert "continue prior branch" in ack
        assert "claude/task-17-fix-bus" in ack

    def test_read_only_followup_after_mutating_task_reuses_branch(
        self, db, tq, tmp_path,
    ):
        """Reviewer's specific case: 'explain what you changed' must
        reuse the prior branch so the worker runs in the right tree."""
        proj = _project_dir(tmp_path)
        db.register_agent("agent-p", proj)
        prior_id, out_id = _seed_prior_task(
            db, tq, proj, "claude/task-17-fix-bus", mutating=True,
        )
        original = db.insert_message(
            "agent-p", "user", "done", "notify", task_id=prior_id,
        )
        apply_reply(
            db, tq, _StubWM(),
            agent_name="agent-p", original_message_id=original["id"],
            body="explain what you changed",
            allowed_base=str(tmp_path),
            original_email_message_id=out_id,
        )
        new = _latest(tq)
        assert new["branch_name"] == "claude/task-17-fix-bus"  # reused
        assert new["mutates_repo"] == 0  # classified read-only

    def test_no_outbound_match_falls_through(self, db, tq, tmp_path):
        """Pre-deploy outbound rows have no task_id. Reply still queues
        with no branch_name → worker creates fresh."""
        proj = _project_dir(tmp_path)
        db.register_agent("agent-p", proj)
        original = db.insert_message("agent-p", "user", "done", "notify")
        ack, _tag = apply_reply(
            db, tq, _StubWM(),
            agent_name="agent-p", original_message_id=original["id"],
            body="add docs",
            allowed_base=str(tmp_path),
            original_email_message_id="<never-sent@x>",
        )
        new = _latest(tq)
        assert new["branch_name"] is None
        # ACK reflects planned branch
        assert "planned branch" in ack


class TestGuards:
    def test_null_sender_agent_rejects_prior(self, db, tq, tmp_path):
        """Round-3 reviewer fix: strict equality. A row with task_id
        but sender_agent=NULL must NOT inherit the prior branch."""
        proj = _project_dir(tmp_path)
        db.register_agent("agent-p", proj)
        prior_id = tq.enqueue(
            proj, "implement X",
            branch_name="claude/task-9-foo", mutates_repo=True,
            origin_message_id="<orig@x>", origin_from="user@example.org",
        )
        tq.mark_done(prior_id)
        # Insert outbound row directly with NULL sender_agent
        db._conn.execute(
            "INSERT INTO outbound_emails "
            "(email_message_id, sent_at, kind, sender_agent, task_id) "
            "VALUES (?, ?, ?, NULL, ?)",
            ("<no-sender@x>", "2026-05-17T00:00:00+00:00", "result", prior_id),
        )
        db._conn.commit()
        original = db.insert_message("agent-p", "user", "done", "notify")
        apply_reply(
            db, tq, _StubWM(),
            agent_name="agent-p", original_message_id=original["id"],
            body="follow up",
            allowed_base=str(tmp_path),
            original_email_message_id="<no-sender@x>",
        )
        assert _latest(tq)["branch_name"] is None  # guard fired

    def test_project_mismatch_rejects_prior(self, db, tq, tmp_path):
        """If outbound's prior task lives in project A but the reply
        routes to agent B in project B, do NOT inherit A's branch."""
        proj_a = _project_dir(tmp_path, "a")
        proj_b = _project_dir(tmp_path, "b")
        db.register_agent("agent-a", proj_a)
        db.register_agent("agent-b", proj_b)
        # Prior task in project A
        prior_id, out_id = _seed_prior_task(
            db, tq, proj_a, "claude/task-1-thing-in-a",
            agent_name="agent-a", mutating=True,
        )
        # Reply routes to agent-b
        original = db.insert_message("agent-b", "user", "done", "notify")
        apply_reply(
            db, tq, _StubWM(),
            agent_name="agent-b", original_message_id=original["id"],
            body="follow up",
            allowed_base=str(tmp_path),
            original_email_message_id=out_id,
        )
        new = _latest(tq)
        assert new["project_path"] == proj_b
        assert new["branch_name"] is None  # guard fired

    def test_agent_mismatch_rejects_prior(self, db, tq, tmp_path):
        """outbound.sender_agent must equal agent_name."""
        proj = _project_dir(tmp_path)
        db.register_agent("agent-p", proj)
        db.register_agent("agent-other", proj)
        prior_id, out_id = _seed_prior_task(
            db, tq, proj, "claude/task-9-foo",
            agent_name="agent-other", mutating=True,
        )
        original = db.insert_message("agent-p", "user", "done", "notify")
        apply_reply(
            db, tq, _StubWM(),
            agent_name="agent-p", original_message_id=original["id"],
            body="follow up",
            allowed_base=str(tmp_path),
            original_email_message_id=out_id,
        )
        new = _latest(tq)
        assert new["branch_name"] is None  # guard fired


class TestClassifierIntegration:
    def test_mutating_body_stamps_true(self, db, tq, tmp_path):
        proj = _project_dir(tmp_path)
        db.register_agent("agent-p", proj)
        original = db.insert_message("agent-p", "user", "x", "notify")
        apply_reply(
            db, tq, _StubWM(),
            agent_name="agent-p", original_message_id=original["id"],
            body="fix the bus",
            allowed_base=str(tmp_path),
            original_email_message_id="",
        )
        assert _latest(tq)["mutates_repo"] == 1

    def test_read_only_body_stamps_false(self, db, tq, tmp_path):
        proj = _project_dir(tmp_path)
        db.register_agent("agent-p", proj)
        original = db.insert_message("agent-p", "user", "x", "notify")
        apply_reply(
            db, tq, _StubWM(),
            agent_name="agent-p", original_message_id=original["id"],
            body="explain the relay",
            allowed_base=str(tmp_path),
            original_email_message_id="",
        )
        new = _latest(tq)
        assert new["mutates_repo"] == 0
        # Read-only ACK
        # (note: empty body would be None, but "explain..." is non-empty)

    def test_empty_body_leaves_mutates_null(self, db, tq, tmp_path):
        proj = _project_dir(tmp_path)
        db.register_agent("agent-p", proj)
        original = db.insert_message("agent-p", "user", "x", "notify")
        apply_reply(
            db, tq, _StubWM(),
            agent_name="agent-p", original_message_id=original["id"],
            body="",
            allowed_base=str(tmp_path),
            original_email_message_id="",
        )
        assert _latest(tq)["mutates_repo"] is None


class TestAckText:
    def test_read_only_ack_says_no_branch(self, db, tq, tmp_path):
        proj = _project_dir(tmp_path)
        db.register_agent("agent-p", proj)
        original = db.insert_message("agent-p", "user", "x", "notify")
        ack, _tag = apply_reply(
            db, tq, _StubWM(),
            agent_name="agent-p", original_message_id=original["id"],
            body="show me the schema",
            allowed_base=str(tmp_path),
            original_email_message_id="",
        )
        assert "read-only" in ack.lower()
        assert "no branch" in ack.lower()

    def test_planned_branch_ack_for_new_mutating_task(
        self, db, tq, tmp_path,
    ):
        proj = _project_dir(tmp_path)
        db.register_agent("agent-p", proj)
        original = db.insert_message("agent-p", "user", "x", "notify")
        ack, _tag = apply_reply(
            db, tq, _StubWM(),
            agent_name="agent-p", original_message_id=original["id"],
            body="implement the new endpoint",
            allowed_base=str(tmp_path),
            original_email_message_id="",
        )
        assert "planned branch" in ack
        assert "claude/task-" in ack
```

- [ ] **Step 2: Run tests — should fail**

Run: `.venv/bin/pytest tests/test_apply_reply_branch_reuse.py -v`
Expected: FAIL — `apply_reply()` has no `original_email_message_id` kwarg.

- [ ] **Step 3: Replace `src/reply_router.py`**

Full rewrite:

```python
"""Reply sub-classification + branch-reuse for email follow-ups.

Three routes (unchanged):
- reply_to_ask: original was a chat_ask → goes on the bus so the
  blocking chat_ask returns.
- reply_to_project: agent has a valid project_path under CLAUDE_CWD →
  queue the reply body as a task and ensure a worker is running.
- reply_bus_only: neither of the above → fall back to bus-only.

Branch-reuse layer: when the user replies on a thread we sent for a
task, walk In-Reply-To → outbound_emails.task_id → prior task to get
the prior branch_name. Guards: prior task must be in the same project,
and outbound.sender_agent must match agent_name. mutates_repo is
classified from the reply body so read-only follow-ups skip the dirty
check (Phase E's matrix).
"""
import logging
import os
from dataclasses import dataclass
from pathlib import Path

from src.git_ops import task_branch_name
from src.mutation_classifier import classify_mutation

logger = logging.getLogger(__name__)


@dataclass
class ReplyDecision:
    route: str   # "ask" | "project" | "bus"
    project_path: str = ""
    ack_subject_suffix: str = ""


def classify_reply(
    chat_db, agent_name: str, original_message_id: int, allowed_base: str,
) -> ReplyDecision:
    original = chat_db.get_message(original_message_id)
    if original is not None and original.get("type") == "ask":
        return ReplyDecision(route="ask")
    agent = chat_db.get_agent(agent_name)
    project_path = (agent or {}).get("project_path", "")
    if project_path and _project_in_base(project_path, allowed_base):
        return ReplyDecision(
            route="project",
            project_path=str(Path(project_path).resolve()),
        )
    return ReplyDecision(route="bus")


def _project_in_base(project_path: str, allowed_base: str) -> bool:
    if not allowed_base or not project_path:
        return False
    try:
        base = str(Path(allowed_base).resolve())
        resolved = str(Path(project_path).resolve())
    except OSError:
        return False
    if not os.path.isdir(resolved):
        return False
    return resolved == base or resolved.startswith(base + os.sep)


def _prior_branch(
    chat_db, task_queue, in_reply_to_header: str,
    project_path: str, agent_name: str,
) -> str:
    """Walk inbound In-Reply-To → outbound_emails.task_id → tasks.branch_name.

    Returns "" when any link is missing OR when sender_agent doesn't
    strictly equal agent_name (NULL fails too — fail closed) OR when
    the prior task is in a different project. Defense against misrouted
    replies inheriting the wrong branch."""
    if not in_reply_to_header or task_queue is None:
        return ""
    outbound = chat_db.find_outbound_email(in_reply_to_header)
    if not outbound or not outbound.get("task_id"):
        return ""
    if outbound.get("sender_agent") != agent_name:
        logger.info(
            "ignoring prior task: outbound sender_agent=%r != reply agent=%r",
            outbound.get("sender_agent"), agent_name,
        )
        return ""
    prior = task_queue.get(outbound["task_id"])
    if not prior:
        return ""
    if prior.get("project_path") != project_path:
        logger.info(
            "ignoring prior task: project mismatch (prior=%s, reply=%s)",
            prior.get("project_path"), project_path,
        )
        return ""
    return (prior.get("branch_name") or "")


def _format_ack(
    *, task_id: int, agent_name: str, worker_pid: int,
    prior_branch: str, mutates: bool | None, body: str,
) -> tuple[str, str]:
    """Return (ack_body, subject_tag). One of three sentences, chosen
    by actual outcome so the ACK never lies about whether a branch will
    exist.

    'continue prior branch' (not 'existing branch') is the round-3
    wording fix: the matrix in src.branch_prep may fall back to a fresh
    new branch if the prior was deleted between enqueue and worker run,
    and 'continue prior' stays accurate either way."""
    tag = f"Queued #{task_id}"
    if prior_branch:
        body_text = (
            f"Queued as task #{task_id} for {agent_name} to continue prior "
            f"branch `{prior_branch}` (worker pid {worker_pid})."
        )
    elif mutates is False:
        body_text = (
            f"Queued as task #{task_id} for {agent_name} as a read-only task "
            f"(no branch will be created; worker pid {worker_pid})."
        )
    else:
        branch = task_branch_name(task_id, body)
        body_text = (
            f"Queued as task #{task_id} for {agent_name} on planned branch "
            f"`{branch}` (worker pid {worker_pid})."
        )
    return body_text, tag


def apply_reply(
    chat_db, task_queue, worker_manager, *,
    agent_name: str, original_message_id: int,
    body: str, allowed_base: str,
    original_email_message_id: str = "",
) -> tuple[str, str]:
    """Record the reply and act on it. Returns (ack_body, subject_tag)."""
    decision = classify_reply(chat_db, agent_name, original_message_id, allowed_base)
    chat_db.insert_message(
        "user", agent_name, body, "reply", in_reply_to=original_message_id,
    )
    if decision.route == "project" and task_queue and worker_manager:
        prior_branch = _prior_branch(
            chat_db, task_queue, original_email_message_id,
            decision.project_path, agent_name,
        )
        mutates = classify_mutation(body)
        try:
            worker_pid = worker_manager.ensure_worker(decision.project_path)
            task_id = task_queue.enqueue(
                decision.project_path, body,
                branch_name=prior_branch,
                mutates_repo=mutates,
            )
        except ValueError as exc:
            logger.warning("Reply enqueue failed: %s", exc)
            return (
                f"Delivered to {agent_name} on the chat bus (couldn't queue: {exc}).",
                "Delivered",
            )
        return _format_ack(
            task_id=task_id, agent_name=agent_name, worker_pid=worker_pid,
            prior_branch=prior_branch, mutates=mutates, body=body,
        )
    if decision.route == "ask":
        return (
            f"Answer delivered to {agent_name} (was waiting on a question).",
            "Answer",
        )
    return (f"Delivered to {agent_name} on the chat bus.", "Delivered")
```

- [ ] **Step 4: Thread `In-Reply-To` through `chat_handlers._handle_reply`**

Edit `src/chat_handlers.py:115-130`:

```python
def _handle_reply(
    route, message, config: dict, chat_db: ChatDB,
    task_queue: TaskQueue | None, worker_manager: WorkerManager | None,
) -> None:
    body = extract_command(message, strip_secret=config.get("shared_secret", ""))
    ack, tag = apply_reply(
        chat_db, task_queue, worker_manager,
        agent_name=route.agent_name,
        original_message_id=route.original_message_id,
        body=body, allowed_base=config.get("claude_cwd") or "",
        original_email_message_id=message.get("In-Reply-To", "").strip(),
    )
    logger.info("Reply routed: %s", ack)
    send_threaded_reply(
        config, message, ack, tag=tag, chat_db=chat_db, kind="reply_ack",
        sender_agent=route.agent_name,
    )
```

- [ ] **Step 5: Adapt `tests/test_reply_router.py` fakes**

The existing `_FakeTaskQueue` accepts `enqueue(self, path, body, priority=0)` — that breaks when `apply_reply` passes `branch_name=` and `mutates_repo=`. Update:

```python
class _FakeTaskQueue:
    def __init__(self):
        self.enqueued = []

    def enqueue(self, path, body, priority=0, branch_name="", mutates_repo=None,
                **_):
        self.enqueued.append((path, body, priority, branch_name, mutates_repo))
        return 42

    def get(self, _task_id):
        return None  # no prior task in the legacy fixtures
```

Adjust the existing tuple-equality assertion in `test_project_reply_enqueues_and_acks`. The body is `"also add docs"`; `classify_mutation("also add docs")` returns True (because `add` is in `_MUTATING`). The tuple shape grows to 5 fields:

```python
        assert tq.enqueued == [
            (proj, "also add docs", 0, "", True),
        ]
```

The ACK assertion in the same test (`"#42" in ack and "555" in ack` + `"claude/task-42-also-add-docs" in ack`) still holds because the planned-branch ACK includes the planned name. The "planned branch" prefix changes the wording but `claude/task-42-also-add-docs` is still in the string.

If `test_project_reply_enqueues_and_acks` also asserts a specific phrasing of "Queued as task #42 for agent-p on branch ...", relax it to check the substring `claude/task-42-also-add-docs` only — the new ACK uses "planned branch" instead of "branch".

- [ ] **Step 6: Run tests**

```
.venv/bin/pytest tests/test_reply_router.py tests/test_apply_reply_branch_reuse.py -v
.venv/bin/pytest tests/ -q
scripts/check-line-limit.sh
```

Expected: all PASS; `src/reply_router.py` under 200 lines. (Exact count varies; capture in Phase H.14.)

- [ ] **Step 7: Commit**

```bash
git add src/reply_router.py src/chat_handlers.py tests/test_reply_router.py tests/test_apply_reply_branch_reuse.py
git commit -m "feat(reply): walk outbound→prior task; project/agent guard; honest ACK"
```
