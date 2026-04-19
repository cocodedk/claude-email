"""Runtime config builder — reads env + .env.test into a dict.

Extracted from main.py to keep main.py under the 200-line cap.
"""
import os

from dotenv import dotenv_values

from src.config_validators import validated_effort
from src.universes import build_universes


def _repo_root() -> str:
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def build_config() -> dict:
    """Return the main.run_loop config dict.

    Loads .env (via load_dotenv, caller's responsibility) into os.environ
    and .env.test (if present) into a separate dict, then builds the
    universe list and folds the common IMAP/SMTP/CLI knobs in.
    """
    shared_secret = os.environ.get("SHARED_SECRET", "")
    _ev = lambda k: os.environ.get(k, "")  # noqa: E731
    extra_env = {
        k: v for k, v in (
            ("CLAUDE_CONFIG_DIR", _ev("CLAUDE_CONFIG_DIR")),
            ("IS_SANDBOX", _ev("IS_SANDBOX")),
        ) if v
    }
    tep = os.path.join(_repo_root(), ".env.test")
    test_env = dotenv_values(tep) if os.path.exists(tep) else {}
    universes = build_universes(os.environ, test_env=test_env)
    return {
        "universes": universes,
        "authorized_senders": [u.sender for u in universes],
        "imap_host": os.environ["IMAP_HOST"], "imap_port": int(os.environ["IMAP_PORT"]),
        "smtp_host": os.environ["SMTP_HOST"], "smtp_port": int(os.environ["SMTP_PORT"]),
        "username": os.environ["EMAIL_ADDRESS"], "password": os.environ["EMAIL_PASSWORD"],
        "authorized_sender": os.environ["AUTHORIZED_SENDER"],
        "shared_secret": shared_secret,
        "gpg_fingerprint": os.environ.get("GPG_FINGERPRINT", ""),
        "gpg_home": os.environ.get("GPG_HOME"),
        "poll_interval": int(os.environ["POLL_INTERVAL"]),
        "claude_timeout": int(os.environ["CLAUDE_TIMEOUT"]),
        "claude_bin": os.environ["CLAUDE_BIN"], "claude_cwd": os.environ["CLAUDE_CWD"],
        "claude_yolo": os.environ.get("CLAUDE_YOLO", "") == "1",
        "claude_model": os.environ.get("CLAUDE_MODEL") or None,
        "claude_effort": validated_effort(os.environ.get("CLAUDE_EFFORT", "").strip() or None),
        "claude_max_budget_usd": os.environ.get("CLAUDE_MAX_BUDGET_USD") or None,
        "claude_extra_env": extra_env,
        "llm_router": os.environ.get("LLM_ROUTER", "") == "1",
        "state_file": os.environ["STATE_FILE"], "email_domain": os.environ["EMAIL_DOMAIN"],
        "chat_db_path": os.environ["CHAT_DB_PATH"], "chat_url": os.environ["CHAT_URL"],
        "service_name_email": os.environ["SERVICE_NAME_EMAIL"],
        "service_name_chat": os.environ["SERVICE_NAME_CHAT"],
        "auth_prefix": f"AUTH:{shared_secret}",
    }
