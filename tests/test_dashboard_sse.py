"""Tests for chat/dashboard.stream_events — the /events SSE generator.

Driven directly with a synthetic is_disconnected so we don't have to
boot the full Starlette app. Kept in its own file so each test module
stays under the 200-line cap.
"""
import asyncio
import json

import pytest

from chat.dashboard import stream_events
from src.chat_db import ChatDB


@pytest.fixture
def db(tmp_path):
    return ChatDB(str(tmp_path / "test.db"))


class TestStreamEvents:
    """Drive the async generator directly with a synthetic is_disconnected."""

    def test_emits_hello_and_exits_when_disconnected(self, db):
        async def always_disconnected():
            return True

        async def run():
            return [c async for c in stream_events(db, always_disconnected, 0.001)]

        chunks = asyncio.run(run())
        assert len(chunks) == 1
        assert "hello" in chunks[0]
        assert "last_id" in chunks[0]

    def test_emits_new_messages_then_keepalive(self, db):
        calls = {"n": 0}

        async def disconnect_second_call():
            calls["n"] += 1
            return calls["n"] > 1

        async def run():
            gen = stream_events(db, disconnect_second_call, 0.001)
            # Pull hello — the watermark is now captured.
            hello_chunk = await gen.__anext__()
            # Insert a message AFTER the watermark so it appears in the stream.
            m = db.insert_message("alice", "bob", "heya", "notify")
            rest = [c async for c in gen]
            return hello_chunk, m, rest

        hello_chunk, m, rest = asyncio.run(run())
        hello = json.loads(hello_chunk[len("data: "):].strip())
        assert hello["kind"] == "hello"
        msg = json.loads(rest[0][len("data: "):].strip())
        assert msg["kind"] == "message"
        assert msg["from_name"] == "alice"
        assert msg["to_name"] == "bob"
        assert msg["body"] == "heya"
        assert msg["id"] == m["id"]
        assert rest[1].startswith(":")  # keepalive

    def test_hello_carries_current_watermark(self, db):
        db.insert_message("a", "b", "one", "notify")
        last = db.insert_message("a", "b", "two", "notify")

        async def immediately_disconnected():
            return True

        async def run():
            return [c async for c in stream_events(
                db, immediately_disconnected, 0.001,
            )]

        chunks = asyncio.run(run())
        hello = json.loads(chunks[0][len("data: "):].strip())
        assert hello["last_id"] == last["id"]

    def test_no_messages_yields_keepalive_only_per_tick(self, db):
        calls = {"n": 0}

        async def disconnect_after_one_tick():
            calls["n"] += 1
            return calls["n"] > 1

        async def run():
            return [c async for c in stream_events(
                db, disconnect_after_one_tick, 0.001,
            )]

        chunks = asyncio.run(run())
        # hello + keepalive (no messages to stream)
        assert len(chunks) == 2
        assert chunks[1].startswith(":")

    def test_streams_flow_events_as_kind_event(self, db):
        """Flow events land on SSE as kind:"event" so the dashboard flow
        panel can dispatch on them independently of message pulses."""
        calls = {"n": 0}

        async def disconnect_second_call():
            calls["n"] += 1
            return calls["n"] > 1

        async def run():
            gen = stream_events(db, disconnect_second_call, 0.001)
            await gen.__anext__()  # hello
            db._log_event("bot", "wake_spawn_start", "pending=1")
            return [c async for c in gen]

        rest = asyncio.run(run())
        flow_frame = json.loads(rest[0][len("data: "):].strip())
        assert flow_frame["kind"] == "event"
        assert flow_frame["event_type"] == "wake_spawn_start"
        assert flow_frame["participant"] == "bot"

    def test_hello_carries_flow_watermark(self, db):
        db._log_event("bot", "wake_spawn_start", "x")
        db._log_event("bot", "hook_drain_stop", "y")
        expected_flow = db.latest_flow_event_id()

        async def immediately_disconnected():
            return True

        async def run():
            return [c async for c in stream_events(
                db, immediately_disconnected, 0.001,
            )]

        chunks = asyncio.run(run())
        hello = json.loads(chunks[0][len("data: "):].strip())
        assert hello["last_flow_id"] == expected_flow
