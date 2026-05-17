"""Deterministic question detector — backstops the LLM router which can
ignore its own "questions → reply plainly" rule under ambiguity. Closes
the cocodedk Task #40 leg of the bus reliability incident."""
import re

from src._verbs import MUTATING_VERBS

# Imperative verbs override question shape ("Could you push?" is a
# command). Shared mutating-verb set from src._verbs guarantees a
# new verb added there is recognized here automatically. The locally-
# added extras are inspection/state-change commands that don't mutate
# the repo but are still "do X" rather than "ask about X".
_IMPERATIVES = MUTATING_VERBS | frozenset({
    "audit", "analyze", "review", "cancel", "reset",
})

_INTERROGATIVE_RE = re.compile(
    r"^(what|which|how|why|when|where|who|can|could|will|would|is|are|do|does|should)\b",
    re.IGNORECASE,
)

# Capture the verb after "you", optionally skipping a polite filler
# ("please" / "pls") so "could you please ship the migration?" detects
# "ship" as the action and overrides question shape.
_YOU_VERB_RE = re.compile(r"\byou\s+(?:please\s+|pls\s+)?([a-z]+)")


def looks_like_question(body: str) -> bool:
    """Return True when ``body`` is an obvious plain-text question.

    Caller (``main.process_email``) uses the result to bypass the task
    pipeline — direct ``claude --print`` answer instead of branch +
    worker spawn. False = let the LLM router handle it as before.
    """
    s = body.strip()
    if not s:
        return False
    s_lower = s.lower()
    if any(w in _IMPERATIVES for w in s_lower.split()[:3]):
        return False
    if (m := _YOU_VERB_RE.search(s_lower)) and m.group(1) in _IMPERATIVES:
        return False
    if s.rstrip(" \t\n.!").endswith("?"):
        return True
    return _INTERROGATIVE_RE.match(s) is not None
