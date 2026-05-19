# LLM-backed mutation classifier — design task

Date: 2026-05-19
Status: Open (design task, not scheduled for implementation)
Owner: TBD

## Why this is open

`src/mutation_classifier.py` is a regex/vocabulary classifier that decides whether an inbound email reply is mutating (`mutates_repo=True`) or read-only (`False`). It runs on every reply and gates `branch_prep`'s dirty-check + branch-creation logic. PR #60 made the gate behavior-sensitive: a wrong call here is the difference between a successful read-only reply and a "repo dirty — commit or stash first" rejection.

On 2026-05-19 we found that obvious read-only replies — `"No issues. I have not changed anything today."`, `"Nothing changed"`, `"All good"` — were being classified as **mutating** because no token in the body matched any of the three hardcoded vocabularies (MUTATING_VERBS, READ_ONLY_VERBS, QUESTION_STARTERS, ACKNOWLEDGMENTS) and the classifier biases to True on ambiguous input. We patched the regex with two more sets (`_NO_CHANGE_STARTERS`, `_CHANGE_WORDS`) plus a `_has_negated_change` helper — see commit `ba8c200`.

That patch ships value today, but it does not fix the underlying smell: **every new natural-language variation of "I'm not doing anything" needs a new code change.** Babak's challenge: "we have an LLM, why are we hardcoding strings?"

## Proposed direction

Move from a regex-only classifier to a **regex pre-filter + LLM fallback**:

1. Regex handles the confidently-decidable cases:
   - First token is an explicit MUTATING_VERB (`fix`, `add`, `ship`, …) → True.
   - First token is an explicit READ_ONLY_VERB / QUESTION_STARTER → False.
   - Body is an empty / polite-only payload → None.
2. Everything else (currently rule 4-6 — the conservative bias-to-True land) hands off to the LLM with a short, focused prompt:
   > "Read this email reply. Decide if the user is asking for code or file changes (return `mutating`) or just confirming / asking / reporting (return `read_only`). Reply with one word."
3. Cache the LLM's verdict on the `tasks` row keyed by `body` hash so the same body doesn't burn repeat tokens, and so a worker run can re-read the verdict without re-classifying.
4. Reuse `src/llm_router.py` (already wired with `ROUTER_MCP_CONFIG_PATH`) so we don't introduce a second LLM client.

## Tradeoffs

- **Cost**: ~1–2 ¢ per ambiguous classification. Bounded because regex catches the obvious cases first.
- **Latency**: ~500 ms–2 s on the ambiguous path. The reply pipeline already does multi-second work elsewhere; this stays inside the existing budget.
- **Reliability**: if the LLM is unreachable, fall back to the current bias-to-True default (status quo).
- **Privacy**: email bodies go to the LLM endpoint on the ambiguous path. `src/llm_router.py` already does this for routing decisions, so the privacy surface doesn't expand.
- **Test surface**: the regex path stays unit-testable; the LLM path needs contract tests against a mock + a small live-call smoke test gated on an env flag.

## Out of scope for this task

- Rewriting `src/_verbs.py`. The shared vocabulary still serves the regex pre-filter.
- Replacing `question_classifier` (`src/question_classifier.py`) with an LLM. Same architecture, separate decision.
- Caching infrastructure beyond the `tasks` row.

## When to pick this up

After the ChatDB transaction wrapper (Phase 0 + Phase 1) lands — that work touches the same DB shape and the same flow path. Doing them sequentially keeps the diffs small and reviewable.

## References

- Existing classifier: `src/mutation_classifier.py`
- Existing LLM router: `src/llm_router.py`
- Patch that motivated this task: commit `ba8c200`
- PR #60 context: commit `f2eb0c6` ("Branch reuse + dirty-gate skip for email follow-ups (v2.2)")
