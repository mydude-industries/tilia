"""Builds a tiny chat.db with the real schema subset."""
from __future__ import annotations

import sqlite3
from pathlib import Path

# Real attributedBody captured from an is_from_me row of ~/Library/Messages/chat.db; decodes to "lol so good ".
REAL_ATTRIBUTED_BODY_HEX = (
    "040B73747265616D747970656481E803840140848484124E5341747472696275746564537472696E67"
    "008484084E534F626A656374008592848484084E53537472696E67019484012B0C6C6F6C20736F2067"
    "6F6F64208684026949010C928484840C4E5344696374696F6E617279009484016901928496961D5F5F"
    "6B494D4D657373616765506172744174747269627574654E616D658692848484084E534E756D626572"
    "008484074E5356616C7565009484012A84999900868686"
)
REAL_ATTRIBUTED_BODY = bytes.fromhex(REAL_ATTRIBUTED_BODY_HEX)
REAL_ATTRIBUTED_TEXT = "lol so good "

APPLE_NS_2025_01_01 = 757_382_400 * 1_000_000_000  # seconds since 2001-01-01 for 2025-01-01 UTC

SCHEMA = """
CREATE TABLE handle (ROWID INTEGER PRIMARY KEY, id TEXT, service TEXT);
CREATE TABLE chat (ROWID INTEGER PRIMARY KEY, guid TEXT, style INTEGER);
CREATE TABLE message (
  ROWID INTEGER PRIMARY KEY, guid TEXT, text TEXT, attributedBody BLOB,
  handle_id INTEGER DEFAULT 0, service TEXT, date INTEGER, is_from_me INTEGER DEFAULT 0,
  associated_message_type INTEGER DEFAULT 0, cache_has_attachments INTEGER DEFAULT 0, date_read INTEGER DEFAULT 0);
CREATE TABLE chat_message_join (chat_id INTEGER, message_id INTEGER);
CREATE TABLE chat_handle_join (chat_id INTEGER, handle_id INTEGER);
"""


def make_chatdb(path: Path) -> sqlite3.Connection:
    """Writable fixture DB: handle 1 Ann (+15550100001) with 1:1 chat 1 (style 45), handle 2 Bob
    (+15550100002) with 1:1 chat 3, group chat 2 (style 43) containing Ann."""
    conn = sqlite3.connect(path)
    conn.executescript(SCHEMA)
    conn.execute("INSERT INTO handle VALUES (1, '+15550100001', 'iMessage')")
    conn.execute("INSERT INTO handle VALUES (2, '+15550100002', 'iMessage')")
    conn.execute("INSERT INTO chat VALUES (1, 'iMessage;-;+15550100001', 45)")
    conn.execute("INSERT INTO chat VALUES (3, 'iMessage;-;+15550100002', 45)")
    conn.execute("INSERT INTO chat VALUES (2, 'iMessage;+;chat123', 43)")
    conn.execute("INSERT INTO chat_handle_join VALUES (1, 1)")
    conn.execute("INSERT INTO chat_handle_join VALUES (2, 1)")
    conn.execute("INSERT INTO chat_handle_join VALUES (3, 2)")
    conn.commit()
    return conn


def add_message(conn, chat_id=1, text="hi", *, rowid=None, handle_id=1, is_from_me=0,
                service="iMessage", date=APPLE_NS_2025_01_01, attributed_body=None,
                associated_message_type=0, has_attachment=0, date_read=0):
    cur = conn.execute(
        "INSERT INTO message (ROWID, guid, text, attributedBody, handle_id, service, date, is_from_me,"
        " associated_message_type, cache_has_attachments, date_read) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
        (rowid, f"guid-{rowid}-{text}", text, attributed_body, handle_id, service, date, is_from_me,
         associated_message_type, has_attachment, date_read),
    )
    conn.execute("INSERT INTO chat_message_join VALUES (?,?)", (chat_id, cur.lastrowid))
    conn.commit()
    return cur.lastrowid


def apple_ns(dt) -> int:
    """Aware datetime -> Apple-epoch nanoseconds."""
    from datetime import datetime, timezone
    return int((dt - datetime(2001, 1, 1, tzinfo=timezone.utc)).total_seconds() * 1_000_000_000)


def make_addressbook(base: Path) -> Path:
    """Minimal AddressBook folder: Ann Example, Bob Sample (+ an email-only Cy Nobody)."""
    d = Path(base) / "Sources" / "FAKE"
    d.mkdir(parents=True)
    conn = sqlite3.connect(d / "AddressBook-v22.abcddb")
    conn.executescript("""
    CREATE TABLE ZABCDRECORD (Z_PK INTEGER PRIMARY KEY, ZFIRSTNAME TEXT, ZLASTNAME TEXT, ZORGANIZATION TEXT);
    CREATE TABLE ZABCDPHONENUMBER (Z_PK INTEGER PRIMARY KEY, ZOWNER INTEGER, ZFULLNUMBER TEXT);
    CREATE TABLE ZABCDEMAILADDRESS (Z_PK INTEGER PRIMARY KEY, ZOWNER INTEGER, ZADDRESS TEXT);
    INSERT INTO ZABCDRECORD VALUES (1, 'Ann', 'Example', NULL), (2, 'Bob', 'Sample', NULL), (3, 'Cy', 'Nobody', NULL);
    INSERT INTO ZABCDPHONENUMBER VALUES (1, 1, '+1 (555) 010-0001'), (2, 2, '+1 555 010 0002');
    INSERT INTO ZABCDEMAILADDRESS VALUES (1, 3, 'cy@example.com');
    """)
    conn.commit()
    conn.close()
    return Path(base)
