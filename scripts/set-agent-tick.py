#!/usr/bin/env python3
"""Set or clear an agent's periodic wake tick.

Usage:
    set-agent-tick.py <agent-name> <seconds|off>

`seconds` enables a watcher-driven turn every N seconds even with an empty
inbox (paid `claude --print` turns — enable per active worker lane only).
`off` restores default behavior (wake on pending messages only).
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

try:
    from dotenv import load_dotenv
    load_dotenv(ROOT / ".env")
except ImportError:
    pass

from src.chat_db import ChatDB  # noqa: E402
from src.hook_utils import resolved_db_path as _resolved_db_path  # noqa: E402


def main() -> int:
    if len(sys.argv) != 3:
        print(__doc__.strip(), file=sys.stderr)
        return 2
    name, raw = sys.argv[1], sys.argv[2].strip().lower()
    if raw == "off":
        tick = None
    else:
        try:
            tick = int(raw)
        except ValueError:
            print(f"invalid seconds value: {raw!r} (use an integer or 'off')",
                  file=sys.stderr)
            return 2
        if tick < 30:
            print("refusing tick < 30s — that hammers paid turns",
                  file=sys.stderr)
            return 2
    db = ChatDB(str(_resolved_db_path(ROOT)))
    if db.get_agent(name) is None:
        print(f"unknown agent: {name}", file=sys.stderr)
        return 1
    db.set_agent_tick(name, tick)
    state = "off" if tick is None else f"{tick}s"
    print(f"tick for {name}: {state}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
