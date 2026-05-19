"""Tests for src/mutation_classifier.py."""
import pytest

from src.mutation_classifier import classify_mutation


class TestNegativeStatusReplies:
    @pytest.mark.parametrize("body", [
        "No issues. I have not changed anything today.",
        "No issues.",
        "I have not changed anything today.",
        "Nothing changed",
        "All good",
    ])
    def test_negative_status_replies_are_read_only(self, body):
        assert classify_mutation(body) is False

    def test_negative_status_with_command_still_mutates(self):
        assert classify_mutation("No issues, please update the README") is True


class TestNegatedPresentTenseMutators:
    """CodeRabbit PR #61: rule order made 'I did not update anything'
    classify as mutating because the bare 'update ∈ _MUTATING' check
    fired before the negation check. Pin the corrected order."""

    @pytest.mark.parametrize("body", [
        "I did not update anything.",
        "did not update anything",
        "I didn't modify the schema.",
        "didn't change anything yet",
        "haven't edited the README",
        "I have not updated the migration.",
        "did not change anything",
    ])
    def test_negated_present_tense_mutating_verb_is_read_only(self, body):
        assert classify_mutation(body) is False


class TestYesNoQuestions:
    @pytest.mark.parametrize("body", [
        "is this expected?",
        "does it still create a branch?",
        "did the previous task finish?",
        "has the worker stopped?",
        "why did it create a new branch?",
        "can it read the repo state?",
        "are the relays running?",
        "should I fix this now?",
        "would the worker pick this up?",
    ])
    def test_yes_no_questions_are_read_only(self, body):
        assert classify_mutation(body) is False


class TestCaseAndPunctuation:
    def test_case_insensitive(self):
        assert classify_mutation("EXPLAIN the bus") is False
        assert classify_mutation("FIX the bus") is True

    def test_punctuation_tolerated(self):
        assert classify_mutation("explain: how does this work?") is False
        assert classify_mutation("fix: stop the leak") is True

    def test_leading_imperative_required_for_read_only(self):
        assert classify_mutation("rewrite this to explain better") is True


class TestStripIdempotent:
    def test_stacked_prefixes_strip(self):
        assert classify_mutation("please can you explain the relay") is False
        assert classify_mutation("could you please show me the schema") is False


class TestSharedVocab:
    """Drift detector: the classifier's verb sets are the ones in
    src._verbs. If someone re-defines them locally, this fires."""

    def test_mutating_set_is_shared(self):
        from src._verbs import MUTATING_VERBS
        from src.mutation_classifier import _MUTATING
        assert _MUTATING is MUTATING_VERBS

    def test_read_only_set_is_shared(self):
        from src._verbs import READ_ONLY_VERBS
        from src.mutation_classifier import _READ_ONLY
        assert _READ_ONLY is READ_ONLY_VERBS

    def test_question_starters_set_is_shared(self):
        from src._verbs import QUESTION_STARTERS
        from src.mutation_classifier import _QUESTION_STARTERS
        assert _QUESTION_STARTERS is QUESTION_STARTERS

    def test_audit_stays_read_only_after_share(self):
        # 'audit' is in question_classifier._IMPERATIVES too (it's a
        # command, not a question), but in *this* classifier it must
        # remain read-only — no branch.
        assert classify_mutation("audit the codebase") is False
        assert classify_mutation("could you audit the relay?") is False


class TestPoliteOnlyReturnsNone:
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
