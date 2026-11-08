from __future__ import annotations

import sqlite3
from pathlib import Path

import phonenumbers

ADDRESSBOOK = Path("~/Library/Application Support/AddressBook")


def normalize_handle(h: str, region: str = "US") -> str:
    """Phone -> E.164; email/unparseable -> stripped lowercase."""
    s = (h or "").strip()
    if "@" not in s:
        try:
            num = phonenumbers.parse(s, region)
            if phonenumbers.is_possible_number(num):
                return phonenumbers.format_number(num, phonenumbers.PhoneNumberFormat.E164)
        except phonenumbers.NumberParseException:
            pass
    return s.lower()


def _db_paths(base: Path) -> list[Path]:
    base = Path(base).expanduser()
    paths = sorted(base.glob("Sources/*/AddressBook-v22.abcddb"))
    root = base / "AddressBook-v22.abcddb"
    if root.exists():
        paths.append(root)
    return paths


def _display(first, last, org, handle: str) -> str:
    name = " ".join(p.strip() for p in (first, last) if p and p.strip())
    return name or (org or "").strip() or handle


_QUERIES = (
    "SELECT p.ZFULLNUMBER, r.ZFIRSTNAME, r.ZLASTNAME, r.ZORGANIZATION "
    "FROM ZABCDPHONENUMBER p JOIN ZABCDRECORD r ON p.ZOWNER = r.Z_PK",
    "SELECT e.ZADDRESS, r.ZFIRSTNAME, r.ZLASTNAME, r.ZORGANIZATION "
    "FROM ZABCDEMAILADDRESS e JOIN ZABCDRECORD r ON e.ZOWNER = r.Z_PK",
)


def load_contacts(base: Path | None = None) -> dict[str, str]:
    """Normalized handle -> display name, across all AddressBook sources."""
    out: dict[str, str] = {}
    for path in _db_paths(ADDRESSBOOK if base is None else base):
        conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        try:
            for q in _QUERIES:
                for raw, first, last, org in conn.execute(q):
                    if not raw or not raw.strip():
                        continue
                    key = normalize_handle(raw)
                    out.setdefault(key, _display(first, last, org, raw.strip()))
        finally:
            conn.close()
    return out


def find_chats(name_query: str, base: Path | None = None, conn=None) -> list[dict]:
    """1:1 chats whose other party's AddressBook name contains name_query (case-insensitive).
    Returns [{"name", "chat_id", "handle"}]. `conn` is a chat.db connection (default: read-only
    real chat.db); `base` is the AddressBook folder."""
    q = (name_query or "").strip().lower()
    if not q:
        return []
    names = {h: n for h, n in load_contacts(base).items() if q in n.lower()}
    if not names:
        return []
    own = conn is None
    if own:
        from .imessage import CHAT_DB, open_ro
        conn = open_ro(CHAT_DB)
    try:
        rows = conn.execute(
            "SELECT c.ROWID, h.id FROM chat c "
            "JOIN chat_handle_join chj ON chj.chat_id = c.ROWID "
            "JOIN handle h ON h.ROWID = chj.handle_id WHERE c.style = 45 ORDER BY c.ROWID").fetchall()
    finally:
        if own:
            conn.close()
    out, seen = [], set()
    for chat_id, handle in rows:
        name = names.get(normalize_handle(handle))
        if name and chat_id not in seen:
            seen.add(chat_id)
            out.append({"name": name, "chat_id": chat_id, "handle": handle})
    return out
