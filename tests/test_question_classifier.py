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
