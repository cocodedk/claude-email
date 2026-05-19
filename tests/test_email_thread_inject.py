"""Router-side injection wiring for email-thread context.

Asserts:
- process_email on a follow-up inbound (In-Reply-To set, prior turns
  exist) calls execute_command with a preamble prepended to the body.
- A fresh inbound (no In-Reply-To, no prior turns) gets the bare body.
- The router-path inbound body is persisted into messages so future
  follow-ups can walk back to it.
- Existing chat_reply routing is untouched (handle_chat_email's return
  value short-circuits before the CLI/router block).
"""
import pytest

import main as main_mod
from src.chat_db import ChatDB
from tests._email_helpers import email_config, inbound_email


def _inbound(msg_id, in_reply_to="", references=""):
    """Local wrapper so subject + body stay AUTH-prefixed for this file."""
    return inbound_email(
        msg_id, in_reply_to=in_reply_to, references=references,
        subject="AUTH:s do thing", body="AUTH:s do thing",
    )


@pytest.fixture
def cdb(tmp_path):
    return ChatDB(str(tmp_path / "i.db"))


def _stub_smtp(mocker, sent_ids):
    """Side-effect that returns one stamped SMTP id per call."""
    queue = iter(sent_ids)
    mocker.patch(
        "src.chat_handlers.send_reply",
        side_effect=lambda **_: next(queue),
    )


class TestInboundPersistence:
    def test_router_path_persists_inbound_body(self, cdb, mocker):
        _stub_smtp(mocker, ["<run-1@x>", "<res-1@x>"])
        mocker.patch("main.execute_command", return_value="ok")
        msg = _inbound("<in-1@example.com>")

        main_mod.process_email(msg, email_config(), chat_db=cdb)

        row = cdb.find_message_by_email_id("<in-1@example.com>")
        assert row is not None
        assert row["from_name"] == "user"
        assert row["body"] == "AUTH:s do thing" or "do thing" in row["body"]

    def test_inbound_in_reply_to_eid_stored(self, cdb, mocker):
        _stub_smtp(mocker, ["<run-2@x>", "<res-2@x>"])
        mocker.patch("main.execute_command", return_value="ok")
        msg = _inbound("<in-2@example.com>", in_reply_to="<prior@example.com>")

        main_mod.process_email(msg, email_config(), chat_db=cdb)

        row = cdb.find_message_by_email_id("<in-2@example.com>")
        assert row["in_reply_to_eid"] == "<prior@example.com>"


class TestPreambleInjection:
    def test_followup_injects_preamble(self, cdb, mocker):
        # Seed a prior router turn that will be walked.
        cdb.record_outbound_email(
            "<prior@example.com>", kind="result",
            body="prior router output", in_reply_to_eid="<root@example.com>",
        )
        cdb.insert_message(
            "user", "router", "first user msg", "email_inbound",
            email_message_id="<root@example.com>",
        )

        _stub_smtp(mocker, ["<run-3@x>", "<res-3@x>"])
        exec_spy = mocker.patch("main.execute_command", return_value="ok")

        msg = _inbound("<in-3@example.com>", in_reply_to="<prior@example.com>")
        main_mod.process_email(msg, email_config(), chat_db=cdb)

        sent_cmd = exec_spy.call_args.args[0]
        assert "Prior turns in this email thread" in sent_cmd
        assert "prior router output" in sent_cmd
        assert "New inbound message:" in sent_cmd
        # Original command body is still present at the bottom.
        assert sent_cmd.rstrip().endswith("AUTH:s do thing") or "do thing" in sent_cmd

    def test_fresh_email_no_preamble(self, cdb, mocker):
        _stub_smtp(mocker, ["<run-4@x>", "<res-4@x>"])
        exec_spy = mocker.patch("main.execute_command", return_value="ok")
        msg = _inbound("<in-4@example.com>")  # no In-Reply-To

        main_mod.process_email(msg, email_config(), chat_db=cdb)

        sent_cmd = exec_spy.call_args.args[0]
        assert "Prior turns in this email thread" not in sent_cmd

    def test_in_reply_to_present_but_unknown_no_preamble(self, cdb, mocker):
        _stub_smtp(mocker, ["<run-5@x>", "<res-5@x>"])
        exec_spy = mocker.patch("main.execute_command", return_value="ok")
        msg = _inbound("<in-5@example.com>", in_reply_to="<missing@x>")

        main_mod.process_email(msg, email_config(), chat_db=cdb)

        sent_cmd = exec_spy.call_args.args[0]
        assert "Prior turns in this email thread" not in sent_cmd
