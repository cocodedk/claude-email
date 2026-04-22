"""Glossary entries — first half (email, storage, process model, CLI).

Paired with dashboard_glossary_b.py; joined in dashboard_glossary.py.
"""

GLOSSARY_A: list[tuple[str, list[tuple[str, str]]]] = [
    ("protocols · email", [
        ("IMAP",
         "Internet Message Access Protocol. Lets the poller read messages "
         "from the claude@ mailbox without deleting them, so idempotency "
         "logic on the client can decide what's new."),
        ("SMTP",
         "Simple Mail Transfer Protocol. Used to send replies. This project "
         "uses SMTP over SSL with a verified TLS context — no submission "
         "over plaintext."),
        ("SSL / TLS",
         "Secure Sockets Layer / Transport Layer Security. Every IMAP and "
         "SMTP connection uses ssl.create_default_context() so certificates "
         "are actually validated; Python's default socket contexts don't."),
        ("GPG",
         "GNU Privacy Guard. An incoming command email is accepted if (a) "
         "the From/Return-Path match the authorised sender AND (b) the body "
         "carries a valid GPG signature — or a shared-secret marker."),
        ("Message-ID",
         "An RFC 5322 header that uniquely identifies an email. The poller "
         "stores seen IDs in processed_ids.json so a retried delivery is "
         "never executed twice."),
        ("In-Reply-To / References",
         "Threading headers. When an agent emails the user, In-Reply-To is "
         "set to the command message's ID so the reply lands in the same "
         "thread, and the router matches inbound replies back to the "
         "original chat_ask via the stored email_message_id."),
        ("DKIM / SPF / DMARC",
         "Email-auth standards. The sender check doesn't parse DKIM itself; "
         "we rely on the inbox server to drop forgeries and cross-check with "
         "GPG / shared-secret at the application layer."),
    ]),
    ("protocols · chat bus", [
        ("MCP",
         "Model Context Protocol. The interface Claude Code uses to talk to "
         "external tool servers. This project's bus exposes chat_register, "
         "chat_ask, chat_notify, chat_check_messages, chat_list_agents, "
         "chat_message_agent, and chat_deregister over MCP."),
        ("SSE",
         "Server-Sent Events. A one-way streaming HTTP channel. The MCP bus "
         "uses SSE transport; the dashboard also uses SSE on /events to push "
         "new messages and flow events to the browser."),
        ("EventSource",
         "The browser API that consumes an SSE stream. The dashboard's "
         "connectStream() opens `new EventSource('events')` and auto-reconnects."),
        ("Starlette",
         "The async Python web framework hosting the MCP SSE server plus "
         "the dashboard routes."),
        ("envelope",
         "The outer JSON structure of an agent-to-agent message. Carries "
         "meta (ask_id, task_id, content_type) alongside the body so replies "
         "can be correlated."),
    ]),
    ("storage", [
        ("SQLite",
         "The single-file embedded database that holds every agent, "
         "message, event, and wake-session. Lives at claude-chat.db."),
        ("WAL",
         "Write-Ahead Logging. A SQLite journal mode that lets writers and "
         "readers coexist without blocking. Enabled via PRAGMA journal_mode=WAL "
         "in ChatDB.__init__."),
        ("BEGIN IMMEDIATE",
         "A SQLite transaction mode that acquires a reserved lock up-front, "
         "so concurrent register_agent calls can't race between SELECT and "
         "INSERT. Falls back to the ON CONFLICT clause when a transaction is "
         "already open."),
        ("RETURNING",
         "A SQLite 3.35+ clause that returns the rows a DML statement "
         "affected. Used by claim_pending_messages_for to mark-and-fetch "
         "in one step. Rows are not returned in any guaranteed order, so "
         "the caller sorts by id."),
        ("idempotency store",
         "processed_ids.json — a newline-delimited JSON file of handled "
         "Message-IDs. Never deleted in production; this is what prevents "
         "duplicate command execution if the mail server re-delivers."),
    ]),
    ("process model", [
        ("systemd (user-level)",
         "Per-user service manager. claude-email and claude-chat run as "
         "two user units under ~/.config/systemd/user/, with lingering "
         "enabled so they survive logout. No sudo required."),
        ("Dependency ordering",
         "claude-chat starts first; claude-email declares After= on it so "
         "the bus is up before the poller tries to route commands."),
        ("Lingering",
         "A systemd feature that keeps a user's session manager alive after "
         "logout. Without it, user-level services would stop at logout."),
        ("shell=False",
         "subprocess.Popen argument — tells Python to exec the argv list "
         "directly rather than handing it to /bin/sh. Every subprocess in "
         "this repo is shell=False to close command-injection vectors."),
        ("PID",
         "Process ID. Stored in the agents table so ownership and liveness "
         "checks can survive hook invocations (hook scripts are short-lived "
         "helpers; the stored PID is the long-running Claude session)."),
        ("PPID",
         "Parent Process ID. A hook script walks up the PPID chain via "
         "is_ancestor_or_self to decide whether the registered PID is in "
         "its own lineage — if yes, the hook is allowed to drain the inbox."),
        ("zombie process",
         "A child that has exited but not been wait()'d yet. `kill(pid, 0)` "
         "returns success on zombies, so reap_dead_agents uses "
         "waitpid(WNOHANG) first and only falls back to kill(0) for non-"
         "child PIDs."),
    ]),
    ("claude code internals", [
        ("claude CLI",
         "The `claude` command that drives a single-turn or resumable "
         "conversation. Always invoked with --print so the process exits "
         "cleanly after one reply."),
        ("--resume <session>",
         "Resumes an earlier conversation by session UUID. wake_watcher "
         "uses this to boot an agent with its previous context intact."),
        ("--print",
         "Claude Code flag that prints the reply to stdout and exits, "
         "instead of entering interactive mode."),
        ("SessionStart hook",
         "Fires once when a Claude session boots. This project's "
         "SessionStart hook runs chat-register-self.py (register on the "
         "bus) and chat-drain-inbox.py (deliver any waiting messages as "
         "additionalContext)."),
        ("UserPromptSubmit hook",
         "Fires each time the user sends a prompt. Drains the inbox the "
         "same way SessionStart does so peer messages arrive alongside the "
         "user's next turn."),
        ("Stop hook",
         "Fires when Claude is about to stop responding. We use it to "
         "drain the inbox and emit decision:\"block\" so peer messages "
         "that arrived mid-response become the next turn instead of going "
         "unread until the next user prompt."),
        ("decision: \"block\"",
         "The Stop-hook response shape that cancels the stop. Claude keeps "
         "going, with the reason field surfaced as the next turn's content."),
        ("hookSpecificOutput · additionalContext",
         "SessionStart / UserPromptSubmit hook response shape. The string "
         "under additionalContext is appended verbatim to the next turn."),
        ("stop_hook_active",
         "A Claude Code flag indicating the Stop hook is already in a "
         "block-loop. The drain script deliberately ignores it — "
         "mark_message_delivered is the real loop guard because a message "
         "can only be redelivered once."),
        ("subagent (agent_id)",
         "When Claude spawns a subagent, its hook invocations carry "
         "agent_id. chat-drain-inbox.py bails on these so subagents don't "
         "steal the master session's inbox."),
    ]),
]
