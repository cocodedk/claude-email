"""Extract commands from email bodies and execute via claude CLI."""
import email.message
import logging
import os
import re
import subprocess
from html.parser import HTMLParser

logger = logging.getLogger(__name__)

MAX_OUTPUT_BYTES = 50_000
# Strip quoted-reply trailers so multi-turn email threads don't balloon the
# CLI prompt or chat_db bodies. Each pattern matches the separator that
# introduces the quote and everything after it.
_QUOTE_PATTERNS = (
    # Gmail / most Unix clients: "On <date>, <sender> wrote:"
    re.compile(r"\n\s*On .+? wrote:\n.*", re.DOTALL),
    # Outlook desktop/web: "________________________________\nFrom: ..."
    re.compile(r"\n\s*_{20,}\s*\n\s*From:.*", re.DOTALL),
    # Various clients: "----- Original Message -----"
    re.compile(r"\n\s*-{3,}\s*Original Message\s*-{3,}.*", re.DOTALL | re.IGNORECASE),
)


class _HTMLTextExtractor(HTMLParser):
    def __init__(self):
        super().__init__()
        self._parts: list[str] = []

    def handle_data(self, data: str) -> None:
        self._parts.append(data)

    def get_text(self) -> str:
        return "".join(self._parts)


def _extract_text_from_html(html: str) -> str:
    extractor = _HTMLTextExtractor()
    extractor.feed(html)
    return extractor.get_text()


def extract_command(message: email.message.Message, strip_secret: str = "") -> str:
    """Extract the command text from an email message body.

    Prefers plain-text parts. Falls back to HTML. Strips quoted replies.
    When strip_secret is non-empty, every occurrence of ``AUTH:<secret>``
    is removed from the returned text so the secret never flows into the
    claude CLI prompt, chat_db, logs, or outbound relay emails.
    """
    body = ""

    if message.is_multipart():
        for part in message.walk():
            ct = part.get_content_type()
            if ct == "text/plain":
                payload = part.get_payload(decode=True)
                if payload:
                    charset = part.get_content_charset() or "utf-8"
                    body = payload.decode(charset, errors="replace")
                    break
        if not body:
            for part in message.walk():
                ct = part.get_content_type()
                if ct == "text/html":
                    payload = part.get_payload(decode=True)
                    if payload:
                        charset = part.get_content_charset() or "utf-8"
                        html = payload.decode(charset, errors="replace")
                        body = _extract_text_from_html(html)
                        break
    else:
        payload = message.get_payload(decode=True)
        if payload:
            charset = message.get_content_charset() or "utf-8"
            raw = payload.decode(charset, errors="replace")
            ct = message.get_content_type()
            if ct == "text/html":
                body = _extract_text_from_html(raw)
            else:
                body = raw

    # Strip quoted-reply trailers so the prompt / chat_db body stays small.
    for pattern in _QUOTE_PATTERNS:
        body = pattern.sub("", body)
    if strip_secret:
        body = body.replace(f"AUTH:{strip_secret}", "")
    return body.strip()


def execute_command(
    command: str,
    claude_bin: str = "claude",
    timeout: int = 300,
    max_output_bytes: int = MAX_OUTPUT_BYTES,
    cwd: str | None = None,
    yolo: bool = False,
    extra_env: dict[str, str] | None = None,
    model: str | None = None,
    effort: str | None = None,
    max_budget_usd: str | None = None,
) -> str:
    """Execute a command via the claude CLI and return the output.

    Uses shell=False to prevent command injection.
    Truncates output to max_output_bytes.
    When cwd is set, the claude CLI runs in that directory.
    When yolo is True, passes --dangerously-skip-permissions so the agent
    auto-approves tool calls (needed for non-interactive email-driven runs).
    extra_env is merged over os.environ for the subprocess.
    Returns an error message on timeout or failure.
    """
    argv = [claude_bin]
    if yolo:
        argv.append("--dangerously-skip-permissions")
    if model:
        argv += ["--model", model]
    if effort:
        argv += ["--effort", effort]
    argv += ["--print", command]
    if max_budget_usd:
        argv += ["--max-budget-usd", max_budget_usd]
    env = {**os.environ, **extra_env} if extra_env else None
    logger.info("Executing command via claude CLI (timeout=%ds, yolo=%s)", timeout, yolo)
    try:
        result = subprocess.run(
            argv,
            capture_output=True,
            text=True,
            timeout=timeout,
            shell=False,
            cwd=cwd,
            env=env,
        )
        output = result.stdout
        if result.stderr:
            output += f"\n[stderr]:\n{result.stderr}"
        if len(output.encode()) > max_output_bytes:
            output = output.encode()[:max_output_bytes].decode(errors="replace")
            output += "\n[truncated]"
        return output
    except subprocess.TimeoutExpired:
        logger.error("Command timed out after %ds", timeout)
        return f"[Error: command timed out after {timeout} seconds]"
    except FileNotFoundError:
        logger.error("claude binary not found at %r", claude_bin)
        return f"[Error: claude binary not found at {claude_bin!r}]"
    except Exception as exc:  # noqa: BLE001
        logger.error("Unexpected error executing command: %s", exc)
        return f"[Error: {exc}]"
