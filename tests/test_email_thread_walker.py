"""Walker tests for build_email_thread_transcript().

Walks backwards from a parent email Message-ID across both
``messages.in_reply_to_eid`` and ``outbound_emails.in_reply_to_eid``,
filters noisy ACK kinds, applies char + turn caps oldest-first, and
falls back to References right-to-left when In-Reply-To is missing.
"""
import pytest

from src.chat_db import ChatDB
from src.email_thread import build_email_thread_transcript


@pytest.fixture
def cdb(tmp_path):
    return ChatDB(str(tmp_path / "w.db"))


def _insert_inbound(cdb, eid, parent_eid, body):
    cdb.insert_message(
        "user", "router", body, "email_inbound",
        in_reply_to_eid=parent_eid, email_message_id=eid,
    )


def _insert_outbound(cdb, eid, parent_eid, body, kind="result"):
    cdb.record_outbound_email(
        eid, kind=kind, body=body, in_reply_to_eid=parent_eid,
    )


class TestEmpty:
    def test_no_parent_returns_empty(self, cdb):
        assert build_email_thread_transcript(cdb, parent_eid="") == []

    def test_unknown_parent_returns_empty(self, cdb):
        out = build_email_thread_transcript(cdb, parent_eid="<missing@x>")
        assert out == []


class TestOrderingAndFilter:
    def test_multi_turn_chain_oldest_first(self, cdb):
        # T0 user -> T1 router result -> T2 user follow-up (current parent).
        _insert_inbound(cdb, "<u0@x>", "", "first user msg")
        _insert_outbound(cdb, "<r1@x>", "<u0@x>", "router replied", kind="result")
        _insert_inbound(cdb, "<u2@x>", "<r1@x>", "follow up")

        out = build_email_thread_transcript(cdb, parent_eid="<u2@x>")

        assert [t["role"] for t in out] == ["user", "router", "user"]
        assert [t["body"] for t in out] == [
            "first user msg", "router replied", "follow up",
        ]

    def test_excludes_running_ack_kinds(self, cdb):
        _insert_inbound(cdb, "<u0@x>", "", "first")
        _insert_outbound(cdb, "<run@x>", "<u0@x>", "running...", kind="running_ack")
        _insert_outbound(cdb, "<res@x>", "<run@x>", "answer", kind="result")
        _insert_inbound(cdb, "<u2@x>", "<res@x>", "now")

        out = build_email_thread_transcript(cdb, parent_eid="<u2@x>")

        kinds = [t["kind"] for t in out]
        assert "running_ack" not in kinds
        assert "result" in kinds
        # User inbound and final result both kept; ack chain skipped.
        bodies = [t["body"] for t in out]
        assert "running..." not in bodies
        assert bodies == ["first", "answer", "now"]

    def test_excludes_dispatch_acks(self, cdb):
        _insert_inbound(cdb, "<u0@x>", "", "ping")
        _insert_outbound(cdb, "<a1@x>", "<u0@x>", "ok", kind="ack")
        _insert_outbound(cdb, "<a2@x>", "<a1@x>", "ok2", kind="reply_ack")
        _insert_outbound(cdb, "<a3@x>", "<a2@x>", "ok3", kind="status")
        _insert_inbound(cdb, "<u4@x>", "<a3@x>", "follow")

        out = build_email_thread_transcript(cdb, parent_eid="<u4@x>")
        bodies = [t["body"] for t in out]
        assert bodies == ["ping", "follow"]


class TestCaps:
    def test_turn_cap_truncates_oldest(self, cdb):
        # Build a 5-turn user-only chain to keep the setup simple.
        prev = ""
        for i in range(5):
            eid = f"<u{i}@x>"
            _insert_inbound(cdb, eid, prev, f"msg-{i}")
            prev = eid

        out = build_email_thread_transcript(cdb, parent_eid="<u4@x>", turn_cap=3)
        # Keep newest 3: msg-2, msg-3, msg-4.
        assert [t["body"] for t in out] == ["msg-2", "msg-3", "msg-4"]

    def test_char_cap_truncates_oldest(self, cdb):
        long_body = "x" * 500
        prev = ""
        for i in range(5):
            eid = f"<u{i}@x>"
            _insert_inbound(cdb, eid, prev, f"{long_body}-{i}")
            prev = eid

        out = build_email_thread_transcript(cdb, parent_eid="<u4@x>", char_cap=1200)
        # Each entry ~504 chars. 1200 chars budgets ~2 entries.
        assert len(out) <= 3
        # Newest always kept.
        assert out[-1]["body"].endswith("-4")


class TestPartialChain:
    def test_walk_stops_on_missing_mid_chain_parent(self, cdb):
        """Anchor exists but its parent_eid points at a row we don't have.
        Walker must stop cleanly with whatever it already collected."""
        # Anchor exists with a parent that was never inserted.
        _insert_inbound(cdb, "<u0@x>", "<gone@x>", "root with dangling parent")

        out = build_email_thread_transcript(cdb, parent_eid="<u0@x>")

        assert [t["body"] for t in out] == ["root with dangling parent"]


class TestReferencesFallback:
    def test_uses_references_when_in_reply_to_missing(self, cdb):
        _insert_inbound(cdb, "<u0@x>", "", "root")
        _insert_outbound(cdb, "<r1@x>", "<u0@x>", "router answer", kind="result")

        # Caller passes parent_eid="" but References (oldest-first per
        # RFC 5322) lets the walker pick the newest known token from
        # the right end.
        out = build_email_thread_transcript(
            cdb, parent_eid="",
            references="<u0@x> <r1@x>",
        )
        assert [t["body"] for t in out] == ["root", "router answer"]

    def test_references_right_to_left_picks_newest_known(self, cdb):
        _insert_inbound(cdb, "<u0@x>", "", "root")
        _insert_outbound(cdb, "<r1@x>", "<u0@x>", "router1", kind="result")
        _insert_inbound(cdb, "<u2@x>", "<r1@x>", "user2")
        _insert_outbound(cdb, "<r3@x>", "<u2@x>", "router3", kind="result")

        # References lists oldest -> newest. Right-to-left scan picks <r3@x>.
        out = build_email_thread_transcript(
            cdb, parent_eid="<missing@x>",
            references="<u0@x> <r1@x> <u2@x> <r3@x>",
        )
        # All four turns recoverable from <r3@x>.
        assert [t["body"] for t in out] == ["root", "router1", "user2", "router3"]
