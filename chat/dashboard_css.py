"""Concatenator for the split dashboard stylesheet.

The real CSS lives in dashboard_css_shell.py (body/topbar/layout) and
dashboard_css_graph.py (radar/nodes/feed). This module joins them so
every file stays under the 200-line cap.
"""
from chat.dashboard_css_shell import CSS_SHELL
from chat.dashboard_css_graph import CSS_GRAPH

DASHBOARD_CSS = CSS_SHELL + CSS_GRAPH
