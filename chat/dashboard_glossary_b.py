"""Glossary entries — second half (chat system, dashboard, quality).

Paired with dashboard_glossary_a.py; joined in dashboard_glossary.py.
"""

GLOSSARY_B: list[tuple[str, list[tuple[str, str]]]] = [
    ("chat system — actors", [
        ("agent-<project>",
         "The conventional name used by any Claude session registered on "
         "the bus: 'agent-' + basename of the cwd. Makes names stable "
         "across reboots."),
        ("user avatar",
         "claude-email itself registered on the bus as the user's proxy. "
         "Inbound emails become outbound bus messages from the user avatar; "
         "bus replies destined for the user get relayed back out as emails."),
        ("peer message",
         "Any message between two agents (neither end being the user). "
         "These are what the wake_watcher and Stop-hook paths deliver."),
        ("ghost agent",
         "An agent whose last_seen_at hasn't moved in a long time — either "
         "a crashed pid=NULL registration or a session that died between "
         "heartbeats. Filtered out by get_agents_summary so the radar "
         "reflects live reality."),
    ]),
    ("chat system — wake + deliver", [
        ("wake_watcher",
         "An asyncio task inside the claude-chat process. Polls "
         "get_distinct_pending_recipients and drives a wake turn for each "
         "agent that has pending messages but isn't currently responding."),
        ("wake turn",
         "One subprocess invocation: `claude --print --resume <session> "
         "\"<prompt>\"`. The SessionStart hook that fires inside the boot "
         "drains the inbox, so the messages end up in that turn's context."),
        ("nudge Event",
         "An asyncio.Event that ChatDB.insert_message sets whenever a new "
         "row lands. wake_watcher awaits it so a new message kicks the "
         "loop immediately, instead of waiting for the next poll tick."),
        ("cold wake",
         "Shorthand for the wake_watcher path: no live Claude process for "
         "the target agent → spawn a fresh one so its SessionStart hook "
         "picks up the inbox."),
        ("stall",
         "A wake turn that exited cleanly but didn't deliver any pending "
         "message. Counted against max_failures so a broken drain hook "
         "escalates to the user instead of looping forever."),
        ("session cache",
         "An in-memory map of agent → last session UUID. Avoids a DB hit "
         "on every poll. Entries expire after idle_expiry_secs so a reboot "
         "doesn't reuse a dead session."),
    ]),
    ("chat system — mcp tools", [
        ("chat_register",
         "Registers the calling agent under a name + project_path. "
         "Enforces at-most-one-live-owner per name and per project_path."),
        ("chat_ask",
         "Blocking. Sends a message and waits (up to ~1h) for a reply "
         "addressed to the same ask_id. Intended for agent→user or "
         "agent→agent Q&A."),
        ("chat_notify",
         "One-way. Send a message and return immediately. Used for "
         "progress updates and status beacons."),
        ("chat_check_messages",
         "Consume-with-ack drain of pending messages. Returned rows are "
         "marked delivered, so a second call returns an empty list."),
        ("chat_message_agent",
         "Peer-to-peer send from one agent to another. Does not route "
         "through the user."),
        ("chat_list_agents",
         "Returns the visible agent roster (via get_agents_summary — "
         "already filters ghosts whose last_seen_at is stale)."),
        ("chat_deregister",
         "Voluntary check-out on clean exit. The server also reaps dead "
         "agents automatically, so this is a courtesy, not a guarantee."),
        ("chat_spawn_agent",
         "Spawn a new Claude session against a project path. Injects the "
         "MCP config, approves the server in the project's trust list, "
         "and fires the SessionStart hook so the new agent registers."),
        ("chat_enqueue_task / chat_queue_status",
         "Task queue API. An agent can accept a long-running task, the "
         "queue tracks it with a PID, and the ghost reaper marks it "
         "failed if the worker dies mid-task."),
    ]),
    ("chat system — lifecycle", [
        ("touch_agent",
         "Updates last_seen_at for an agent. Runs up-front on every MCP "
         "tool invocation via dispatch._heartbeat, so active agents stay "
         "visible even when they're only sending rather than draining."),
        ("reap_dead_agents",
         "Mark agents whose PID is no longer alive as disconnected. Runs "
         "every claude-email tick. Zombie children are reaped via waitpid "
         "before the liveness probe."),
        ("ghost reaper",
         "A sibling concept for the task queue: any running task whose PID "
         "is gone gets marked failed. Prevents permanent 'running' state "
         "when a worker dies before set_pid or mid-task."),
        ("idle_expiry_secs",
         "Wake-watcher config. If a persisted wake session is older than "
         "this, it's dropped — --resume would otherwise boot a stale "
         "conversation."),
        ("rate_limit_secs",
         "Minimum interval between user-facing failure notifications per "
         "agent. The bus still cleans up stuck messages every cycle; only "
         "the email is gated."),
    ]),
    ("dashboard internals", [
        ("observatory view",
         "The live CRT radar at /dashboard — user at centre, agents on a "
         "ring, each message a phosphor pulse along the chord."),
        ("flow view",
         "The second face at /dashboard. Static diagram of the two "
         "code paths (Stop-hook self-poll, wake_watcher spawn) that light "
         "up when the bus actually fires those events."),
        ("glossary view",
         "The panel you're reading. Indexes every acronym and term the "
         "project uses so a newcomer doesn't need to grep the code."),
        ("FLOW_EVENT_TYPES",
         "The event_type values (hook_drain_stop, hook_drain_session, "
         "wake_spawn_start, wake_spawn_end) the dashboard cares about. "
         "All other events table rows (message, register, disconnect) are "
         "ignored."),
        ("heat edge",
         "A persistent arc between two nodes on the radar. Stroke width "
         "and opacity scale logarithmically with traffic volume."),
        ("pulse",
         "The short-lived dot that travels along a chord on the radar "
         "whenever a message is inserted. Coloured by the sender's "
         "hash-derived hue."),
        ("hash-derived hue",
         "A stable per-name colour computed from the agent's name string. "
         "No palette config needed; adding agents just gives them distinct "
         "colours automatically."),
    ]),
    ("quality gates", [
        ("coverage (100%)",
         "Every line of production code under src/ and chat/ must be "
         "exercised by a test. .coveragerc omits tests/, the entry-shim, "
         "and standard pragma patterns."),
        ("200-line cap",
         "No production or test file exceeds 200 lines. Enforced by "
         "scripts/check-line-limit.sh. Keeps every file small enough to "
         "read in one screenful."),
        ("TDD",
         "Test-Driven Development — write the failing test first, then "
         "the minimal implementation. Enforced by the 'test-driven-"
         "development' superpowers skill referenced in CLAUDE.md."),
    ]),
]
