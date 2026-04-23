"""Dashboard stylesheet — glossary panel half.

Paired with dashboard_css_shell.py / dashboard_css_graph.py /
dashboard_flow_css.py; joined in dashboard_css.py.
"""

CSS_GLOSSARY = """
body.show-glossary #graph { display: none; }
body.show-glossary #flowLayer { display: none; }
body.show-glossary #stage::after { display: none; }
body.show-glossary aside.feed { display: none; }
body.show-glossary .wrap { grid-template-columns: 1fr; }
body:not(.show-glossary) #glossaryLayer { display: none; }
#glossaryLayer {
  position: absolute; inset: 0;
  display: flex; flex-direction: column;
  overflow: hidden;
  padding: 28px 28px 16px;
  background:
    radial-gradient(ellipse at 50% -20%, rgba(111,243,197,0.06), transparent 60%);
}
.gloss-head {
  display: flex; justify-content: space-between; align-items: baseline;
  gap: 24px; padding-bottom: 14px;
  border-bottom: 1px dashed rgba(111,243,197,0.22);
  margin-bottom: 8px;
}
.gloss-title { display: flex; flex-direction: column; gap: 4px; }
.gloss-title .mark {
  font-family: "Major Mono Display", monospace;
  font-size: 22px; letter-spacing: 0.22em;
  color: var(--phos);
  text-shadow: 0 0 14px rgba(111,243,197,0.35);
}
.gloss-title .sub {
  font-size: 10px; letter-spacing: 0.34em;
  color: var(--fg-dim); text-transform: uppercase;
}
.gloss-search {
  flex: 0 1 360px; align-self: center;
  background: var(--panel); color: var(--fg);
  border: 1px solid var(--stroke-hi);
  padding: 10px 14px;
  font-family: "IBM Plex Mono", monospace; font-size: 13px;
  letter-spacing: 0.05em;
  outline: none;
  border-radius: 2px;
  transition: border-color 0.2s, box-shadow 0.2s;
}
.gloss-search:focus {
  border-color: var(--phos);
  box-shadow: 0 0 0 1px rgba(111,243,197,0.35),
              0 0 14px rgba(111,243,197,0.18);
}
.gloss-search::placeholder { color: var(--fg-mute); letter-spacing: 0.1em; }
.gloss-body-wrap {
  overflow-y: auto; padding-top: 12px; padding-right: 6px;
  flex: 1;
  column-width: 360px; column-gap: 24px;
  column-rule: 1px dashed rgba(111,243,197,0.12);
}
.gloss-body-wrap::-webkit-scrollbar { width: 8px; }
.gloss-body-wrap::-webkit-scrollbar-thumb {
  background: var(--stroke-hi); border-radius: 4px;
}
.gloss-cat {
  break-inside: avoid;
  margin-bottom: 22px;
}
.gloss-cat-title {
  display: flex; justify-content: space-between; align-items: baseline;
  margin: 0 0 8px; padding: 0 2px 6px;
  border-bottom: 1px solid var(--stroke);
  color: var(--phos);
  font-family: "Major Mono Display", monospace;
  font-size: 11px; letter-spacing: 0.38em;
  text-transform: uppercase;
}
.gloss-count {
  color: var(--fg-mute); font-size: 10px; letter-spacing: 0.3em;
  font-weight: 400;
}
.gloss-entries {
  display: flex; flex-direction: column; gap: 2px;
}
.gloss-entry {
  padding: 0;
  border-left: 2px solid transparent;
  transition: border-color 0.2s, background 0.2s;
}
.gloss-entry[open] {
  border-left-color: var(--phos);
  background: linear-gradient(180deg, rgba(111,243,197,0.04), transparent 85%);
}
.gloss-entry > summary {
  list-style: none;
  cursor: pointer;
  display: flex; align-items: baseline; justify-content: space-between;
  padding: 8px 12px; gap: 12px;
  color: var(--fg);
  transition: color 0.2s, background 0.2s;
  user-select: none;
}
.gloss-entry > summary::-webkit-details-marker { display: none; }
.gloss-entry > summary:hover { background: var(--panel-hi); color: var(--phos-hot); }
.gloss-term {
  font-family: "IBM Plex Mono", monospace; font-size: 13px;
  letter-spacing: 0.04em; font-weight: 500;
}
.gloss-chev {
  font-family: "Major Mono Display", monospace;
  color: var(--phos); font-size: 14px; line-height: 1;
  transition: transform 0.25s;
}
.gloss-entry[open] > summary .gloss-chev {
  transform: rotate(45deg);
}
.gloss-body {
  margin: 0; padding: 0 14px 12px 14px;
  color: var(--fg); font-size: 12px; line-height: 1.6;
  letter-spacing: 0.01em;
}
.gloss-entry[hidden] { display: none; }
.gloss-cat[hidden] { display: none; }
.gloss-empty {
  text-align: center; padding: 32px;
  color: var(--fg-mute); letter-spacing: 0.3em; font-size: 11px;
  text-transform: uppercase;
}
"""
