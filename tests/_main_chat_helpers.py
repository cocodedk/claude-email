"""Shared helpers for test_main_chat_* modules.

Underscore prefix → pytest skips collecting this file as a test module.
"""
import email.message
import pytest

from src.chat_db import ChatDB


def _make_config(secret="testsecret"):
    return {
        "authorized_sender": "user@example.com",
        "shared_secret": secret,
        "gpg_fingerprint": "",
        "gpg_home": None,
        "smtp_host": "send.one.com",
        "smtp_port": 465,
        "username": "agent@example.com",
        "password": "pw",
        "claude_timeout": 30,
        "claude_bin": "claude",
        "auth_prefix": f"AUTH:{secret}",
        "chat_url": "http://localhost:8420/sse",
    }


def _make_msg(subject, body, from_addr="user@example.com", msg_id="<test001@mail>",
              in_reply_to=""):
    msg = email.message.EmailMessage()
    msg["From"] = f"Babak <{from_addr}>"
    msg["Return-Path"] = f"<{from_addr}>"
    msg["Subject"] = subject
    msg["Message-ID"] = msg_id
    if in_reply_to:
        msg["In-Reply-To"] = in_reply_to
    msg.set_content(body)
    return msg


@pytest.fixture
def chat_db(tmp_path):
    db_path = str(tmp_path / "test-chat.db")
    return ChatDB(db_path)
