"""IMAP email poller — fetches unseen messages and refuses replayed ones.

Two independent keys guard the mailbox, both held in the same ``STATE_FILE``
set. The ``Message-ID`` makes redelivery idempotent. The content-bound
replay key from :mod:`src.replay_guard` makes a *captured* credential
single-use, which the Message-ID cannot do because no credential this
system accepts covers that header. See that module for the full argument.
"""
import email
import email.message
import imaplib
import json
import logging
import os
import ssl
from pathlib import Path

from src.replay_guard import replay_key

logger = logging.getLogger(__name__)

# Two entries per signed message (its Message-ID and its replay key), so this
# is doubled from the 10 000 it held when Message-IDs were the only key —
# otherwise the idempotency horizon would silently halve. The Message-ID is
# inserted first and so is always evicted first, which is the right order:
# losing it costs one duplicate execution of a redelivered mail, while losing
# the replay key costs a captured credential becoming usable again.
_MAX_PROCESSED_IDS = 20_000


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
        # dict[str, None] acts as an insertion-ordered set. We rely on the
        # insertion order so _save_state can drop the OLDEST entries when
        # truncating — a plain set() would lose the most-recent-added id
        # on a random truncation, silently breaking replay protection.
        self._processed_ids: dict[str, None] = self._load_state()
        # uid -> content-bound replay key for the batch currently being
        # worked through, so mark_processed can record it without being
        # handed the message a second time. Reset by every fetch_unseen so
        # a batch abandoned mid-way (shutdown) cannot accumulate here.
        self._pending_keys: dict[str, str] = {}

    # ------------------------------------------------------------------
    # State persistence
    # ------------------------------------------------------------------

    def _load_state(self) -> dict[str, None]:
        if self._state_file.exists():
            try:
                data = json.loads(self._state_file.read_text())
                if not isinstance(data, list) or not all(isinstance(x, str) for x in data):
                    raise TypeError("state file must contain a JSON list of strings")
                # Keep only the most recent entries to bound memory
                if len(data) > _MAX_PROCESSED_IDS:
                    data = data[-_MAX_PROCESSED_IDS:]
                return {x: None for x in data}
            except (json.JSONDecodeError, TypeError):
                logger.warning("State file corrupted, starting fresh")
        return {}

    def _save_state(self) -> None:
        """Atomic write: temp file + rename prevents corruption on crash.

        Trims from the FRONT (oldest insertions) so the newly-added id is
        always preserved.
        """
        while len(self._processed_ids) > _MAX_PROCESSED_IDS:
            # Pop oldest — dict iteration yields keys in insertion order
            self._processed_ids.pop(next(iter(self._processed_ids)))
        data = json.dumps(list(self._processed_ids))
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

        # Reset before the early return below, so the "cleared by every fetch"
        # invariant holds on an empty mailbox too.
        self._pending_keys = {}
        self._conn.select(self._mailbox)
        status, data = self._conn.uid("SEARCH", None, "UNSEEN")
        if status != "OK" or not data or not data[0]:
            return []

        uids = data[0].split()
        results: list[tuple[str, email.message.Message]] = []
        batch: set[str] = set()

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

            if msg_id and (msg_id in self._processed_ids or msg_id in batch):
                logger.info("Skipping already-processed message %s", msg_id)
                continue

            # A replayed credential under a fresh Message-ID looks brand new
            # above and is caught here instead. ``batch`` is consulted as well
            # as the persisted store because the store is only written once
            # main.py has finished with a message: without it, delivering two
            # copies inside a single poll interval would run both.
            key = replay_key(msg)
            if key and (key in self._processed_ids or key in batch):
                logger.warning(
                    "Skipping replayed content re-sent as message %s", msg_id)
                continue

            batch.update(entry for entry in (msg_id, key) if entry)
            self._pending_keys[uid] = key
            results.append((uid, msg))

        return results

    def mark_processed(self, uid: str, message_id: str) -> None:
        """Mark an email seen; record its Message-ID and its replay key.

        Both keys go into the same bounded set. A uid this poller never handed
        out — every caller in the unit suite, and any future one — simply has
        no replay key, and only the Message-ID is stored.
        """
        if self._conn is None:
            return
        try:
            self._conn.uid("STORE", uid.encode(), "+FLAGS", r"(\Seen)")
        except Exception as exc:
            logger.warning("Failed to mark UID %s as seen: %s", uid, exc)

        recorded = [entry for entry in (message_id, self._pending_keys.pop(uid, ""))
                    if entry]
        if recorded:
            for entry in recorded:
                self._processed_ids[entry] = None
            self._save_state()
