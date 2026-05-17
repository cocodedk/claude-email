# Phase B — Mutation classifier with polite-prefix strip

One task. Adds the deterministic regex classifier that downstream phases use to stamp `mutates_repo` on tasks.

The classifier returns:
- `True` — mutating intent detected (clearly mutating verb anywhere)
- `False` — clearly read-only (leading read-only verb or interrogative *after* stripping politeness)
- `None` — zero signal (empty body or polite-only input after prefix stripping); caller leaves the column NULL so the worker falls back to today's gated behavior

The polite-prefix strip is the reviewer's catch — "can you explain X" was misclassified as mutating in v1 because `can` is not in `_READ_ONLY`. Stripping `please|can you|could you|would you|will you|tell me|pls` before tokenizing makes "can you explain X" → "explain X" → read-only. The "mutating verb anywhere" check still runs first, so "can you commit the changes?" stays mutating via `commit`.

---

## Task B.4: `src/mutation_classifier.py`

**Files:**
- Create: `src/mutation_classifier.py`
- Create: `tests/test_mutation_classifier.py`

- [ ] **Step 1: Write the failing test**

`tests/test_mutation_classifier.py`:

```python
"""Tests for src/mutation_classifier.py — read-only vs mutating intent.

The classifier biases to 'mutates' on ambiguity. NULL (the None return)
is reserved for bodies with zero signal so existing rows + ambiguous
new rows stay safety-gated by the worker."""
import pytest

from src.mutation_classifier import classify_mutation


class TestReadOnly:
    @pytest.mark.parametrize("body", [
        "explain how the bus reaper works",
        "show the last 5 commits on this branch",
        "list the agents currently registered",
        "where is the dispatch token validated?",
        "what is mutates_repo for?",
        "why did task 17 fail?",
        "status",
        "summarize the diff between HEAD and main",
        "read src/chat_relay.py and tell me what it does",
        "inspect the outbound_emails table",
        "How many tasks ran today?",
        "describe the schema",
    ])
    def test_obvious_read_only_returns_false(self, body):
        assert classify_mutation(body) is False


class TestPolitePrefixStrip:
    """v1 reviewer catch: 'can you explain X' is read-only. The polite
    prefix is stripped before classification, but the mutating-verb
    check still runs against the *original* body so 'can you commit X'
    stays mutating via the verb anywhere rule."""

    @pytest.mark.parametrize("body", [
        "can you explain the relay?",
        "could you show me the schema?",
        "would you list the agents?",
        "please describe the bus",
        "tell me what changed in task 17",
        "Pls show recent commits",
    ])
    def test_polite_read_only_returns_false(self, body):
        assert classify_mutation(body) is False

    @pytest.mark.parametrize("body", [
        "can you commit the changes?",
        "please push the branch",
        "could you delete the stale row?",
        "would you rewrite this please",
        "tell me to fix the bus",  # 'fix' anywhere -> mutating
    ])
    def test_polite_mutating_still_returns_true(self, body):
        assert classify_mutation(body) is True


class TestMutating:
    @pytest.mark.parametrize("body", [
        "fix the dirty-repo gate",
        "implement the classifier",
        "add a column to outbound_emails",
        "update the README",
        "refactor chat_handlers into two files",
        "delete the stale wake row for agent-x",
        "rename branch_name to per_task_branch",
        "change the default priority to 5",
        "commit these changes",
        "push the current branch",
        "rewrite the relay loop",
        "build the dashboard CSS bundle",
        "create a new agent for the search service",
        "drop the test database",
        "merge master into this branch",
    ])
    def test_obvious_mutating_returns_true(self, body):
        assert classify_mutation(body) is True


class TestAmbiguity:
    def test_empty_returns_none(self):
        assert classify_mutation("") is None
        assert classify_mutation("   \n\t ") is None

    def test_no_signal_returns_true_not_none(self):
        # 'thinking about X' has no read-only verb and no mutating verb,
        # but it's not zero-signal — body has content. Bias to mutates.
        assert classify_mutation("thinking about the architecture") is True

    def test_mixed_signals_bias_to_mutating(self):
        assert classify_mutation("explain why we should fix the bus") is True

    def test_imperative_inside_question_still_mutates(self):
        assert classify_mutation("can you commit the changes?") is True


class TestCaseAndPunctuation:
    def test_case_insensitive(self):
        assert classify_mutation("EXPLAIN the bus") is False
        assert classify_mutation("FIX the bus") is True

    def test_punctuation_tolerated(self):
        assert classify_mutation("explain: how does this work?") is False
        assert classify_mutation("fix: stop the leak") is True

    def test_leading_imperative_required_for_read_only(self):
        # Mutating verb later in body still wins.
        assert classify_mutation("rewrite this to explain better") is True


class TestStripIdempotent:
    """Multiple polite prefixes stack — 'please can you explain' should
    still strip down to 'explain'."""

    def test_stacked_prefixes_strip(self):
        assert classify_mutation("please can you explain the relay") is False
        assert classify_mutation("could you please show me the schema") is False


class TestPoliteOnlyReturnsNone:
    """Round-3 reviewer catch: body that is *only* a polite prefix
    (no verb at all after stripping) is zero-signal and must return
    None so the row stays NULL-gated, not bias-to-mutating."""

    @pytest.mark.parametrize("body", [
        "please",
        "Please.",
        "Pls!",
        "pls",
        "can you",
        "could you please",
        "would you",
    ])
    def test_polite_only_returns_none(self, body):
        assert classify_mutation(body) is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/test_mutation_classifier.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'src.mutation_classifier'`.

- [ ] **Step 3: Implement the classifier**

`src/mutation_classifier.py`:

```python
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
```

Note on step 2 placement: it runs against the *original* body's tokens, before the polite strip. That's intentional — a mutating verb anywhere ("can you commit") must win regardless of phrasing. The polite strip only governs the leading-token check.

Note on step 3a (the `if not stripped_tokens: return None`): this is the round-3 reviewer fix. Without it, `classify_mutation("please")` falls through to step 4 (bias-to-mutating) and returns `True`, which contradicts the docstring's "None for zero signal" claim and stamps a politeness-only message as mutating. Returning `None` keeps the row NULL-gated and falls back to today's behavior.

- [ ] **Step 4: Run tests**

```
.venv/bin/pytest tests/test_mutation_classifier.py -v
.venv/bin/pytest tests/ -q
```

Expected: all PASS. (Exact count varies with parametrize expansion; capture in Phase H.14.)

- [ ] **Step 5: Commit**

```bash
git add src/mutation_classifier.py tests/test_mutation_classifier.py
git commit -m "feat: conservative mutation classifier with polite-prefix strip"
```
