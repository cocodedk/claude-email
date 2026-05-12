"""Envelope-version constants and negotiation.

Owns the server's protocol ceiling ``V`` and the per-response
``negotiate_v(client_v)`` helper. Lives in its own tiny module so
that ``src.envelope_builder`` and ``src.json_envelope`` can both
import from a neutral point without forming an import cycle.

The previous arrangement had ``envelope_builder`` import ``V`` from
``json_envelope``, while ``json_envelope`` re-exported
``build_envelope`` from ``envelope_builder`` — fine when
``json_envelope`` was loaded first, ImportError when
``envelope_builder`` was loaded first. Codex review flagged this as
P2; pulling ``V`` out here removes the directional dependency.
"""

V = 2


def negotiate_v(client_v: int) -> int:
    """Return the envelope version to use in the response.

    Caps at the server's ``V``; floors at 1 so a malformed or missing
    inbound ``v`` falls back to legacy shape rather than crashing or
    silently advertising support the server can't honor.
    """
    if client_v < 1:
        return 1
    return min(client_v, V)
