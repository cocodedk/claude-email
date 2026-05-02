"""Router-prompt regression tests for the recurring UX complaints:

  1. Plain questions emailed in were getting routed to chat_enqueue_task,
     so each question spawned a worker on a fresh `claude/task-<id>-...`
     branch instead of being answered inline.
  2. "Commit and push" requests fell through to chat_enqueue_task because
     the existing chat_commit_project line in the prompt only mentioned
     "commit", and chat_commit_project itself didn't push anything.

The contract here is the literal prompt text the router LLM consumes —
brittle by nature, but the only way to lock the routing intent in place.
"""
from src.llm_router import build_email_router_prompt

EMAIL_ROUTER_SYSTEM_PROMPT = build_email_router_prompt(reply_to="")


class TestQuestionsRouteToPlainText:
    def test_question_intent_maps_to_plain_text(self):
        assert "question" in EMAIL_ROUTER_SYSTEM_PROMPT
        assert "reply in plain text" in EMAIL_ROUTER_SYSTEM_PROMPT

    def test_question_about_no_longer_enqueues_a_task(self):
        """The old enqueue_task line listed "question about ..." — that
        was the trigger for branch-creation on innocuous questions."""
        assert "question about" not in EMAIL_ROUTER_SYSTEM_PROMPT

    def test_help_me_no_longer_enqueues_a_task(self):
        """"help me" was on the same line; it should also not auto-enqueue."""
        # The phrase may stay in the prompt elsewhere, but it must not be
        # mapped to chat_enqueue_task with priority=10.
        bad = '"help me ...'
        if bad in EMAIL_ROUTER_SYSTEM_PROMPT:
            idx = EMAIL_ROUTER_SYSTEM_PROMPT.index(bad)
            window = EMAIL_ROUTER_SYSTEM_PROMPT[idx:idx + 200]
            assert "chat_enqueue_task" not in window


class TestCommitAndPushRouting:
    def test_push_routed_to_commit_project(self):
        """Anything mentioning push must steer the LLM to chat_commit_project,
        not chat_enqueue_task (which would create a new branch)."""
        # Cheap signal: the word "push" should appear somewhere near
        # chat_commit_project guidance.
        idx = EMAIL_ROUTER_SYSTEM_PROMPT.find("chat_commit_project")
        assert idx != -1, "chat_commit_project must remain documented"
        # Look at the same paragraph (forward 400 chars).
        window = EMAIL_ROUTER_SYSTEM_PROMPT[idx:idx + 400]
        assert "push" in window.lower(), (
            "chat_commit_project guidance must mention push so 'commit and "
            "push' requests don't fall through to chat_enqueue_task"
        )

    def test_commit_push_does_not_route_to_enqueue_task(self):
        """The intent map shouldn't *positively* route push to enqueue_task.
        A negative reinforcement ('NEVER route push through chat_enqueue_task')
        is fine — that's the correction, not the bug.
        """
        for line in EMAIL_ROUTER_SYSTEM_PROMPT.splitlines():
            low = line.lower()
            if "push" not in low or "chat_enqueue_task" not in low:
                continue
            if "never" in low or " not " in low:
                continue
            raise AssertionError(
                f"Found push positively routed to chat_enqueue_task: {line!r}"
            )
