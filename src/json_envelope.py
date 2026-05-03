"""JSON envelope parser + builder for the structured-client protocol.

Detection: inbound email whose Content-Type is `application/json`
(either top-level or as a part inside multipart) enters JSON mode.
Backend replies with the same Content-Type. Anything else stays
plain-text — zero impact on existing clients.

Envelope shape (v=1):

    {"v": 1,
     "kind": "...",
     "task_id": 42,
     "body": "...",
     "meta": {"client":"...", "sent_at":"...", "auth":"..."}}

Parser is permissive on unknown fields, strict on required fields for
each kind. Errors surface via KINDS-specific codes so the app can
branch programmatically instead of regex-matching prose.
"""
import email.message
import json
import re as _re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any


V = 1
CONTENT_TYPE = "application/json"

ROUTED_VIA_AGENT = "agent"
ROUTED_VIA_WORKER = "worker"

INBOUND_KINDS = {
    "command", "reply", "status", "cancel",
    "retry", "commit", "reset", "confirm_reset",
    "list_projects",
}


class EnvelopeError(Exception):
    """Raised when an inbound email's body can't be parsed as a valid
    envelope. Carries a stable `code` the outbound error envelope echoes,
    plus an optional `ask_id` salvaged from `meta.ask_id` so the app can
    still correlate the error reply with the originating `chat_ask` even
    when the envelope itself fails validation."""

    def __init__(self, code: str, message: str, ask_id: int | None = None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.ask_id = ask_id


@dataclass
class Envelope:
    v: int
    kind: str
    body: str = ""
    task_id: int | None = None
    project: str | None = None
    priority: int | None = None
    plan_first: bool = False
    drain_queue: bool = False
    new_body: str = ""
    token: str = ""
    auth: str = ""
    client: str = ""
    sent_at: str = ""
    ask_id: int | None = None
    prefer_live_agent: bool = False
    extras: dict[str, Any] = field(default_factory=dict)


def is_json_email(message: email.message.Message) -> bool:
    """True when any part (or the whole message) declares application/json."""
    if message.get_content_type() == CONTENT_TYPE:
        return True
    if message.is_multipart():
        for part in message.walk():
            if part.get_content_type() == CONTENT_TYPE:
                return True
    return False


def _extract_json_text(message: email.message.Message) -> str:
    """Return the decoded text of the first application/json part."""
    if message.get_content_type() == CONTENT_TYPE:
        payload = message.get_payload(decode=True)
        if payload:
            charset = message.get_content_charset() or "utf-8"
            return payload.decode(charset, errors="replace")
    if message.is_multipart():
        for part in message.walk():
            if part.get_content_type() == CONTENT_TYPE:
                payload = part.get_payload(decode=True)
                if payload:
                    charset = part.get_content_charset() or "utf-8"
                    return payload.decode(charset, errors="replace")
    return ""


def parse_envelope(message: email.message.Message) -> Envelope:
    text = _extract_json_text(message).strip()
    if not text:
        raise EnvelopeError("bad_envelope", "no application/json part found")
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise EnvelopeError("bad_envelope", f"JSON parse failed: {exc}") from exc
    if not isinstance(data, dict):
        raise EnvelopeError("bad_envelope", "envelope must be a JSON object")

    # Salvage meta.ask_id as early as possible so validation-failure
    # replies can still carry it for app-side chat_ask correlation.
    meta = data.get("meta") if isinstance(data.get("meta"), dict) else {}
    ask_id = _int_or_none(meta.get("ask_id"))

    v = data.get("v")
    if v != V:
        raise EnvelopeError("bad_envelope", f"unsupported version {v!r}; expected {V}", ask_id=ask_id)

    kind = data.get("kind")
    if kind not in INBOUND_KINDS:
        raise EnvelopeError("unknown_kind", f"kind {kind!r} is not one of {sorted(INBOUND_KINDS)}", ask_id=ask_id)
    return Envelope(
        v=v,
        kind=kind,
        body=str(data.get("body") or ""),
        task_id=_int_or_none(data.get("task_id")),
        project=str(data["project"]) if "project" in data and data["project"] else None,
        priority=_int_or_none(data.get("priority")),
        plan_first=bool(data.get("plan_first", False)),
        drain_queue=bool(data.get("drain_queue", False)),
        new_body=str(data.get("new_body") or ""),
        token=str(data.get("token") or ""),
        auth=str(meta.get("auth") or ""),
        client=str(meta.get("client") or ""),
        sent_at=str(meta.get("sent_at") or ""),
        ask_id=ask_id,
        prefer_live_agent=bool(meta.get("prefer_live_agent", False)),
        extras=data,
    )


def _int_or_none(value) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def build_envelope(
    kind: str, body: str = "", task_id: int | None = None,
    data: dict | None = None, error: dict | None = None,
    ask_id: int | None = None, routed_via: str | None = None,
    progress: dict | None = None,
) -> str:
    """Build an outbound envelope as a JSON string.

    `ask_id` echoes the inbound `meta.ask_id` so the app can match a reply
    to the originating question and unblock the right chat_ask.
    `routed_via` (when set) lands as ``meta.routed_via`` so the app can
    show the user whether the command went to a live agent or a worker.
    `progress` (when set) lands as ``meta.progress`` carrying optional
    ``current/total/percent/label`` fields for the kind=progress path.
    """
    out: dict[str, Any] = {
        "v": V,
        "kind": kind,
        "body": body,
        "meta": {
            "server": "claude-email/1.0",
            "sent_at": datetime.now(timezone.utc).isoformat(),
        },
    }
    if ask_id is not None:
        out["meta"]["ask_id"] = int(ask_id)
    if routed_via:
        out["meta"]["routed_via"] = routed_via
    if progress:
        out["meta"]["progress"] = progress
    if task_id is not None:
        out["task_id"] = int(task_id)
    if data:
        out["data"] = data
    if error:
        out["error"] = error
    return json.dumps(out, separators=(",", ":"))


def strip_auth_from_body(body: str, secret: str) -> str:
    """Same guarantee as executor.extract_command's strip_secret — never
    let the auth token live in downstream storage/logs."""
    if not secret:
        return body
    return _re.sub(_re.escape(f"AUTH:{secret}"), "", body)
