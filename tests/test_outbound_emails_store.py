"""ChatDB inherits OutboundEmailsMixin — moving these two methods out of
chat_db.py keeps the host file under the 200-line cap. This test pins
the public surface so the move is verifiably behavior-preserving."""
from src.chat_db import ChatDB
from src.outbound_emails_store import OutboundEmailsMixin


def test_chatdb_inherits_outbound_mixin():
    assert issubclass(ChatDB, OutboundEmailsMixin)


def test_record_and_find_still_work(tmp_path):
    cdb = ChatDB(str(tmp_path / "x.db"))
    cdb.record_outbound_email("<m@x>", kind="ack", sender_agent="agent-x")
    row = cdb.find_outbound_email("<m@x>")
    assert row["kind"] == "ack"
    assert row["sender_agent"] == "agent-x"
