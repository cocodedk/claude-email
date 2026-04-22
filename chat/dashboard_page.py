"""Inline single-page dashboard — CRT observatory / node-graph view.

Kept lean; the heavy lifting lives in dashboard_css.py (stylesheet)
and dashboard_js.py (client logic) so every file stays under the
200-line cap.
"""
from chat.dashboard_css import DASHBOARD_CSS
from chat.dashboard_flow_svg import DASHBOARD_FLOW_SVG
from chat.dashboard_glossary import DASHBOARD_GLOSSARY_HTML
from chat.dashboard_js import DASHBOARD_JS

_FONTS_HREF = (
    "https://fonts.googleapis.com/css2"
    "?family=Major+Mono+Display"
    "&family=IBM+Plex+Mono:wght@300;400;500;600"
    "&display=swap"
)

_BODY = f"""
<header class="topbar">
  <div class="brand">
    <span class="mark">CLAUDE.CHAT</span>
    <span class="sub">// node graph · observatory</span>
  </div>
  <div class="telemetry">
    <div class="tel"><span class="k">UTC</span>
      <span class="v" id="clock">00:00:00Z</span></div>
    <div class="tel"><span class="k">OPS</span>
      <span class="v" id="ops">00</span></div>
    <div class="tel"><span class="k">EVENTS</span>
      <span class="v" id="count">0000</span></div>
  </div>
  <div class="topbar-right">
    <div class="mode-toggle" role="group" aria-label="view mode">
      <button id="modeObs" type="button" aria-pressed="true">observatory</button>
      <button id="modeFlow" type="button" aria-pressed="false">flow</button>
      <button id="modeGlossary" type="button" aria-pressed="false">glossary</button>
    </div>
    <div id="status" class="link-state">
      <span class="led"></span><span id="statusText">connecting</span>
    </div>
  </div>
</header>
<div class="wrap">
  <section id="stage">
    <svg id="graph" viewBox="-230 -215 460 430"
         preserveAspectRatio="xMidYMid meet">
      <defs>
        <radialGradient id="sweepGrad" cx="0" cy="0" r="1"
                        gradientUnits="userSpaceOnUse">
          <stop offset="0"   stop-color="rgba(111,243,197,0.55)"/>
          <stop offset="0.4" stop-color="rgba(111,243,197,0.15)"/>
          <stop offset="1"   stop-color="rgba(111,243,197,0)"/>
        </radialGradient>
      </defs>
      <circle class="grid-ring"       r="60"/>
      <circle class="grid-ring"       r="110"/>
      <circle class="grid-ring"       r="165"/>
      <circle class="grid-ring outer" r="200"/>
      <line class="grid-spoke" x1="-205" y1="0"    x2="205" y2="0"/>
      <line class="grid-spoke" x1="0"    y1="-205" x2="0"   y2="205"/>
      <line class="grid-spoke" x1="-145" y1="-145" x2="145" y2="145"/>
      <line class="grid-spoke" x1="145"  y1="-145" x2="-145" y2="145"/>
      <path class="sweep"
            d="M0,0 L205,0 A205,205 0 0,0 145,-145 Z"
            fill="url(#sweepGrad)"/>
      <g id="edges"></g>
      <g id="nodes">
        <g class="node user">
          <circle class="halo" r="18"
                  fill="rgba(111,243,197,0.25)"/>
          <circle class="core" r="12"/>
          <text y="28">user</text>
        </g>
      </g>
      <g id="pulses"></g>
    </svg>
    <div id="flowLayer">
      <div class="flow-live-indicator">
        <span class="dot"></span><span id="flowLiveText">awaiting events</span>
      </div>
      {DASHBOARD_FLOW_SVG}
    </div>
    {DASHBOARD_GLOSSARY_HTML}
  </section>
  <aside class="feed">
    <div class="feed-head">
      <h2>// transmissions</h2>
      <div class="filter">channel:
        <span id="filterLabel">ALL CHANNELS</span>
        <button id="filterClear" style="display:none">× clear</button>
      </div>
    </div>
    <div id="feed" class="feed-list">
      <div class="empty">initialising…</div>
    </div>
  </aside>
</div>
"""

DASHBOARD_HTML = f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>CLAUDE.CHAT // node graph</title>
<meta name="viewport" content="width=device-width, initial-scale=1">
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link rel="stylesheet" href="{_FONTS_HREF}">
<style>{DASHBOARD_CSS}</style>
</head>
<body>{_BODY}<script>{DASHBOARD_JS}</script>
</body>
</html>
"""
