"""IMAP email poller — fetches unseen messages, prevents replay via Message-ID store."""
import email
import email.message
import imaplib
import json
import logging
import os
import ssl
from pathlib import Path

logger = logging.getLogger(__name__)

_MAX_PROCESSED_IDS = 10_000


class EmailPoller:
    """Polls an IMAP mailbox for unseen messages.

    Idempotency: tracks processed Message-IDs in a JSON file so that reconnects
    or restarts do not replay already-processed commands.
    """

    def __init__(
        self,
        host: str,
        port: int,
        username: str,
        password: str,
        state_file: str,
        mailbox: str = "INBOX",
    ) -> None:
        self._host = host
        self._port = port
        self._username = username
        self._password = password
        self._state_file = Path(state_file)
        self._mailbox = mailbox
        self._conn: imaplib.IMAP4_SSL | None = None
        self._processed_ids: set[str] = self._load_state()

    # ------------------------------------------------------------------
    # State persistence
    # ------------------------------------------------------------------

    def _load_state(self) -> set[str]:
        if self._state_file.exists():
            try:
                data = json.loads(self._state_file.read_text())
                # Keep only the most recent entries to bound memory
                if len(data) > _MAX_PROCESSED_IDS:
                    data = data[-_MAX_PROCESSED_IDS:]
                return set(data)
            except (json.JSONDecodeError, TypeError):
                logger.warning("State file corrupted, starting fresh")
        return set()

    def _save_state(self) -> None:
        """Atomic write: temp file + rename prevents corruption on crash."""
        ids = list(self._processed_ids)
        if len(ids) > _MAX_PROCESSED_IDS:
            ids = ids[-_MAX_PROCESSED_IDS:]
            self._processed_ids = set(ids)
        data = json.dumps(ids)
        tmp = str(self._state_file) + ".tmp"
        with open(tmp, "w") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        os.rename(tmp, str(self._state_file))

    # ------------------------------------------------------------------
    # Connection management
    # ------------------------------------------------------------------

    def connect(self) -> None:
        """Open an SSL-verified IMAP connection."""
        ctx = ssl.create_default_context()
        self._conn = imaplib.IMAP4_SSL(self._host, self._port, ssl_context=ctx)
        self._conn.login(self._username, self._password)
        logger.info("IMAP connected to %s:%d as %s", self._host, self._port, self._username)

    def disconnect(self) -> None:
        """Close the IMAP connection cleanly."""
        if self._conn:
            try:
                self._conn.close()
            except Exception:
                pass
            try:
                self._conn.logout()
            except Exception:
                pass
            self._conn = None
            logger.info("IMAP disconnected")

    # ------------------------------------------------------------------
    # Fetching
    # ------------------------------------------------------------------

    def fetch_unseen(self) -> list[tuple[str, email.message.Message]]:
        """Return list of (uid, message) tuples for unseen, unprocessed emails."""
        if self._conn is None:
            raise RuntimeError("Not connected — call connect() first")

        self._conn.select(self._mailbox)
        status, data = self._conn.uid("SEARCH", None, "UNSEEN")
        if status != "OK" or not data or not data[0]:
            return []

        uids = data[0].split()
        results: list[tuple[str, email.message.Message]] = []

        for uid_bytes in uids:
            uid = uid_bytes.decode()
            status, msg_data = self._conn.uid("FETCH", uid_bytes, "(RFC822)")
            if status != "OK" or not msg_data or msg_data[0] is None:
                continue
            raw = msg_data[0][1]
            if not isinstance(raw, bytes):
                continue
            msg = email.message_from_bytes(raw)
            msg_id = msg.get("Message-ID", "").strip()

            if msg_id and msg_id in self._processed_ids:
                logger.info("Skipping already-processed message %s", msg_id)
                continue

            results.append((uid, msg))

        return results

    def mark_processed(self, uid: str, message_id: str) -> None:
        """Mark an email as seen and record its Message-ID to prevent replay."""
        if self._conn is None:
            return
        try:
            self._conn.uid("STORE", uid.encode(), "+FLAGS", r"(\Seen)")
        except Exception as exc:
            logger.warning("Failed to mark UID %s as seen: %s", uid, exc)

        if message_id:
            self._processed_ids.add(message_id)
            self._save_state()
