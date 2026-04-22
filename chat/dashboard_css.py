"""Concatenator for the split dashboard stylesheet.

The real CSS lives in dashboard_css_shell.py (body/topbar/layout),
dashboard_css_graph.py (radar/nodes/feed), and dashboard_flow_css.py
(the technical-flow panel). This module joins them so every file stays
under the 200-line cap.
"""
from chat.dashboard_css_shell import CSS_SHELL
from chat.dashboard_css_graph import CSS_GRAPH
from chat.dashboard_flow_css import CSS_FLOW

DASHBOARD_CSS = CSS_SHELL + CSS_GRAPH + CSS_FLOW
