"""Tests for the plain-question heuristic that bypasses the task pipeline.

Closes the cocodedk Task #40 incident: a plain question got enqueued, the
worker tried to create a per-task branch, hit a dirty tree, and bounced
back. After three retries the user still had no answer. The heuristic
short-circuits BEFORE the LLM router can mis-classify obvious questions.
"""
from src.question_classifier import looks_like_question


class TestLooksLikeQuestion:
    def test_question_mark_at_end_is_question(self):
        assert looks_like_question("Which repo was it pushed to last time?") is True

    def test_interrogative_start_is_question(self):
        # No `?` but starts with "What" — still a question.
        assert looks_like_question("What's the latest commit on master") is True

    def test_imperative_verb_overrides_question_mark(self):
        # Phrased as a question, but the imperative verb "implement" wins —
        # this is a command.
        assert looks_like_question("Why don't you implement the new flow?") is False

    def test_imperative_anywhere_overrides(self):
        # Imperative anywhere in the body → command, even with `?` and
        # interrogative start.
        assert looks_like_question("Can you push the changes?") is False

    def test_command_no_question_mark_is_not_question(self):
        # Plain command, no question shape.
        assert looks_like_question("add a test for the auth flow") is False

    def test_empty_body_is_not_question(self):
        assert looks_like_question("") is False
        assert looks_like_question("   \n\t  ") is False

    def test_status_keyword_is_not_question(self):
        # No `?`, no interrogative — it's a meta-command, not a question.
        assert looks_like_question("status of cocodedk") is False

    def test_question_with_trailing_punctuation(self):
        # `?!` at the end still counts.
        assert looks_like_question("Which repo?!") is True

    def test_question_with_trailing_whitespace(self):
        assert looks_like_question("How does the router work?   \n") is True

    def test_review_imperative_overrides_question(self):
        # "review" is in the imperative list — even phrased politely it's
        # a command.
        assert looks_like_question("Could you review the latest PR?") is False


class TestSharedMutatingVerbs:
    """Drift regression: verbs in src._verbs.MUTATING_VERBS must
    override question shape. Previously these were only in
    mutation_classifier and the question gate misrouted them."""

    def test_shared_mutating_verbs_override_question_shape(self):
        assert looks_like_question("Can you ship the migration?") is False
        assert looks_like_question("Can you write the migration?") is False
        assert looks_like_question("Could you scaffold the tests?") is False
        assert looks_like_question("Would you upgrade the package?") is False
        assert looks_like_question("Will you replace the config?") is False

    def test_polite_you_command_overrides_question_shape(self):
        # 'you please X' / 'you pls X' — captures X past the polite filler.
        assert looks_like_question("Could you please ship the migration?") is False
        assert looks_like_question("Can you pls generate the report?") is False


class TestImperativeVocabIsShared:
    """Drift detector: MUTATING_VERBS must be a subset of _IMPERATIVES.
    If someone forgets the import, this fires before merge."""

    def test_imperatives_includes_all_mutating_verbs(self):
        from src._verbs import MUTATING_VERBS
        from src.question_classifier import _IMPERATIVES
        assert MUTATING_VERBS <= _IMPERATIVES


class TestSharedQuestionStarters:
    """Drift regression: yes/no questions without a trailing '?' relied
    on _INTERROGATIVE_RE. The old hardcoded alternation was missing
    'was', 'were', 'did', 'has', 'have', 'had' so these slipped past
    the short-circuit gate."""

    def test_shared_question_starters_drive_interrogative_match(self):
        assert looks_like_question("Did it create a branch") is True
        assert looks_like_question("Has the worker stopped") is True
        assert looks_like_question("Were tests run") is True
        assert looks_like_question("Was the migration applied") is True
        assert looks_like_question("Have the relays been restarted") is True
        assert looks_like_question("Had the worker finished") is True
