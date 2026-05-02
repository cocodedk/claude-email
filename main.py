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

from src.chat_handlers import handle_chat_email, maybe_cleanup_db, relay_outbound_messages, send_threaded_reply
from src.config import build_config
from src.dispatch import build_universe_resources, dispatch_by_sender, universes_from_config
from src.email_extract import extract_command
from src.executor import execute_command
from src.ghost_reaper import sweep_ghosts
from src.json_envelope import is_json_email
from src.json_handler import handle_json_email
from src.llm_router import EMAIL_ROUTER_SYSTEM_PROMPT
from src.poller import EmailPoller
from src.security import identify_sender, is_authorized

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


_config = build_config  # alias: tests patch `main._config`


def process_email(message, config: dict, chat_db=None, task_queue=None, worker_manager=None) -> None:
    """Validate, execute, and reply for a single email message."""
    allowed = config.get("authorized_senders") or config.get("authorized_sender")
    # Envelope check (From + Return-Path) is mandatory for BOTH protocols.
    # Plain-text emails additionally need AUTH:<secret> or GPG; JSON emails
    # carry auth in meta.auth, checked inside handle_json_email.
    if not allowed or identify_sender(message, allowed) is None:
        logger.warning("Unauthorized email dropped (envelope)")
        return
    if is_json_email(message) and chat_db is not None and task_queue is not None and worker_manager is not None:
        handle_json_email(message, config, chat_db, task_queue, worker_manager)
        return
    if not is_authorized(
        message, authorized_sender=allowed,
        shared_secret=config["shared_secret"], gpg_fingerprint=config["gpg_fingerprint"],
        gpg_home=config["gpg_home"], chat_db=chat_db,
    ):
        logger.warning("Unauthorized email dropped (plain-text auth)")
        return
    if chat_db is not None and handle_chat_email(
        message, config, chat_db, task_queue=task_queue, worker_manager=worker_manager,
    ):
        return

    command = extract_command(message, strip_secret=config["shared_secret"])
    if not command:
        logger.warning("Authorized email has empty command body — skipping")
        return

    timeout = config["claude_timeout"]
    try:
        send_threaded_reply(
            config, message, f"Command received. Running (up to {timeout}s)...",
            tag="Running", chat_db=chat_db, kind="running_ack",
        )
    except Exception:
        logger.exception("Failed to send progress ack — continuing with execution")

    logger.info("Executing command from authorized sender")
    on = config.get("llm_router")
    u = config.get("_universe")
    output = execute_command(
        command, claude_bin=config["claude_bin"], timeout=timeout,
        cwd=(u.allowed_base if u else config.get("claude_cwd")),
        yolo=config.get("claude_yolo", False),
        extra_env=config.get("claude_extra_env") or None,
        model=config.get("claude_model"), effort=config.get("claude_effort"),
        max_budget_usd=config.get("claude_max_budget_usd"),
        system_prompt=EMAIL_ROUTER_SYSTEM_PROMPT if on else None,
        mcp_config=(u.mcp_config if (on and u) else None),
    )
    send_threaded_reply(
        config, message, output, tag="Result", chat_db=chat_db, kind="result",
    )


def _tick_housekeeping(config: dict, cdb, tq) -> None:
    """Per-universe chores: relay, reap agents, reap ghost tasks, cleanup."""
    try: relay_outbound_messages(config, cdb)
    except Exception: logger.exception("Outbound relay error")
    try:
        for name in cdb.reap_dead_agents():
            logger.info("Agent %s marked disconnected (process exited)", name)
    except Exception: logger.exception("Liveness check error")
    try:
        n = sweep_ghosts(tq)
        if n: logger.warning("Ghost reaper: %d orphaned task(s) marked failed", n)
    except Exception: logger.exception("Ghost reaper error")
    maybe_cleanup_db(cdb)


def run_loop(config: dict) -> None:
    """Main polling loop. Runs until SIGTERM/SIGINT received."""
    global _shutdown
    universes = universes_from_config(config)
    config = {**config, "universes": universes,
              "authorized_senders": config.get("authorized_senders") or [
                  addr for u in universes for addr in u.all_senders
              ]}
    resources = build_universe_resources(universes)
    poller = EmailPoller(
        host=config["imap_host"],
        port=config["imap_port"],
        username=config["username"],
        password=config["password"],
        state_file=config["state_file"],
    )

    logger.info(
        "Claude Email Agent starting. Polling every %ds. Authorized senders: %s",
        config["poll_interval"],
        ", ".join(config["authorized_senders"]),
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
                    dispatch_by_sender(msg, config, resources, process_email)
                except Exception:
                    logger.exception("Error processing message %s from %s", msg_id, from_hdr)
                finally:
                    poller.mark_processed(uid, msg_id)
            poller.disconnect()
        except Exception:
            logger.exception("IMAP error — retrying after %ds", config["poll_interval"])

        # Alias senders point at the same (universe, cdb, tq, wm) bundle,
        # so resources.values() can list the same tuple twice. Dedupe by
        # ChatDB identity so relay / reap / cleanup runs once per universe.
        seen: set[int] = set()
        for _, cdb, tq, _ in resources.values():
            if id(cdb) in seen:
                continue
            seen.add(id(cdb))
            _tick_housekeeping(config, cdb, tq)

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
