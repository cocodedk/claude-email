"""Tests for /dashboard's non-observatory faces — flow panel + glossary.

Split from tests/test_dashboard.py (which covers routes, agents, messages,
SSE stream, events handler, server wiring) so each file stays under the
200-line cap.
"""
from chat.dashboard_page import DASHBOARD_HTML


class TestFlowPanel:
    """The /dashboard has a second face: a technical-flow illustration
    toggled from the topbar (observatory ↔ flow). It's pure SVG/CSS with
    no backend data — these checks confirm the markup is present and the
    two code paths (Stop-hook self-poll, wake_watcher spawn) are labelled."""

    def test_mode_toggle_buttons_present(self):
        assert 'id="modeObs"' in DASHBOARD_HTML
        assert 'id="modeFlow"' in DASHBOARD_HTML
        assert "observatory" in DASHBOARD_HTML
        # Default mode is observatory; flow button starts unpressed.
        assert 'id="modeObs" type="button" aria-pressed="true"' in DASHBOARD_HTML
        assert 'id="modeFlow" type="button" aria-pressed="false"' in DASHBOARD_HTML

    def test_flow_layer_embedded(self):
        assert 'id="flowLayer"' in DASHBOARD_HTML
        assert 'id="flow"' in DASHBOARD_HTML  # the inner flow <svg>
        assert "flow-title" in DASHBOARD_HTML

    def test_both_paths_labelled(self):
        assert "STOP-HOOK SELF-POLL" in DASHBOARD_HTML
        assert "SPAWN DORMANT AGENT" in DASHBOARD_HTML

    def test_stop_hook_lane_names_key_actors(self):
        # Every actor in the prose explainer must appear somewhere.
        for needle in (
            "chat_message_agent",
            "claude-chat.db",
            "Stop hook fires",
            "chat-drain-inbox.py",
            'decision: &quot;block&quot;',  # rendered by the f-string escape
        ):
            assert needle in DASHBOARD_HTML, f"missing: {needle}"

    def test_wake_lane_names_key_actors(self):
        for needle in (
            "wake_watcher",
            "SessionStart hook",
            "claude --print",
            "--resume &lt;session&gt;",
        ):
            assert needle in DASHBOARD_HTML, f"missing: {needle}"

    def test_mode_toggle_js_binds_localstorage(self):
        assert "bindModeToggle" in DASHBOARD_HTML
        assert "dashboard.mode" in DASHBOARD_HTML
        assert "show-flow" in DASHBOARD_HTML

    def test_step_cards_carry_data_attrs_for_live_firing(self):
        """JS targets step cards via [data-lane][data-step] to fire
        animations when flow events arrive over SSE."""
        assert 'data-lane="01"' in DASHBOARD_HTML
        assert 'data-lane="02"' in DASHBOARD_HTML
        # Both lanes have at least steps 01..05; lane 02 goes to 06.
        for step in ("01", "02", "03", "04", "05"):
            assert f'data-step="{step}"' in DASHBOARD_HTML
        # Lane 02 has one more (the booted-agent terminal card).
        assert 'data-step="06"' in DASHBOARD_HTML

    def test_flow_event_map_wired(self):
        """The JS knows how to route each emitted flow event_type to
        a lane + step sequence. Keep the keys in lock-step with
        FLOW_EVENT_TYPES from src/dashboard_queries.py."""
        for event_type in (
            "wake_spawn_start", "wake_spawn_end",
            "hook_drain_stop", "hook_drain_session",
        ):
            assert event_type in DASHBOARD_HTML, f"missing: {event_type}"
        assert "FLOW_EVENT_MAP" in DASHBOARD_HTML
        assert "onFlowEvent" in DASHBOARD_HTML
        assert "flow-live-indicator" in DASHBOARD_HTML


class TestGlossaryPanel:
    """Third face of /dashboard: a click-to-expand glossary indexing every
    acronym and term used in the project. The renderer flattens two data
    halves (GLOSSARY_A + GLOSSARY_B) into collapsible <details> entries
    with a live search input at the top."""

    def test_mode_toggle_has_glossary_button(self):
        assert 'id="modeGlossary"' in DASHBOARD_HTML
        assert ">glossary<" in DASHBOARD_HTML

    def test_glossary_layer_embedded(self):
        assert 'id="glossaryLayer"' in DASHBOARD_HTML
        assert 'id="glossSearch"' in DASHBOARD_HTML
        assert 'id="glossEmpty"' in DASHBOARD_HTML

    def test_core_acronyms_all_present(self):
        """Don't spare anything — every acronym the user might click
        for should be in the panel. A failure here means a term got
        removed from dashboard_glossary_[ab].py without a replacement."""
        for term in (
            "MCP", "SSE", "IMAP", "SMTP", "GPG", "SQLite", "WAL",
            "PID", "PPID", "TDD", "shell=False",
            "SessionStart hook", "UserPromptSubmit hook", "Stop hook",
            "wake_watcher", "nudge Event",
            "chat_ask", "chat_notify", "chat_check_messages",
            "chat_message_agent", "chat_register",
            "FLOW_EVENT_TYPES", "zombie process",
        ):
            assert term in DASHBOARD_HTML, f"glossary missing: {term}"

    def test_categories_are_stable(self):
        for title in (
            "protocols · email", "protocols · chat bus", "storage",
            "process model", "claude code internals",
            "chat system — actors", "chat system — wake + deliver",
            "chat system — mcp tools", "chat system — lifecycle",
            "dashboard internals", "quality gates",
        ):
            assert title in DASHBOARD_HTML, f"category missing: {title}"

    def test_search_js_binding_shipped(self):
        assert "bindGlossarySearch" in DASHBOARD_HTML
        assert "show-glossary" in DASHBOARD_HTML

    def test_entries_have_data_term_attr(self):
        """Search walks entries via [data-term] and .textContent."""
        assert 'data-term="mcp"' in DASHBOARD_HTML
        assert 'data-term="wal"' in DASHBOARD_HTML

    def test_auto_collapse_when_search_cleared(self):
        """Entries that auto-expanded during search must collapse again
        after the query is cleared — otherwise the panel stays cluttered."""
        assert "removeAttribute('open')" in DASHBOARD_HTML

    def test_empty_state_shows_searched_term(self):
        """When no match is found, the empty-state message echoes the
        query so the user sees exactly what they typed. The JS populates
        #glossTerm with the lowercased query."""
        assert 'id="glossTerm"' in DASHBOARD_HTML
        assert "emptyTerm.textContent = q" in DASHBOARD_HTML
