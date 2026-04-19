"""Claude Email Agent — main orchestration loop.

Polls for commands from the authorized sender, executes them via
the claude CLI, and replies with the output. Runs as a systemd service.
"""
import logging
import logging.handlers
import os
import signal
import sys
import time

from dotenv import load_dotenv

from src.chat_db import ChatDB
from src.chat_handlers import (
    handle_chat_email,
    maybe_cleanup_db,
    relay_outbound_messages,
    send_threaded_reply,
)
from src.config_validators import validated_effort
from src.executor import execute_command, extract_command
from src.llm_router import EMAIL_ROUTER_SYSTEM_PROMPT, ROUTER_MCP_CONFIG_PATH as _ROUTER_MCP_CONFIG
from src.poller import EmailPoller
from src.security import is_authorized

load_dotenv()

_LOG_FILE = os.environ.get("LOG_FILE", os.path.join(os.path.dirname(__file__), "claude-email.log"))
_log_handler = logging.handlers.RotatingFileHandler(
    _LOG_FILE, maxBytes=10_240, backupCount=7
)
_log_handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    handlers=[logging.StreamHandler(sys.stdout), _log_handler],
)
logger = logging.getLogger(__name__)

_shutdown = False


def _handle_signal(signum, frame):  # noqa: ANN001
    global _shutdown
    logger.info("Received signal %d — shutting down gracefully", signum)
    _shutdown = True


signal.signal(signal.SIGTERM, _handle_signal)
signal.signal(signal.SIGINT, _handle_signal)


def _config() -> dict:
    shared_secret = os.environ.get("SHARED_SECRET", "")
    _ev = lambda k: os.environ.get(k, "")  # noqa: E731
    extra_env = {k: v for k, v in (("CLAUDE_CONFIG_DIR", _ev("CLAUDE_CONFIG_DIR")), ("IS_SANDBOX", _ev("IS_SANDBOX"))) if v}
    return {
        "imap_host": os.environ["IMAP_HOST"],
        "imap_port": int(os.environ["IMAP_PORT"]),
        "smtp_host": os.environ["SMTP_HOST"],
        "smtp_port": int(os.environ["SMTP_PORT"]),
        "username": os.environ["EMAIL_ADDRESS"],
        "password": os.environ["EMAIL_PASSWORD"],
        "authorized_sender": os.environ["AUTHORIZED_SENDER"],
        "shared_secret": shared_secret,
        "gpg_fingerprint": os.environ.get("GPG_FINGERPRINT", ""),
        "gpg_home": os.environ.get("GPG_HOME"),
        "poll_interval": int(os.environ["POLL_INTERVAL"]),
        "claude_timeout": int(os.environ["CLAUDE_TIMEOUT"]),
        "claude_bin": os.environ["CLAUDE_BIN"],
        "claude_cwd": os.environ["CLAUDE_CWD"],
        "claude_yolo": os.environ.get("CLAUDE_YOLO", "") == "1",
        "claude_model": os.environ.get("CLAUDE_MODEL") or None,
        "claude_effort": validated_effort(os.environ.get("CLAUDE_EFFORT", "").strip() or None),
        "claude_max_budget_usd": os.environ.get("CLAUDE_MAX_BUDGET_USD") or None,
        "claude_extra_env": extra_env,
        "llm_router": os.environ.get("LLM_ROUTER", "") == "1",
        "state_file": os.environ["STATE_FILE"],
        "email_domain": os.environ["EMAIL_DOMAIN"],
        "chat_db_path": os.environ["CHAT_DB_PATH"],
        "chat_url": os.environ["CHAT_URL"],
        "service_name_email": os.environ["SERVICE_NAME_EMAIL"],
        "service_name_chat": os.environ["SERVICE_NAME_CHAT"],
        "auth_prefix": f"AUTH:{shared_secret}",
    }


def process_email(message, config: dict, chat_db=None) -> None:
    """Validate, execute, and reply for a single email message."""
    if not is_authorized(
        message,
        authorized_sender=config["authorized_sender"],
        shared_secret=config["shared_secret"],
        gpg_fingerprint=config["gpg_fingerprint"],
        gpg_home=config["gpg_home"],
        chat_db=chat_db,
    ):
        logger.warning("Unauthorized email dropped")
        return

    # Chat routing: when chat_db is provided, try chat system first
    if chat_db is not None and handle_chat_email(message, config, chat_db):
        return

    command = extract_command(message, strip_secret=config["shared_secret"])
    if not command:
        logger.warning("Authorized email has empty command body — skipping")
        return

    timeout = config["claude_timeout"]
    try:
        send_threaded_reply(config, message, f"Command received. Running (up to {timeout}s)...")
    except Exception:
        logger.exception("Failed to send progress ack — continuing with execution")

    logger.info("Executing command from authorized sender")
    on = config.get("llm_router")
    output = execute_command(
        command, claude_bin=config["claude_bin"], timeout=timeout,
        cwd=config.get("claude_cwd"), yolo=config.get("claude_yolo", False),
        extra_env=config.get("claude_extra_env") or None,
        model=config.get("claude_model"), effort=config.get("claude_effort"),
        max_budget_usd=config.get("claude_max_budget_usd"),
        system_prompt=EMAIL_ROUTER_SYSTEM_PROMPT if on else None, mcp_config=_ROUTER_MCP_CONFIG if on else None,
    )
    send_threaded_reply(config, message, output)


def run_loop(config: dict) -> None:
    """Main polling loop. Runs until SIGTERM/SIGINT received."""
    global _shutdown
    chat_db = ChatDB(config["chat_db_path"])
    poller = EmailPoller(
        host=config["imap_host"],
        port=config["imap_port"],
        username=config["username"],
        password=config["password"],
        state_file=config["state_file"],
    )

    logger.info(
        "Claude Email Agent starting. Polling every %ds. Authorized sender: %s",
        config["poll_interval"],
        config["authorized_sender"],
    )

    while not _shutdown:
        try:
            poller.connect()
            messages = poller.fetch_unseen()
            for uid, msg in messages:
                if _shutdown:
                    break
                msg_id = msg.get("Message-ID", "").strip()
                from_hdr = msg.get("From", "")
                try:
                    process_email(msg, config, chat_db=chat_db)
                except Exception:
                    logger.exception("Error processing message %s from %s", msg_id, from_hdr)
                finally:
                    poller.mark_processed(uid, msg_id)
            poller.disconnect()
        except Exception:
            logger.exception("IMAP error — retrying after %ds", config["poll_interval"])

        try:
            relay_outbound_messages(config, chat_db)
        except Exception:
            logger.exception("Outbound relay error")

        try:
            reaped = chat_db.reap_dead_agents()
            for name in reaped:
                logger.info("Agent %s marked disconnected (process exited)", name)
        except Exception:
            logger.exception("Liveness check error")

        maybe_cleanup_db(chat_db)

        for _ in range(config["poll_interval"]):
            if _shutdown:
                break
            time.sleep(1)

    logger.info("Shutdown complete")


if __name__ == "__main__":
    try:
        cfg = _config()
    except KeyError as exc:
        logger.error("Missing required environment variable: %s", exc)
        sys.exit(1)
    if not cfg["gpg_fingerprint"] and not cfg["shared_secret"]:
        logger.error("FATAL: Neither GPG_FINGERPRINT nor SHARED_SECRET is set — refusing to start")
        sys.exit(1)
    run_loop(cfg)
