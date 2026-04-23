"""Technical-flow diagram — the second face of the dashboard.

Renders a static SVG that traces how a peer message reaches an idle
agent through two distinct code paths: the Stop-hook self-poll and the
wake_watcher cold-spawn. Pure markup + CSS animation; no backend feed.

The layout is generated from LANE_1_STEPS / LANE_2_STEPS so tweaks stay
readable. Coordinates live in one place and the rest is composition.
"""
from html import escape as _esc

VIEW_W = 1200
VIEW_H = 620
CARD_W = 170
CARD_H = 124

LANE_1 = {
    "num": "01",
    "title": "PUSH — STOP-HOOK SELF-POLL",
    "sub": "the agent is awake; its own Stop hook drains the inbox before idling",
    "y": 86,
    "steps": [
        ("01", "peer agent", "chat_message_agent", "→ SQLite row"),
        ("02", "claude-chat.db", "messages table", "delivered=0"),
        ("03", "agent · replying", "Stop hook fires", "end-of-turn"),
        ("04", "chat-drain-inbox.py", 'decision: "block"', "cancels stop"),
        ("05", "agent · next turn", "msgs in context", "conversation flows"),
    ],
}
LANE_2 = {
    "num": "02",
    "title": "COLD WAKE — SPAWN DORMANT AGENT",
    "sub": "the agent process is gone; wake_watcher boots a fresh CLI so the inbox gets read",
    "y": 360,
    "steps": [
        ("01", "peer agent", "chat_message_agent", "B is offline"),
        ("02", "claude-chat.db", "pending recipient", "nudge.set()"),
        ("03", "wake_watcher", "loop inside server", "finds dead agent"),
        ("04", "spawn_fn", "claude --print", "--resume <session>"),
        ("05", "SessionStart hook", "drains inbox", "additionalContext"),
        ("06", "agent · booted", "first turn", "sees messages"),
    ],
}


def _cells(n: int) -> list[int]:
    """Centred x positions for n cells of width CARD_W."""
    gap = 30
    total = n * CARD_W + (n - 1) * gap
    x0 = (VIEW_W - total) // 2
    return [x0 + i * (CARD_W + gap) for i in range(n)]


def _card(
    x: int, y: int, num: str, actor: str, act: str, detail: str,
    lane: str,
) -> str:
    return f'''
<g class="card" data-lane="{lane}" data-step="{num}" transform="translate({x},{y})">
  <rect class="card-bg" width="{CARD_W}" height="{CARD_H}" rx="3"/>
  <rect class="card-bar" x="0" y="0" width="{CARD_W}" height="3"/>
  <g class="step-chip" transform="translate(18,22)">
    <rect x="-14" y="-12" width="28" height="24" rx="2"/>
    <text class="chip-txt" x="0" y="5">{_esc(num)}</text>
  </g>
  <text class="actor"  x="18" y="62">{_esc(actor)}</text>
  <text class="action" x="18" y="82">{_esc(act)}</text>
  <text class="detail" x="18" y="102">{_esc(detail)}</text>
</g>'''


def _arrow(x1: int, y: int, x2: int, idx: int, lane: str) -> str:
    return f'''
<g class="arrow a-{lane}-{idx}">
  <line x1="{x1}" y1="{y}" x2="{x2 - 8}" y2="{y}"
        class="arrow-line"/>
  <polygon class="arrow-head"
           points="{x2 - 8},{y - 4} {x2},{y} {x2 - 8},{y + 4}"/>
</g>'''


def _lane(lane: dict) -> str:
    y = lane["y"]
    xs = _cells(len(lane["steps"]))
    body_y = y + 56
    arrow_y = body_y + CARD_H // 2
    parts = [
        f'<g class="lane lane-{lane["num"]}">',
        f'  <rect class="lane-bg" x="20" y="{y}" width="{VIEW_W - 40}" '
        f'height="230" rx="4"/>',
        f'  <text class="lane-num" x="42" y="{y + 28}">{lane["num"]}</text>',
        f'  <text class="lane-title" x="78" y="{y + 28}">{lane["title"]}</text>',
        f'  <text class="lane-sub" x="42" y="{y + 46}">{lane["sub"]}</text>',
    ]
    for x, step in zip(xs, lane["steps"]):
        parts.append(_card(x, body_y, *step, lane=lane["num"]))
    for i in range(len(xs) - 1):
        x1 = xs[i] + CARD_W
        x2 = xs[i + 1]
        parts.append(_arrow(x1 + 4, arrow_y, x2 - 4, i, lane["num"]))
    parts.append(
        f'<g class="sweep-dot sweep-{lane["num"]}" '
        f'transform="translate({xs[0]},{arrow_y})">'
        f'<circle r="5"/></g>'
    )
    parts.append('</g>')
    return "\n".join(parts)


DASHBOARD_FLOW_SVG = f'''
<svg id="flow" class="flow-svg" viewBox="0 0 {VIEW_W} {VIEW_H}"
     preserveAspectRatio="xMidYMid meet">
  <defs>
    <linearGradient id="flowLaneGrad" x1="0" x2="1" y1="0" y2="0">
      <stop offset="0"   stop-color="rgba(111,243,197,0.10)"/>
      <stop offset="0.5" stop-color="rgba(111,243,197,0.03)"/>
      <stop offset="1"   stop-color="rgba(111,243,197,0.10)"/>
    </linearGradient>
  </defs>
  <text class="flow-title" x="{VIEW_W // 2}" y="36">
    how a peer message reaches an idle agent
  </text>
  <text class="flow-sub" x="{VIEW_W // 2}" y="58">
    two paths · one shared SQLite bus
  </text>
  {_lane(LANE_1)}
  {_lane(LANE_2)}
  <text class="flow-foot" x="{VIEW_W // 2}" y="{VIEW_H - 16}">
    both paths share: claude-chat.db (WAL) · chat-drain-inbox.py
    · the ChatDB nudge Event
  </text>
</svg>
'''
