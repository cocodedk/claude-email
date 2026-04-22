"""Dashboard stylesheet — technical-flow panel half.

Pairs with dashboard_css_shell.py / dashboard_css_graph.py; joined in
dashboard_css.py. Governs the `/dashboard` toggle-second-face SVG.
"""

CSS_FLOW = """
.topbar-right {
  display: inline-flex; align-items: center; gap: 16px;
}
.mode-toggle {
  display: inline-flex; gap: 0; margin-right: 16px;
  border: 1px solid var(--stroke); border-radius: 2px;
  overflow: hidden; font-size: 10px; letter-spacing: 0.28em;
  text-transform: uppercase;
}
.mode-toggle button {
  all: unset; cursor: pointer; padding: 6px 14px;
  color: var(--fg-dim);
  background: linear-gradient(180deg, rgba(255,255,255,0.01), transparent);
  transition: color 0.25s, background 0.25s;
}
.mode-toggle button + button { border-left: 1px solid var(--stroke); }
.mode-toggle button:hover { color: var(--fg); }
.mode-toggle button[aria-pressed="true"] {
  color: var(--phos);
  background: linear-gradient(180deg, rgba(111,243,197,0.14), transparent);
  text-shadow: 0 0 8px rgba(111,243,197,0.55);
}
body.show-flow #graph { display: none; }
body.show-flow #stage::after { display: none; }
body.show-flow aside.feed { display: none; }
body.show-flow .wrap { grid-template-columns: 1fr; }
body:not(.show-flow) #flowLayer { display: none; }
#flowLayer { position: absolute; inset: 0; width: 100%; height: 100%; }
#flowLayer::after {
  content: "DOC 0x07  LATENCY ~0  RING 8420  MODE FLOW";
  position: absolute; bottom: 10px; right: 14px;
  font-size: 9px; letter-spacing: 0.3em; color: var(--fg-mute);
  text-transform: uppercase;
}
.flow-svg { width: 100%; height: 100%; display: block; }
.flow-svg text {
  fill: var(--fg); font-family: "IBM Plex Mono", monospace;
  dominant-baseline: middle;
}
.flow-title {
  fill: var(--phos); font-family: "Major Mono Display", monospace;
  font-size: 14px; letter-spacing: 0.34em;
  text-anchor: middle; text-transform: lowercase;
  text-shadow: 0 0 10px rgba(111,243,197,0.4);
}
.flow-sub {
  fill: var(--fg-dim); font-size: 10px; letter-spacing: 0.42em;
  text-anchor: middle; text-transform: uppercase;
}
.flow-foot {
  fill: var(--fg-mute); font-size: 9px; letter-spacing: 0.3em;
  text-anchor: middle; text-transform: uppercase;
}
.lane-bg {
  fill: url(#flowLaneGrad);
  stroke: rgba(111,243,197,0.22); stroke-width: 1;
  stroke-dasharray: 2 5;
}
.lane-num {
  fill: var(--phos); font-family: "Major Mono Display", monospace;
  font-size: 18px; letter-spacing: 0.18em;
  text-shadow: 0 0 8px rgba(111,243,197,0.4);
}
.lane-title {
  fill: var(--fg); font-size: 11px; letter-spacing: 0.36em;
  text-transform: uppercase; font-weight: 500;
}
.lane-sub {
  fill: var(--fg-dim); font-size: 10px; letter-spacing: 0.18em;
}
.card-bg {
  fill: var(--panel); stroke: var(--stroke-hi); stroke-width: 1;
}
.card-bar {
  fill: var(--phos-dim);
}
.card:hover .card-bg {
  stroke: rgba(111,243,197,0.55);
  filter: drop-shadow(0 0 8px rgba(111,243,197,0.18));
}
.step-chip rect {
  fill: var(--bg); stroke: var(--phos); stroke-width: 1;
}
.chip-txt {
  fill: var(--phos); font-family: "Major Mono Display", monospace;
  font-size: 9px; letter-spacing: 0.22em; text-anchor: middle;
}
.actor {
  fill: var(--fg); font-size: 11px; letter-spacing: 0.12em;
  font-weight: 600;
}
.action {
  fill: var(--phos); font-size: 10px; letter-spacing: 0.06em;
  font-family: "IBM Plex Mono", monospace;
}
.detail {
  fill: var(--fg-dim); font-size: 9px; letter-spacing: 0.2em;
  text-transform: uppercase;
}
.arrow-line {
  stroke: rgba(111,243,197,0.55); stroke-width: 1.3;
  stroke-dasharray: 4 4;
  animation: flowDash 1.4s linear infinite;
}
.lane-02 .arrow-line { animation-duration: 1.8s; }
@keyframes flowDash { to { stroke-dashoffset: -16; } }
.arrow-head {
  fill: var(--phos);
  filter: drop-shadow(0 0 4px rgba(111,243,197,0.8));
}
.sweep-dot circle {
  fill: var(--phos-hot);
  filter: drop-shadow(0 0 8px var(--phos))
          drop-shadow(0 0 16px var(--phos));
}
.sweep-01 { animation: sweep01 5.2s cubic-bezier(.7,0,.3,1) infinite; }
.sweep-02 { animation: sweep02 6.6s cubic-bezier(.7,0,.3,1) infinite; }
@keyframes sweep01 {
  0%,6%   { transform: translate(115px, 218px); opacity: 1; }
  18%     { transform: translate(315px, 218px); opacity: 1; }
  32%     { transform: translate(515px, 218px); opacity: 1; }
  46%     { transform: translate(715px, 218px); opacity: 1; }
  60%     { transform: translate(915px, 218px); opacity: 1; }
  72%,100%{ transform: translate(915px, 218px); opacity: 0; }
}
@keyframes sweep02 {
  0%,5%   { transform: translate(15px, 492px);  opacity: 1; }
  14%     { transform: translate(215px, 492px); opacity: 1; }
  25%     { transform: translate(415px, 492px); opacity: 1; }
  38%     { transform: translate(615px, 492px); opacity: 1; }
  51%     { transform: translate(815px, 492px); opacity: 1; }
  64%     { transform: translate(1015px, 492px); opacity: 1; }
  76%,100%{ transform: translate(1015px, 492px); opacity: 0; }
}
"""
