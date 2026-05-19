"""Shared helpers for executor test modules.

Underscore-prefixed module name keeps pytest from collecting it as a test
file. Imported by `tests/test_executor_*.py`.
"""
import email.message


def _text_msg(body: str, subject: str = "AUTH:secret cmd") -> email.message.Message:
    msg = email.message.EmailMessage()
    msg["Subject"] = subject
    msg.set_content(body)
    return msg


def _multipart_msg(text: str, html: str) -> email.message.Message:
    msg = email.message.EmailMessage()
    msg["Subject"] = "test"
    msg.set_content(text)
    msg.add_alternative(f"<html><body>{html}</body></html>", subtype="html")
    return msg
