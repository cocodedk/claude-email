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
  2. Stripped first token is mutating          → True
  3. Stripped first token is read-only OR a    → False  (question shape
     yes/no question starter (is/does/can/...)          beats a mutating
                                                         word mentioned
                                                         later as the
                                                         topic)
  4. Otherwise                                 → True   (bias to mutates)
"""
import re

_MUTATING = frozenset({
    "implement", "create", "fix", "add", "build", "run", "deploy",
    "push", "merge", "refactor", "update", "delete", "remove",
    "rename", "change", "commit", "stash", "rollback", "revert",
    "rebase", "install", "configure", "rewrite", "drop", "write",
    "modify", "edit", "patch", "scaffold", "generate", "ship",
    "bump", "upgrade", "migrate", "regenerate", "replace",
})

_READ_ONLY = frozenset({
    "explain", "show", "list", "describe", "summarize", "summarise",
    "read", "inspect", "report", "audit", "status", "tell", "print",
    "display", "find",
})

_QUESTION_STARTERS = frozenset({
    "what", "which", "how", "why", "when", "where", "who",
    "is", "are", "was", "were",
    "do", "does", "did",
    "has", "have", "had",
    "can", "could", "should", "would", "will",
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
    return True
