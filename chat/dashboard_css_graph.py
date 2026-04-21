"""Dashboard stylesheet — radar/graph + transmission feed half.

Paired with dashboard_css_shell.py; dashboard_css.py joins the two.
"""

CSS_GRAPH = """
.grid-ring { fill: none; stroke: var(--grid); stroke-width: 1; }
.grid-ring.outer {
  stroke: rgba(111,243,197,0.22); stroke-dasharray: 2 6;
}
.grid-spoke { stroke: var(--grid); stroke-width: 1; stroke-dasharray: 1 4; }
.sweep {
  transform-origin: 0 0;
  animation: sweep 12s linear infinite;
  opacity: 0.55; mix-blend-mode: screen;
}
@keyframes sweep { to { transform: rotate(360deg); } }
.edge {
  fill: none; stroke: #4cc3a0; stroke-linecap: round;
  transition: opacity 0.35s, stroke-width 0.35s;
  mix-blend-mode: screen;
}
.node text {
  fill: var(--fg); font-size: 9px; letter-spacing: 0.22em;
  text-anchor: middle; text-transform: uppercase;
  pointer-events: none;
  paint-order: stroke; stroke: var(--bg); stroke-width: 3;
}
.node .core { fill: var(--panel); stroke-width: 1.6; }
.node.user .core {
  fill: var(--phos); stroke: var(--phos-hot);
  filter: drop-shadow(0 0 10px rgba(111,243,197,0.7));
}
.node.user text {
  fill: var(--phos); font-family: "Major Mono Display", monospace;
  font-size: 11px; letter-spacing: 0.28em;
}
.node.agent .core { stroke: var(--agent-color, var(--phos)); }
.node.agent .halo {
  fill: var(--agent-color, var(--phos)); opacity: 0.12;
  transition: opacity 0.45s, r 0.45s;
}
.node.agent.active .halo { opacity: 0.55; r: 22; }
.node.agent.disconnected .core {
  stroke: var(--fg-mute); stroke-dasharray: 2 3;
}
.node.agent { cursor: pointer; }
.node.agent.muted { opacity: 0.25; }
.pulse {
  filter: drop-shadow(0 0 6px currentColor)
          drop-shadow(0 0 14px currentColor);
}
aside.feed { display: flex; flex-direction: column; min-height: 0; }
.feed-head {
  padding: 16px 20px 12px; border-bottom: 1px solid var(--stroke);
  display: flex; justify-content: space-between; align-items: center;
}
.feed-head h2 {
  margin: 0; font-family: "Major Mono Display", monospace;
  font-size: 12px; letter-spacing: 0.34em; color: var(--phos);
}
.filter { font-size: 10px; color: var(--fg-dim); letter-spacing: 0.22em; }
.filter #filterLabel { color: var(--amber); margin-left: 6px; }
.filter button {
  all: unset; cursor: pointer; color: var(--red);
  margin-left: 10px; font-size: 10px; letter-spacing: 0.2em;
  border-bottom: 1px dashed currentColor;
}
.feed-list { overflow-y: auto; padding: 8px; flex: 1; }
.feed-list::-webkit-scrollbar { width: 8px; }
.feed-list::-webkit-scrollbar-thumb {
  background: var(--stroke-hi); border-radius: 4px;
}
.entry {
  padding: 10px 12px; margin: 6px 4px;
  border: 1px solid var(--stroke);
  border-left: 3px solid var(--entry-color, var(--stroke-hi));
  background: linear-gradient(180deg, rgba(255,255,255,0.012), transparent);
  cursor: pointer;
  transition: background 0.2s, transform 0.2s, border-color 0.2s;
}
.entry:hover { background: var(--panel-hi); border-color: var(--stroke-hi); }
.entry .meta {
  display: flex; align-items: baseline; gap: 10px; font-size: 11px;
}
.entry .ts {
  color: var(--fg-dim); font-variant-numeric: tabular-nums;
  letter-spacing: 0.04em;
}
.entry .from { color: var(--from-color, var(--phos)); font-weight: 600; }
.entry .arrow {
  color: var(--fg-mute); letter-spacing: -0.1em; font-weight: 400;
}
.entry .to { color: var(--to-color, var(--phos)); font-weight: 600; }
.entry .kind {
  margin-left: auto; font-size: 9px; letter-spacing: 0.28em;
  padding: 2px 7px; border: 1px solid var(--stroke);
  color: var(--fg-dim); text-transform: uppercase;
}
.entry .body {
  margin-top: 8px; color: var(--fg); font-size: 12px;
  white-space: pre-wrap; word-break: break-word;
  display: -webkit-box; -webkit-line-clamp: 2;
  -webkit-box-orient: vertical; overflow: hidden;
}
.entry.open .body { display: block; -webkit-line-clamp: unset; }
.entry.fresh { animation: land 0.55s cubic-bezier(0.2, 0.9, 0.3, 1); }
@keyframes land {
  from { transform: translateX(14px); opacity: 0; }
  to   { transform: translateX(0); opacity: 1; }
}
.empty {
  text-align: center; padding: 48px 20px;
  color: var(--fg-mute); letter-spacing: 0.3em;
  font-size: 10px; text-transform: uppercase;
}
"""
