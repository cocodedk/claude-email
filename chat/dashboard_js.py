"""Concatenator for the split dashboard client script.

The real JS lives in dashboard_js_graph.py (nodes/edges/pulses) and
dashboard_js_stream.py (fetch/SSE/entries). This module joins them into
a single string emitted inside the dashboard page.
"""
from chat.dashboard_js_graph import JS_GRAPH
from chat.dashboard_js_stream import JS_STREAM

DASHBOARD_JS = JS_GRAPH + JS_STREAM
