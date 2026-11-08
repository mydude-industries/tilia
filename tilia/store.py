"""SQLite persistence. Timestamps are timezone-aware ISO-8601 strings."""

import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS turns(id INTEGER PRIMARY KEY, at TEXT NOT NULL, role TEXT NOT NULL, text TEXT NOT NULL, state TEXT NOT NULL DEFAULT 'ok');
CREATE TABLE IF NOT EXISTS assignments(id INTEGER PRIMARY KEY, kind TEXT NOT NULL, text TEXT NOT NULL, created TEXT NOT NULL, due TEXT, status TEXT NOT NULL DEFAULT 'open', followed_up_at TEXT, notes TEXT);
CREATE TABLE IF NOT EXISTS checkins(id INTEGER PRIMARY KEY, at TEXT NOT NULL, reason TEXT NOT NULL, text TEXT NOT NULL, sent INTEGER NOT NULL DEFAULT 0, replied INTEGER NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS text_cursor(chat_id INTEGER PRIMARY KEY, last_rowid INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS text_msgs(chat_id INTEGER NOT NULL, rowid_src INTEGER NOT NULL, at TEXT NOT NULL, from_me INTEGER NOT NULL, text TEXT NOT NULL, read_at TEXT, PRIMARY KEY(chat_id, rowid_src));
CREATE TABLE IF NOT EXISTS signals(id INTEGER PRIMARY KEY, at TEXT NOT NULL, chat_id INTEGER NOT NULL, kind TEXT NOT NULL, detail TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS corrections(id INTEGER PRIMARY KEY, at TEXT NOT NULL, text TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS journal(id INTEGER PRIMARY KEY, at TEXT NOT NULL, text TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS pending(id INTEGER PRIMARY KEY, at TEXT NOT NULL, text TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY, value TEXT);
"""
TABLES = ["turns", "assignments", "checkins", "text_cursor", "text_msgs",
          "signals", "corrections", "journal", "pending", "meta"]
# tables with a timestamp column, for forget("day")
_DAY_COL = {"turns": "at", "assignments": "created", "checkins": "at", "text_msgs": "at",
            "signals": "at", "corrections": "at", "journal": "at", "pending": "at"}


@dataclass
class Turn:
    id: int
    at: str
    role: str
    text: str
    state: str


@dataclass
class Assignment:
    id: int
    kind: str
    text: str
    created: str
    due: str | None
    status: str
    followed_up_at: str | None
    notes: str | None


def local_now() -> datetime:
    """The one clock: aware local time. Rows are stamped with it and forget("day") reads
    "today" in the same local zone, so the two always agree on which day a row belongs to."""
    return datetime.now().astimezone()


def _day(iso: str):
    return datetime.fromisoformat(iso).astimezone().date()


class Store:
    def __init__(self, path, clock=local_now):
        self._clock = clock
        self._conn = sqlite3.connect(str(path))
        self._depth = 0  # >0 inside atomic(): writes wait for its single commit
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA secure_delete=ON")  # deleted rows are overwritten
        self._conn.executescript(SCHEMA)
        cols = [r["name"] for r in self._conn.execute("PRAGMA table_info(text_msgs)")]
        if "read_at" not in cols:
            self._conn.execute("ALTER TABLE text_msgs ADD COLUMN read_at TEXT")
        self._conn.commit()
        for suffix in ("", "-wal", "-shm"):  # sidecars made before umask 077 was set
            try:
                Path(str(path) + suffix).chmod(0o600)
            except OSError:
                pass

    def _commit(self):
        if self._depth == 0:
            self._conn.commit()

    def _x(self, sql, args=()):
        cur = self._conn.execute(sql, args)
        self._commit()
        return cur

    @contextmanager
    def atomic(self):
        """Every write inside lands in one transaction, or none do: any exception,
        including KeyboardInterrupt/SystemExit, rolls it all back."""
        self._depth += 1
        try:
            yield self
        except BaseException:
            self._depth -= 1
            if self._depth == 0:
                self._conn.rollback()
            raise
        self._depth -= 1
        if self._depth == 0:
            self._conn.commit()

    # turns
    def add_turn(self, role, text, state="ok"):
        self._x("INSERT INTO turns(at,role,text,state) VALUES (?,?,?,?)",
                (self._clock().isoformat(), role, text, state))

    def recent_turns(self, n) -> list[Turn]:
        rows = self._conn.execute(
            "SELECT * FROM turns ORDER BY id DESC LIMIT ?", (n,)).fetchall()
        return [Turn(r["id"], r["at"], r["role"], r["text"], r["state"]) for r in reversed(rows)]

    # pending
    def add_pending(self, text):
        self._x("INSERT INTO pending(at,text) VALUES (?,?)", (self._clock().isoformat(), text))

    def peek_pending(self) -> list[tuple[int, str]]:
        """(id, text) oldest first, without removing anything."""
        return [(r["id"], r["text"]) for r in
                self._conn.execute("SELECT id,text FROM pending ORDER BY id")]

    def delete_pending(self, upto_id):
        """Remove the rows a reply consumed (ids <= upto_id); newer ones stay."""
        self._x("DELETE FROM pending WHERE id<=?", (upto_id,))

    def take_pending(self) -> list[str]:
        rows = self._conn.execute("SELECT id,text FROM pending ORDER BY id").fetchall()
        self._x("DELETE FROM pending")
        return [r["text"] for r in rows]

    # assignments
    def add_assignment(self, kind, text, due) -> int:
        return self._x("INSERT INTO assignments(kind,text,created,due) VALUES (?,?,?,?)",
                       (kind, text, self._clock().isoformat(), due)).lastrowid

    @staticmethod
    def _asg(r):
        return Assignment(r["id"], r["kind"], r["text"], r["created"], r["due"],
                          r["status"], r["followed_up_at"], r["notes"])

    def open_assignments(self) -> list[Assignment]:
        return [self._asg(r) for r in self._conn.execute(
            "SELECT * FROM assignments WHERE status='open' ORDER BY id")]

    def close_assignment(self, id, status):
        self._x("UPDATE assignments SET status=? WHERE id=?", (status, id))

    def mark_followed_up(self, id, at):
        self._x("UPDATE assignments SET followed_up_at=? WHERE id=?", (at.isoformat(), id))

    def open_challenge(self) -> Assignment | None:
        r = self._conn.execute(
            "SELECT * FROM assignments WHERE status='open' AND kind='challenge' "
            "ORDER BY id DESC LIMIT 1").fetchone()
        return self._asg(r) if r else None

    # checkins
    def add_checkin(self, reason, text, sent):
        self._x("INSERT INTO checkins(at,reason,text,sent) VALUES (?,?,?,?)",
                (self._clock().isoformat(), reason, text, 1 if sent else 0))

    def mark_last_checkin_replied(self):
        self._x("UPDATE checkins SET replied=1 WHERE id=(SELECT MAX(id) FROM checkins WHERE sent=1)")

    def recent_checkins(self, n) -> list[dict]:
        """Oldest first."""
        rows = self._conn.execute("SELECT * FROM checkins ORDER BY id DESC LIMIT ?", (n,))
        return [dict(r) for r in reversed(rows.fetchall())]

    def checkins_today(self, now) -> int:
        today = now.astimezone().date()
        rows = self._conn.execute("SELECT at FROM checkins WHERE sent=1").fetchall()
        return sum(1 for r in rows if _day(r["at"]) == today)

    def unreplied_streak(self) -> int:
        n = 0
        for r in self._conn.execute("SELECT replied FROM checkins WHERE sent=1 ORDER BY id DESC"):
            if r["replied"]:
                break
            n += 1
        return n

    # signals
    def add_signal(self, chat_id, kind, detail, at):
        self._x("INSERT INTO signals(at,chat_id,kind,detail) VALUES (?,?,?,?)",
                (at.isoformat(), chat_id, kind, detail))

    def signals_since(self, dt) -> list[dict]:
        rows = self._conn.execute("SELECT * FROM signals ORDER BY id").fetchall()
        cutoff = dt.astimezone()
        return [dict(r) for r in rows if datetime.fromisoformat(r["at"]) >= cutoff]

    # text messages
    def add_text_msgs(self, rows):
        """rows: (chat_id, rowid, at_iso, from_me, text[, read_at_iso]) tuples."""
        self._conn.executemany(
            "INSERT OR REPLACE INTO text_msgs(chat_id,rowid_src,at,from_me,text,read_at) "
            "VALUES (?,?,?,?,?,?)",
            [(r[0], r[1], r[2], 1 if r[3] else 0, r[4], r[5] if len(r) > 5 else None)
             for r in rows])
        self._commit()

    def text_msgs(self, chat_id, since=None, limit=None) -> list[dict]:
        """Oldest first. With limit, the newest `limit` rows are kept."""
        out = []
        for r in self._conn.execute(
                "SELECT * FROM text_msgs WHERE chat_id=? ORDER BY at, rowid_src", (chat_id,)):
            if since is not None and datetime.fromisoformat(r["at"]) < since.astimezone():
                continue
            out.append({"chat_id": r["chat_id"], "rowid": r["rowid_src"], "at": r["at"],
                        "from_me": bool(r["from_me"]), "text": r["text"],
                        "read_at": r["read_at"]})
        return out[-limit:] if limit else out

    def set_read_at(self, chat_id, rowid, read_at_iso):
        self._x("UPDATE text_msgs SET read_at=? WHERE chat_id=? AND rowid_src=?",
                (read_at_iso, chat_id, rowid))

    def cursor(self, chat_id) -> int:
        r = self._conn.execute("SELECT last_rowid FROM text_cursor WHERE chat_id=?",
                               (chat_id,)).fetchone()
        return r["last_rowid"] if r else 0

    def set_cursor(self, chat_id, rowid):
        self._x("INSERT OR REPLACE INTO text_cursor(chat_id,last_rowid) VALUES (?,?)",
                (chat_id, rowid))

    # corrections, journal, meta
    def add_correction(self, text):
        self._x("INSERT INTO corrections(at,text) VALUES (?,?)", (self._clock().isoformat(), text))

    def corrections(self) -> list[str]:
        return [r["text"] for r in self._conn.execute("SELECT text FROM corrections ORDER BY id")]

    def add_journal(self, text):
        self._x("INSERT INTO journal(at,text) VALUES (?,?)", (self._clock().isoformat(), text))

    def journal_entries(self) -> list[tuple[str, str]]:
        return [(r["at"], r["text"]) for r in
                self._conn.execute("SELECT at,text FROM journal ORDER BY id")]

    def all_assignments(self) -> list[Assignment]:
        return [self._asg(r) for r in
                self._conn.execute("SELECT * FROM assignments ORDER BY id")]

    def get_meta(self, k):
        r = self._conn.execute("SELECT value FROM meta WHERE key=?", (k,)).fetchone()
        return r["value"] if r else None

    def set_meta(self, k, v):
        self._x("INSERT OR REPLACE INTO meta(key,value) VALUES (?,?)", (k, v))

    # forgetting
    def forget(self, scope, now):
        if scope == "all":
            for t in TABLES:
                self._conn.execute(f"DELETE FROM {t}")
        elif scope == "last":
            r = self._conn.execute(
                "SELECT MAX(id) m FROM turns WHERE role='user'").fetchone()
            if r["m"] is not None:
                self._conn.execute("DELETE FROM turns WHERE id>=?", (r["m"],))
        elif scope == "day":
            today = now.astimezone().date()
            for t, col in _DAY_COL.items():
                ids = [r["r"] for r in self._conn.execute(f"SELECT rowid r, {col} c FROM {t}")
                       if _day(r["c"]) == today]
                self._conn.executemany(f"DELETE FROM {t} WHERE rowid=?", [(i,) for i in ids])
        else:
            raise ValueError(scope)
        self._commit()
        if scope == "all" and self._depth == 0:
            # fold the write-ahead log into the file, then rewrite the file without free pages
            self._conn.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchall()
            self._conn.execute("VACUUM")
            self._conn.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchall()

    def stats(self) -> dict[str, int]:
        return {t: self._conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] for t in TABLES}
