"""Shared verb vocabulary for intent classifiers.

`mutation_classifier` and `question_classifier` both encode the same
"is this a command vs a question" intuition for different gates in
the email pipeline (first gate: short-circuit answer; second gate:
branch creation). Drift between their verb lists previously caused
"can you ship the migration?" to short-circuit as a question because
`ship` was in mutation_classifier but missing from question_classifier.
This module is the single source of truth — adding a verb here makes
both classifiers see it.
"""

MUTATING_VERBS = frozenset({
    "implement", "create", "fix", "add", "build", "run", "deploy",
    "push", "merge", "refactor", "update", "delete", "remove",
    "rename", "change", "commit", "stash", "rollback", "revert",
    "rebase", "install", "configure", "rewrite", "drop", "write",
    "modify", "edit", "patch", "scaffold", "generate", "ship",
    "bump", "upgrade", "migrate", "regenerate", "replace",
})

READ_ONLY_VERBS = frozenset({
    "explain", "show", "list", "describe", "summarize", "summarise",
    "read", "inspect", "report", "audit", "status", "tell", "print",
    "display", "find",
})

QUESTION_STARTERS = frozenset({
    "what", "which", "how", "why", "when", "where", "who",
    "is", "are", "was", "were",
    "do", "does", "did",
    "has", "have", "had",
    "can", "could", "should", "would", "will",
})
