"""Deterministic question detector — backstops the LLM router which can
ignore its own "questions → reply plainly" rule under ambiguity. Closes
the cocodedk Task #40 leg of the bus reliability incident."""
import re

from src._verbs import MUTATING_VERBS, QUESTION_STARTERS

# Imperative verbs override question shape ("Could you push?" is a
# command). Shared mutating-verb set from src._verbs guarantees a
# new verb added there is recognized here automatically. The locally-
# added extras are inspection/state-change commands that don't mutate
# the repo but are still "do X" rather than "ask about X".
_IMPERATIVES = MUTATING_VERBS | frozenset({
    "audit", "analyze", "review", "cancel", "reset",
})

# Interrogative match built from the shared QUESTION_STARTERS set so
# 'has', 'have', 'had', 'was', 'were', 'did' are recognised even when
# the email lacks a trailing '?'. Sorted by length descending so
# longer alternatives win if a shorter one ever becomes a prefix.
_INTERROGATIVE_RE = re.compile(
    r"^(" + "|".join(sorted(QUESTION_STARTERS, key=len, reverse=True)) + r")\b",
    re.IGNORECASE,
)

# Capture the verb after "you" only when the phrase is a *directive*
# anchored at the start of the body: "Can/Could/Would/Will you (please)?
# X", "Why don't you X", or bare "You X". This avoids treating yes/no
# questions like "Did you push the branch?" as commands just because
# the answer's topic happens to be a mutating verb.
_DIRECTIVE_YOU_VERB_RE = re.compile(
    r"^(?:why\s+don['’]?t\s+you\s+|"
    r"(?:can|could|would|will)\s+you\s+(?:please\s+|pls\s+)?|"
    r"you\s+(?:please\s+|pls\s+)?)([a-z]+)\b"
)


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
    # Anchored 'directive' shape — 'Can/Could/Would/Will you (please)? X',
    # 'Why don't you X', or bare 'You X' — wins over question shape only
    # when X is an imperative. Other auxiliaries ('Did/Has/Have you X')
    # fall through to interrogative handling so factual questions land
    # in the question short-circuit.
    if (m := _DIRECTIVE_YOU_VERB_RE.match(s_lower)) and m.group(1) in _IMPERATIVES:
        return False
    # Interrogative starter wins over a mutating verb mentioned later
    # as the topic of the question — 'Did it create a branch' is
    # asking whether creation happened, not requesting one.
    if _INTERROGATIVE_RE.match(s):
        return True
    # No interrogative shape: an imperative in the first three words
    # marks this as a command even with a trailing '?'
    # ('Please fix the bug?').
    if any(w in _IMPERATIVES for w in s_lower.split()[:3]):
        return False
    return s.rstrip(" \t\n.!").endswith("?")
