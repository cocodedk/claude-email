"""Dashboard stylesheet — shell/chrome half (body, topbar, layout, stage).

Paired with dashboard_css_graph.py; dashboard_css.py joins the two.
Split to keep each file under the 200-line cap.
"""

CSS_SHELL = """
:root {
  --bg: #07090d;
  --panel: #0d1218;
  --panel-hi: #131b24;
  --stroke: #1a2431;
  --stroke-hi: #25344a;
  --phos: #6ff3c5;
  --phos-hot: #b7ffe6;
  --phos-dim: #2b6b54;
  --amber: #f5b041;
  --red: #ff7676;
  --fg: #c3d1dc;
  --fg-dim: #5a6b7d;
  --fg-mute: #3a4756;
  --grid: rgba(111, 243, 197, 0.09);
  color-scheme: dark;
}
* { box-sizing: border-box; }
html, body { margin: 0; height: 100%; }
body {
  background:
    radial-gradient(ellipse at 15% -10%, #14243b 0%, transparent 55%),
    radial-gradient(ellipse at 110% 120%, #1b1d3d 0%, transparent 50%),
    var(--bg);
  background-attachment: fixed;
  color: var(--fg);
  font-family: "IBM Plex Mono", ui-monospace, Menlo, monospace;
  font-size: 13px;
  overflow: hidden;
}
body::before {
  content: ""; position: fixed; inset: 0; pointer-events: none; z-index: 3;
  background: url("data:image/svg+xml;utf8,<svg xmlns='http://www.w3.org/2000/svg' width='180' height='180'><filter id='n'><feTurbulence baseFrequency='0.9' numOctaves='2' stitchTiles='stitch'/><feColorMatrix values='0 0 0 0 1  0 0 0 0 1  0 0 0 0 1  0 0 0 0.05 0'/></filter><rect width='180' height='180' filter='url(%23n)'/></svg>");
  mix-blend-mode: overlay; opacity: 0.65;
}
body::after {
  content: ""; position: fixed; inset: 0; pointer-events: none; z-index: 2;
  background: repeating-linear-gradient(0deg,
    rgba(255,255,255,0.02) 0 1px, transparent 1px 3px);
}
header.topbar {
  display: grid;
  grid-template-columns: 260px 1fr auto;
  gap: 32px; align-items: center;
  padding: 14px 24px;
  border-bottom: 1px solid var(--stroke);
  background: linear-gradient(180deg, rgba(111,243,197,0.04), transparent);
  position: relative; z-index: 10;
}
.brand { display: flex; flex-direction: column; gap: 2px; line-height: 1; }
.brand .mark {
  font-family: "Major Mono Display", monospace;
  letter-spacing: 0.14em; font-size: 20px; color: var(--phos);
  text-shadow: 0 0 14px rgba(111,243,197,0.45);
}
.brand .sub {
  font-size: 9px; letter-spacing: 0.38em; color: var(--fg-dim);
  text-transform: uppercase;
}
.telemetry { display: flex; gap: 34px; justify-content: center; }
.tel { display: flex; flex-direction: column; gap: 4px; }
.tel .k {
  font-size: 9px; letter-spacing: 0.28em; color: var(--fg-dim);
  text-transform: uppercase;
}
.tel .v {
  font-size: 15px; color: var(--fg);
  font-variant-numeric: tabular-nums; letter-spacing: 0.04em;
}
.link-state {
  display: inline-flex; align-items: center; gap: 10px;
  padding: 7px 12px; border: 1px solid var(--stroke);
  border-radius: 2px; font-size: 10px;
  letter-spacing: 0.28em; text-transform: uppercase; color: var(--fg-dim);
}
.link-state .led {
  width: 8px; height: 8px; border-radius: 50%;
  background: var(--red); box-shadow: 0 0 8px currentColor;
  animation: ledPulse 1.4s infinite;
}
.link-state.live { color: var(--phos); border-color: rgba(111,243,197,0.45); }
.link-state.live .led { background: var(--phos); }
@keyframes ledPulse { 50% { opacity: 0.25; } }
.wrap {
  display: grid;
  grid-template-columns: minmax(0, 1fr) 420px;
  height: calc(100vh - 64px);
}
#stage {
  position: relative; overflow: hidden;
  border-right: 1px solid var(--stroke);
  background:
    radial-gradient(circle at 50% 52%, rgba(111,243,197,0.05), transparent 62%);
}
#stage::after {
  content: "LAT 284°17'  LON 0.864  Δt 12s  BAND 8420";
  position: absolute; bottom: 10px; left: 14px;
  font-size: 9px; letter-spacing: 0.3em; color: var(--fg-mute);
  text-transform: uppercase;
}
#graph { position: absolute; inset: 0; width: 100%; height: 100%; }
"""
