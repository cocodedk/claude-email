"""Deterministic question detector — backstops the LLM router which can
ignore its own "questions → reply plainly" rule under ambiguity. Closes
the cocodedk Task #40 leg of the bus reliability incident."""
import re

# Imperative verbs override question shape ("Could you push?" is a
# command). Curated from src.llm_router's intent map.
_IMPERATIVES = frozenset({
    "implement", "create", "fix", "add", "build", "run", "deploy",
    "push", "merge", "refactor", "audit", "analyze", "review",
    "update", "delete", "remove", "cancel", "reset", "commit", "stash",
    "rollback", "revert", "rebase", "install", "configure",
})

_INTERROGATIVE_RE = re.compile(
    r"^(what|which|how|why|when|where|who|can|could|will|would|is|are|do|does|should)\b",
    re.IGNORECASE,
)

# Imperative as the FIRST 3 words, or directly after "you" — "commit" as
# a noun later in a question doesn't count.
_YOU_VERB_RE = re.compile(r"\byou\s+([a-z]+)")


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
