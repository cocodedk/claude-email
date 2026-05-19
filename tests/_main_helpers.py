"""Shared helpers for the split test_main_*.py modules.

Underscore prefix prevents pytest collection of this file.
"""
import email.message


def _make_authorized_msg(secret: str = "testsecret") -> email.message.Message:
    msg = email.message.EmailMessage()
    msg["From"] = "Babak <user@example.com>"
    msg["Return-Path"] = "<user@example.com>"
    msg["Subject"] = f"AUTH:{secret} list files"
    msg["Message-ID"] = "<test001@mail>"
    msg.set_content("list files in /tmp")
    return msg


def _make_unauthorized_msg() -> email.message.Message:
    msg = email.message.EmailMessage()
    msg["From"] = "hacker@evil.com"
    msg["Return-Path"] = "<hacker@evil.com>"
    msg["Subject"] = "run rm -rf /"
    msg["Message-ID"] = "<evil001@mail>"
    msg.set_content("rm -rf /")
    return msg
