"""Tests for scripts/chat-agent-instruction.txt — the SessionStart context
piped into every chat-bus session. Asserts load-bearing content survives
future edits."""
from pathlib import Path


_INSTRUCTION = (
    Path(__file__).resolve().parent.parent / "scripts" / "chat-agent-instruction.txt"
).read_text()


def test_instruction_file_exists_and_nonempty():
    assert _INSTRUCTION.strip(), "instruction file missing or empty"


def test_lists_required_bus_tools():
    for tool in ("chat_ask", "chat_notify", "chat_check_messages", "chat_deregister"):
        assert tool in _INSTRUCTION, f"{tool} not mentioned in instruction"


def test_instructs_cron_install_on_first_turn():
    """The 5-min auto-drain cron is the only fallback for idle live sessions
    whose drain hooks don't fire. SessionStart must tell the model to install
    it — otherwise pid=alive agents accumulate pending DMs indefinitely.
    """
    assert "CronCreate" in _INSTRUCTION, "model not told to call CronCreate"
    assert "*/5 * * * *" in _INSTRUCTION, "cron schedule missing"
    assert "auto-drain tick" in _INSTRUCTION, "auto-drain tick marker missing"


def test_warns_against_main_branch_writes():
    text = _INSTRUCTION.lower()
    assert "do not push" in text or "do not push" in _INSTRUCTION
    assert "main" in text
