"""Glossary panel — HTML renderer for /dashboard's third face.

Joins the two glossary data halves (dashboard_glossary_a +
dashboard_glossary_b) and emits a section-per-category HTML fragment.
Entries collapse by default and expand on click; a search input at the
top filters in-place via a tiny JS helper in dashboard_js_stream.py.
"""
from html import escape as _esc

from chat.dashboard_glossary_a import GLOSSARY_A
from chat.dashboard_glossary_b import GLOSSARY_B

GLOSSARY = GLOSSARY_A + GLOSSARY_B


def _entry(term: str, body: str) -> str:
    return (
        f'<details class="gloss-entry" data-term="{_esc(term.lower())}">'
        f'<summary><span class="gloss-term">{_esc(term)}</span>'
        f'<span class="gloss-chev">+</span></summary>'
        f'<p class="gloss-body">{_esc(body)}</p>'
        f'</details>'
    )


def _category(title: str, entries: list[tuple[str, str]]) -> str:
    items = "".join(_entry(term, body) for term, body in entries)
    term_count = len(entries)
    return (
        f'<section class="gloss-cat">'
        f'<h3 class="gloss-cat-title">'
        f'<span>{_esc(title)}</span>'
        f'<span class="gloss-count">{term_count:02d}</span>'
        f'</h3>'
        f'<div class="gloss-entries">{items}</div>'
        f'</section>'
    )


def _build() -> str:
    total = sum(len(e) for _, e in GLOSSARY)
    cats = "".join(_category(t, e) for t, e in GLOSSARY)
    return f'''
<div id="glossaryLayer">
  <header class="gloss-head">
    <div class="gloss-title">
      <span class="mark">glossary</span>
      <span class="sub">// {total:02d} terms · click to expand</span>
    </div>
    <input id="glossSearch" class="gloss-search" type="search"
           placeholder="filter — try MCP, WAL, SessionStart…"
           autocomplete="off" spellcheck="false"/>
  </header>
  <div class="gloss-body-wrap">{cats}</div>
  <div id="glossEmpty" class="gloss-empty" hidden>
    no match — the term may live in CLAUDE.md or the code comments
  </div>
</div>'''


DASHBOARD_GLOSSARY_HTML = _build()
