"""Concatenator for the split dashboard stylesheet.

Real CSS lives in dashboard_css_shell.py (body/topbar/layout),
dashboard_css_graph.py (radar/nodes/feed), dashboard_flow_css.py
(technical-flow panel), and dashboard_glossary_css.py (glossary panel).
This module joins them so every file stays under the 200-line cap.
"""
from chat.dashboard_css_shell import CSS_SHELL
from chat.dashboard_css_graph import CSS_GRAPH
from chat.dashboard_flow_css import CSS_FLOW
from chat.dashboard_glossary_css import CSS_GLOSSARY

DASHBOARD_CSS = CSS_SHELL + CSS_GRAPH + CSS_FLOW + CSS_GLOSSARY
