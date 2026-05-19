"""Shared helpers for tests/test_chat_handlers_*.py.

Underscore-prefixed → pytest does not collect this as a test module.
"""
import email.message


def _make_message(subject="Re: test", msg_id="<orig@mail>"):
    msg = email.message.EmailMessage()
    msg["Subject"] = subject
    msg["Message-ID"] = msg_id
    msg.set_content("body")
    return msg


def _base_config():
    return {
        "smtp_host": "smtp.example.com",
        "smtp_port": 465,
        "username": "claude@example.com",
        "password": "secret",
        "authorized_sender": "bb@example.com",
        "email_domain": "example.com",
        "chat_url": "http://localhost:8420/sse",
        "claude_bin": "claude",
        "claude_cwd": "/tmp",
        "claude_yolo": False,
        "claude_model": None,
        "claude_effort": None,
        "claude_max_budget_usd": None,
        "claude_extra_env": None,
        "service_name_email": "claude-email.service",
        "service_name_chat": "claude-chat.service",
    }
