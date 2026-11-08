"""Chosen iMessage threads: ingest into the local store, prompt text, and rule-based signals."""

from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone

from . import imessage

PROMPT_HOURS = 48
PROMPT_MAX_MSGS = 150
PROMPT_MAX_CHARS = 16_000
REPLY_WINDOW = timedelta(hours=3)
SIGNAL_LOOKBACK = timedelta(days=7)
_EPOCH = datetime(2000, 1, 1, tzinfo=timezone.utc)


def ingest(store, cfg, conn, now) -> int:
    """Copy new messages of the chosen threads into the store. conn=None opens the real
    chat.db read-only; if it cannot be read, texts are switched off quietly."""
    if not cfg.threads:
        return 0
    own = conn is None
    if own:
        if not imessage.can_read():
            store.set_meta("texts_unavailable", "1")
            return 0
        conn = imessage.open_ro(imessage.CHAT_DB)
    try:
        added = 0
        for t in cfg.threads:
            cid = t["chat_id"]
            cur = store.cursor(cid)
            since = now - timedelta(days=cfg.backfill_days) if cur == 0 else None
            msgs = imessage.fetch_chat(conn, cid, cur, since)
            rows = [(cid, m.rowid, m.date.isoformat(), m.is_from_me, m.text,
                     m.date_read.isoformat() if m.date_read else None)
                    for m in msgs if m.text and m.text.strip()]
            store.add_text_msgs(rows)
            added += len(rows)
            if msgs:
                store.set_cursor(cid, max(m.rowid for m in msgs))
            _refresh_read_times(store, conn, cid, now)
        store.set_meta("texts_unavailable", "0")
        return added
    except sqlite3.OperationalError:
        store.set_meta("texts_unavailable", "1")
        return 0
    finally:
        if own:
            conn.close()


def _refresh_read_times(store, conn, cid, now) -> None:
    """Incoming messages are usually ingested before they are read; pick up read times that
    appeared later for recent incoming rows still lacking one."""
    since = now - SIGNAL_LOOKBACK
    pending = {m["rowid"] for m in store.text_msgs(cid, since=since)
               if not m["from_me"] and not m["read_at"]}
    if not pending:
        return
    for m in imessage.fetch_chat(conn, cid, 0, since):
        if m.rowid in pending and m.date_read:
            store.set_read_at(cid, m.rowid, m.date_read.isoformat())


def _line(m, name) -> str:
    at = datetime.fromisoformat(m["at"]).astimezone().strftime("%Y-%m-%d %H:%M")
    return f"[{at}] {'me' if m['from_me'] else name}: {m['text']}"


def recent_for_prompt(store, cfg, now) -> str:
    since = now - timedelta(hours=PROMPT_HOURS)
    items = []
    for t in cfg.threads:
        for m in store.text_msgs(t["chat_id"], since=since):
            items.append((m["at"], m["rowid"], _line(m, t["name"])))
    items.sort(key=lambda x: (datetime.fromisoformat(x[0]), x[1]))
    lines = [x[2] for x in items][-PROMPT_MAX_MSGS:]
    kept, total = [], 0
    for ln in reversed(lines):
        cost = len(ln) + (1 if kept else 0)
        if total + cost > PROMPT_MAX_CHARS:
            break
        kept.append(ln)
        total += cost
    return "\n".join(reversed(kept))


def window(store, chat_id, n=300, name="them") -> str:
    return "\n".join(_line(m, name) for m in store.text_msgs(chat_id, limit=n))


def _seen(store, kind) -> set[tuple[int, str]]:
    return {(s["chat_id"], s["detail"]) for s in store.signals_since(_EPOCH) if s["kind"] == kind}


def rule_signals(store, cfg, now) -> int:
    added = 0
    seen_q = _seen(store, "unanswered_question")
    seen_s = _seen(store, "long_silence")
    today = now.astimezone().date().isoformat()
    for t in cfg.threads:
        cid = t["chat_id"]
        msgs = store.text_msgs(cid)
        if not msgs:
            continue
        mine = [datetime.fromisoformat(m["at"]) for m in msgs if m["from_me"]]
        for m in msgs:
            if m["from_me"] or "?" not in m["text"] or not m["read_at"]:
                continue
            read = datetime.fromisoformat(m["read_at"])
            # 7-day cap on read time: prevents a backfill flood of stale signals
            if now - read < REPLY_WINDOW or now - read > SIGNAL_LOOKBACK:
                continue
            if any(read < at <= read + REPLY_WINDOW for at in mine):
                continue
            detail = f"rowid:{m['rowid']}"
            if (cid, detail) in seen_q:
                continue
            store.add_signal(cid, "unanswered_question", detail, now)
            seen_q.add((cid, detail))
            added += 1
        newest = datetime.fromisoformat(msgs[-1]["at"])
        if now - newest >= timedelta(hours=24) and (cid, today) not in seen_s:
            days = {datetime.fromisoformat(m["at"]).astimezone().date() for m in msgs
                    if now - timedelta(days=14) <= datetime.fromisoformat(m["at"]) < now}
            if len(days) >= 5:
                store.add_signal(cid, "long_silence", today, now)
                added += 1
    return added
