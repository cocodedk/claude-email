"""Short-circuit answer for obvious plain questions — bypasses the task
pipeline (no Running ack, no branch, no worker). Closes cocodedk Task #40."""
import logging

from src.chat_handlers import send_threaded_reply
from src.executor import execute_command
from src.llm_router import build_question_answer_prompt
from src.question_classifier import looks_like_question

logger = logging.getLogger(__name__)

_QUESTION_TIMEOUT_CAP = 60


def maybe_answer_question(
    message, config: dict, command: str, chat_db, base, u,
) -> bool:
    """Return True iff the short-circuit fired (caller skips its task path)."""
    if not (config.get("llm_router") and looks_like_question(command)):
        return False
    logger.info("question short-circuit: %s", command[:60])
    answer = execute_command(
        command, claude_bin=config["claude_bin"],
        timeout=min(_QUESTION_TIMEOUT_CAP, config["claude_timeout"]),
        cwd=base or None, yolo=config.get("claude_yolo", False),
        extra_env=config.get("claude_extra_env"),
        model=config.get("claude_model"), effort=config.get("claude_effort"),
        max_budget_usd=config.get("claude_max_budget_usd"),
        system_prompt=build_question_answer_prompt(),
        mcp_config=u.mcp_config if u else None,
    )
    send_threaded_reply(
        config, message, answer, tag="Answer",
        chat_db=chat_db, kind="question_answer",
    )
    return True
