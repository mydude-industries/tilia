"""Read-only access to macOS Messages chat.db. Never writes, never sends."""

from __future__ import annotations

import sqlite3
import sys
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .typedstream import decode_attributed_body

CHAT_DB = Path("~/Library/Messages/chat.db").expanduser()
APPLE_EPOCH = datetime(2001, 1, 1, tzinfo=timezone.utc)

_SELECT = """
SELECT m.ROWID, m.guid, c.ROWID, c.guid, COALESCE(h.id, ''), m.is_from_me,
       COALESCE(m.service, ''), m.text, m.attributedBody, m.date, c.style,
       m.associated_message_type, m.cache_has_attachments, m.date_read
FROM message m
JOIN chat_message_join cmj ON cmj.message_id = m.ROWID
JOIN chat c ON c.ROWID = cmj.chat_id
LEFT JOIN handle h ON h.ROWID = m.handle_id
"""


@dataclass
class Msg:
    rowid: int
    guid: str
    chat_id: int
    chat_guid: str
    handle: str
    is_from_me: bool
    service: str
    text: str | None
    date: datetime
    is_group: bool
    is_tapback: bool
    has_attachment: bool
    date_read: datetime | None = None


def open_ro(path: Path) -> sqlite3.Connection:
    return sqlite3.connect(f"file:{Path(path).expanduser()}?mode=ro", uri=True)


def can_read(path: Path | None = None) -> bool:
    path = CHAT_DB if path is None else path
    try:
        conn = open_ro(path)
        try:
            conn.execute("SELECT 1 FROM message LIMIT 1").fetchall()
        finally:
            conn.close()
        return True
    except (sqlite3.OperationalError, PermissionError):
        return False


def _to_datetime(raw: int | None) -> datetime:
    """Apple timestamp (ns, or legacy seconds) -> aware local time. Never raises."""
    try:
        raw = raw or 0
        seconds = raw if abs(raw) < 100_000_000_000 else raw / 1e9
        return (APPLE_EPOCH + timedelta(seconds=seconds)).astimezone()
    except (OverflowError, ValueError, TypeError):
        return APPLE_EPOCH.astimezone()


def _row_to_msg(r: tuple) -> Msg:
    text = r[7] if r[7] is not None else decode_attributed_body(r[8])
    return Msg(
        rowid=r[0], guid=r[1], chat_id=r[2], chat_guid=r[3], handle=r[4],
        is_from_me=bool(r[5]), service=r[6], text=text, date=_to_datetime(r[9]),
        is_group=r[10] == 43, is_tapback=bool(r[11]), has_attachment=bool(r[12]),
        date_read=_to_datetime(r[13]) if r[13] else None,
    )


def fetch_chat(conn: sqlite3.Connection, chat_id: int, after_rowid: int = 0,
               since: datetime | None = None) -> list[Msg]:
    """Non-tapback messages of one chat, oldest first, with ROWID > after_rowid and
    (if given) sent at or after `since`. Unreadable rows are skipped."""
    rows = conn.execute(
        _SELECT + " WHERE c.ROWID = ? AND m.ROWID > ? AND COALESCE(m.associated_message_type, 0) = 0"
        " ORDER BY m.ROWID", (chat_id, after_rowid)).fetchall()
    out = []
    for r in rows:
        try:
            m = _row_to_msg(r)
        except Exception as e:
            print(f"tilia: skipping unreadable message rowid {r[0]}: {type(e).__name__}",
                  file=sys.stderr)
            continue
        if since is not None and m.date < since.astimezone():
            continue
        out.append(m)
    return out
