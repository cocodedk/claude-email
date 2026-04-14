"""Claude Email Agent — main orchestration loop.

Polls claude@cocode.dk for commands from bb@cocode.dk, executes them via
the claude CLI, and replies with the output. Runs as a systemd service.
"""
import logging
import os
import signal
import sys
import time

from dotenv import load_dotenv

from src.executor import execute_command, extract_command
from src.mailer import send_reply
from src.poller import EmailPoller
from src.security import is_authorized

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    stream=sys.stdout,
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
    return {
        "imap_host": os.environ["IMAP_HOST"],
        "imap_port": int(os.environ.get("IMAP_PORT", "993")),
        "smtp_host": os.environ["SMTP_HOST"],
        "smtp_port": int(os.environ.get("SMTP_PORT", "465")),
        "username": os.environ["EMAIL_ADDRESS"],
        "password": os.environ["EMAIL_PASSWORD"],
        "authorized_sender": os.environ["AUTHORIZED_SENDER"],
        "shared_secret": os.environ.get("SHARED_SECRET", ""),
        "gpg_fingerprint": os.environ.get("GPG_FINGERPRINT", ""),
        "gpg_home": os.environ.get("GPG_HOME"),
        "poll_interval": int(os.environ.get("POLL_INTERVAL", "30")),
        "claude_timeout": int(os.environ.get("CLAUDE_TIMEOUT", "300")),
        "claude_bin": os.environ.get("CLAUDE_BIN", "claude"),
        "state_file": os.environ.get("STATE_FILE", "processed_ids.json"),
    }


def process_email(message, config: dict) -> None:
    """Validate, execute, and reply for a single email message."""
    if not is_authorized(
        message,
        authorized_sender=config["authorized_sender"],
        shared_secret=config["shared_secret"],
        gpg_fingerprint=config["gpg_fingerprint"],
        gpg_home=config["gpg_home"],
    ):
        logger.warning("Unauthorized email dropped")
        return

    command = extract_command(message)
    if not command:
        logger.warning("Authorized email has empty command body — skipping")
        return

    logger.info("Executing command from authorized sender")
    output = execute_command(command, claude_bin=config["claude_bin"], timeout=config["claude_timeout"])

    original_subject = message.get("Subject", "command")
    msg_id = message.get("Message-ID", "")
    subject = original_subject if original_subject.startswith("Re:") else f"Re: {original_subject}"

    send_reply(
        smtp_host=config["smtp_host"],
        smtp_port=config["smtp_port"],
        username=config["username"],
        password=config["password"],
        to=config["authorized_sender"],
        subject=subject,
        body=output,
        in_reply_to=msg_id,
        references=msg_id,
    )


def run_loop(config: dict) -> None:
    """Main polling loop. Runs until SIGTERM/SIGINT received."""
    global _shutdown
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
                try:
                    process_email(msg, config)
                except Exception as exc:
                    logger.error("Error processing message %s: %s", msg_id, exc)
                finally:
                    poller.mark_processed(uid, msg_id)
            poller.disconnect()
        except Exception as exc:
            logger.error("IMAP error: %s — retrying after %ds", exc, config["poll_interval"])

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
    run_loop(cfg)
