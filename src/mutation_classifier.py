"""Read-only vs mutating intent classifier for task bodies.

Server-side, regex-only. Maps to ``tasks.mutates_repo``:

    True  → row stamped 1 → worker creates a per-task branch (or reuses
                            a prior branch if set).
    False → row stamped 0 → worker skips dirty-check + new branch
                            (still checks out prior branch if set).
    None  → row stays NULL → worker treats as mutating (safety default
                             for pre-migration rows + bodies with no
                             verbal signal).

Decision order (after polite-prefix strip):
  1. Empty / polite-only body                  → None
  2. Stripped first token is mutating          → True   (direct command)
  3. Stripped first token is read-only OR a    → False  (question shape
     yes/no question starter (is/does/can/...)          beats a mutating
                                                         word mentioned
                                                         later as the
                                                         topic)
  4. Mutating verb anywhere in the body        → True   ('Thanks, please
                                                         fix the bug')
  5. Short body (≤8 tokens) contains an        → False  ('I have received
     acknowledgment word                                 it.', 'Thanks!')
  6. Otherwise                                 → True   (bias to mutates)

Steps 4 and 5 are ordered so a mixed body like 'Got it, now ship the
migration' still mutates — the explicit mutating verb wins over the
acknowledgment marker.
"""
import re

from src._verbs import MUTATING_VERBS, QUESTION_STARTERS, READ_ONLY_VERBS

_MUTATING = MUTATING_VERBS
_READ_ONLY = READ_ONLY_VERBS
_QUESTION_STARTERS = QUESTION_STARTERS

# Short bodies containing one of these tokens are classified read-only
# so polite acknowledgment replies don't burn a per-task branch. Kept
# narrow on purpose — anything not on this list falls through to the
# conservative mutating default.
_ACKNOWLEDGMENTS = frozenset({
    "received", "got", "noted", "acknowledged", "understood",
    "seen", "confirmed",
    "thanks", "thank", "thx", "ty", "ok", "okay",
})
_ACK_MAX_TOKENS = 8
_NO_CHANGE_STARTERS = frozenset({"no", "nothing", "none", "all", "fine"})
_CHANGE_WORDS = frozenset({
    "change", "changed", "modify", "modified", "update", "updated",
    "edit", "edited",
})

# Sorted by length descending so 'could you' wins over 'could'.
_POLITE_PREFIXES = (
    "could you please", "would you please", "can you please",
    "please can you", "please could you", "please would you",
    "could you", "would you", "can you", "will you",
    "tell me to", "tell me", "please", "pls",
)

_TOKEN_RE = re.compile(r"[a-zA-Z]+")


def _tokens(body: str) -> list[str]:
    return [t.lower() for t in _TOKEN_RE.findall(body)]


def _strip_polite(body: str) -> str:
    """Repeatedly strip leading polite prefixes (case-insensitive)."""
    # Trailing punctuation goes so 'Please.' / 'Pls!' resolve to
    # polite-only zero-signal input.
    s = body.strip().lower().rstrip(" .!?,;:")
    while True:
        before = s
        for prefix in _POLITE_PREFIXES:
            if s.startswith(prefix + " ") or s == prefix:
                s = s[len(prefix):].lstrip(" ,:")
                break
        if s == before:
            return s


def _has_negated_change(tokens: list[str]) -> bool:
    for i, token in enumerate(tokens):
        if token == "not" and i + 1 < len(tokens) and tokens[i + 1] in _CHANGE_WORDS:
            return True
        if (
            token in {"have", "has", "had", "did", "do", "does"}
            and i + 2 < len(tokens)
            and tokens[i + 1] == "not"
            and tokens[i + 2] in _CHANGE_WORDS
        ):
            return True
        if (
            token in {"haven", "hasn", "hadn", "didn", "don", "doesn"}
            and i + 2 < len(tokens)
            and tokens[i + 1] == "t"
            and tokens[i + 2] in _CHANGE_WORDS
        ):
            return True
    return False


def classify_mutation(body: str) -> bool | None:
    """Return True (mutating), False (read-only), or None (no signal)."""
    if not body.strip():
        return None
    stripped_tokens = _tokens(_strip_polite(body))
    if not stripped_tokens:
        return None
    first = stripped_tokens[0]
    if first in _MUTATING:
        return True
    if first in _READ_ONLY or first in _QUESTION_STARTERS:
        return False
    if any(t in _MUTATING for t in stripped_tokens):
        return True
    if (
        len(stripped_tokens) <= _ACK_MAX_TOKENS
        and (
            first in _NO_CHANGE_STARTERS
            or _has_negated_change(stripped_tokens)
        )
    ):
        return False
    if (
        len(stripped_tokens) <= _ACK_MAX_TOKENS
        and any(t in _ACKNOWLEDGMENTS for t in stripped_tokens)
    ):
        return False
    return True
