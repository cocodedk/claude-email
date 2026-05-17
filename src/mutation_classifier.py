"""Conservative read-only vs mutating intent classifier for task bodies.

Server-side, regex-only, biased to "mutates" on any ambiguity. Output
maps directly to ``tasks.mutates_repo``:

    True  → row stamped 1 → worker behaves as today (clean + new branch
                            or reuse prior branch if set)
    False → row stamped 0 → worker skips dirty check + skips new branch
                            (but still checks out prior branch if set,
                            see src.branch_prep)
    None  → row stays NULL → worker behaves as today (gated)

The NULL pass-through is what protects the 1000+ existing task rows and
any genuinely ambiguous future input from a behavior change.

Decision order:
  1. Empty / whitespace-only body                 → None
  2. Any mutating verb anywhere in the body       → True
  3. After stripping polite prefixes:
       a. nothing left (polite-only input)        → None
       b. first token is read-only / interrogative → False
       c. otherwise                                → True (bias to mutates)
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
    "what", "which", "how", "why", "when", "where", "who",
})

# Polite prefixes are stripped (greedily, repeatedly) before step 3.
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
    # Strip trailing punctuation so 'Please.' / 'Pls!' are recognized as
    # polite-only zero-signal input (round-5 fix). _tokens() already
    # ignores punctuation downstream so this only affects prefix matching.
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
    tokens = _tokens(body)
    if not tokens:
        return None
    if any(t in _MUTATING for t in tokens):
        return True
    stripped_tokens = _tokens(_strip_polite(body))
    if not stripped_tokens:
        return None  # polite-only input — zero signal
    if stripped_tokens[0] in _READ_ONLY:
        return False
    return True
