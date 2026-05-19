"""Shared test fixtures for email-thread test files.

Two helpers were duplicating across the four test_email_thread_* files;
this is their single source of truth. Kept underscore-prefixed so
pytest skips them as test modules.
"""
import email.message


def email_config() -> dict:
    """Minimum config dict for process_email / send_threaded_reply tests."""
    return {
        "smtp_host": "smtp.example.com", "smtp_port": 465,
        "username": "agent@example.com", "password": "pw",
        "authorized_sender": "user@example.com",
        "email_domain": "example.com",
        "shared_secret": "s", "auth_prefix": "AUTH:s",
        "gpg_fingerprint": "", "gpg_home": None,
        "authorized_senders": ["user@example.com"],
        "claude_cwd": "/tmp",
        "service_name_chat": "claude-chat.service",
        "service_name_email": "claude-email.service",
        "claude_bin": "claude", "claude_yolo": False,
        "chat_url": "http://127.0.0.1:8420/sse",
        "claude_timeout": 30, "claude_extra_env": None,
        "claude_model": None, "claude_effort": None,
        "claude_max_budget_usd": None,
        "llm_router": False, "universes": [],
    }


def inbound_email(
    msg_id: str = "<inbound-1@example.com>",
    *, in_reply_to: str = "", references: str = "",
    subject: str = "cmd", body: str = "body",
) -> email.message.EmailMessage:
    """Construct an authorized inbound email with optional thread headers."""
    m = email.message.EmailMessage()
    m["From"] = "user@example.com"
    m["Return-Path"] = "<user@example.com>"
    m["Message-ID"] = msg_id
    m["Subject"] = subject
    if in_reply_to:
        m["In-Reply-To"] = in_reply_to
    if references:
        m["References"] = references
    m.set_content(body)
    return m
