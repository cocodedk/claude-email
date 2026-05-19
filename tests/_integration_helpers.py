"""Shared fixtures and helpers for chat-system integration tests.

Exercises the full flow: DB, routing, tools, and relay — without network calls.
Email sending is mocked; everything else uses real implementations.
"""
import email.message

import pytest

from src.chat_db import ChatDB

AUTH_PREFIX = "AUTH:testsecret"

DUMMY_CONFIG = {
    "smtp_host": "smtp.example.com",
    "smtp_port": 465,
    "username": "bot@example.com",
    "password": "fake-password",
    "authorized_sender": "user@example.com",
    "auth_prefix": AUTH_PREFIX,
}


@pytest.fixture
def db(tmp_path):
    return ChatDB(str(tmp_path / "integration.db"))


def _make_msg(
    subject: str = "",
    body: str = "",
    in_reply_to: str = "",
) -> email.message.EmailMessage:
    msg = email.message.EmailMessage()
    if subject:
        msg["Subject"] = subject
    if in_reply_to:
        msg["In-Reply-To"] = in_reply_to
    if body:
        msg.set_content(body)
    return msg
