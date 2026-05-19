"""Tests for src/mutation_classifier.py."""
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
        "tell me to fix the bus",
        "can you fix the branch bug?",
        "could you update the README?",
        "please create the migration",
        "will you commit the changes?",
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

    def test_question_shape_wins_over_mutating_topic(self):
        assert classify_mutation("explain why we should fix the bus") is False

    def test_imperative_inside_question_still_mutates(self):
        # Polite-strip removes 'can you', leaving 'commit the changes'.
        assert classify_mutation("can you commit the changes?") is True


class TestShortAcknowledgments:
    """The original user complaint: a polite ack reply ('I have received
    it.') used to fall through to bias-to-mutates and burn a per-task
    branch. Short bodies (≤8 tokens after polite-strip) containing an
    acknowledgment word are now read-only."""

    @pytest.mark.parametrize("body", [
        "I have received it.",
        "Thanks!",
        "Got it",
        "Noted, will check",
        "Acknowledged",
        "Understood",
        "Seen",
        "Confirmed",
        "ok",
        "okay thanks",
    ])
    def test_short_acknowledgments_are_read_only(self, body):
        assert classify_mutation(body) is False

    @pytest.mark.parametrize("body", [
        "Thanks, please fix the bug",
        "Got it, now ship the migration",
        "Ok, update the README",
    ])
    def test_ack_plus_mutating_command_still_mutates(self, body):
        assert classify_mutation(body) is True
