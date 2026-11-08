from datetime import datetime, timedelta

import pytest

from tests.fixtures.make_chatdb import add_message, apple_ns, make_addressbook, make_chatdb
from tilia import contacts, imessage, texts
from tilia.config import Config
from tilia.store import Store

NOW = datetime(2026, 11, 7, 12, 0).astimezone()


@pytest.fixture
def store(tmp_path):
    return Store(tmp_path / "t.db")


@pytest.fixture
def db(tmp_path):
    return make_chatdb(tmp_path / "chat.db")


def msg(db, rowid, ago, text, chat=1, me=0, read_after=None, **kw):
    at = NOW - ago
    read = apple_ns(at + read_after) if read_after is not None else 0
    return add_message(db, chat, text, rowid=rowid, date=apple_ns(at), is_from_me=me,
                       date_read=read, **kw)


def cfg(*chats, days=365):
    return Config(threads=[{"name": n, "chat_id": c} for n, c in chats], backfill_days=days)


H = timedelta(hours=1)
D = timedelta(days=1)


def test_backfill_respects_days_then_incremental(store, db):
    msg(db, 1, 40 * D, "ancient")
    msg(db, 2, 5 * D, "recent")
    c = cfg(("Ann", 1), days=30)
    assert texts.ingest(store, c, db, NOW) == 1
    assert [m["text"] for m in store.text_msgs(1)] == ["recent"]
    assert store.cursor(1) == 2
    msg(db, 3, 1 * H, "new")
    assert texts.ingest(store, c, db, NOW) == 1
    assert [m["text"] for m in store.text_msgs(1)] == ["recent", "new"]
    assert texts.ingest(store, c, db, NOW) == 0


def test_tapbacks_excluded(store, db):
    msg(db, 1, 3 * H, "hello")
    msg(db, 2, 2 * H, "Loved “hello”", associated_message_type=2000)
    assert [m.text for m in imessage.fetch_chat(db, 1, 0, None)] == ["hello"]
    texts.ingest(store, cfg(("Ann", 1)), db, NOW)
    assert len(store.text_msgs(1)) == 1


def test_recent_for_prompt_caps_keep_newest(store, db, tmp_path):
    for i in range(1, 201):
        msg(db, i, timedelta(minutes=300 - i), f"m{i}")
    c = cfg(("Ann", 1))
    texts.ingest(store, c, db, NOW)
    out = texts.recent_for_prompt(store, c, NOW)
    lines = out.splitlines()
    assert len(lines) == 150
    assert lines[-1].endswith("Ann: m200") and "m51" in lines[0]
    assert lines[0].startswith("[") and "] Ann: m51" in lines[0]
    # char cap, newest kept
    store2 = Store(tmp_path / "t2.db")
    store2.add_text_msgs([(1, i, (NOW - timedelta(minutes=500 - i)).isoformat(), i % 2, "x" * 1000)
                          for i in range(1, 41)])
    out2 = texts.recent_for_prompt(store2, c, NOW)
    assert len(out2) <= 16000
    assert out2.splitlines()[-1].startswith("[2026-11-07 04:20] Ann: x")
    assert len(out2.splitlines()) < 40


def test_recent_for_prompt_window_48h(store, db):
    msg(db, 1, 60 * H, "old")
    msg(db, 2, 1 * H, "mine", me=1)
    c = cfg(("Ann", 1))
    texts.ingest(store, c, db, NOW)
    out = texts.recent_for_prompt(store, c, NOW)
    assert "old" not in out and "] me: mine" in out


def test_window_for_rehearsal(store, db):
    for i in range(1, 11):
        msg(db, i, timedelta(hours=20 - i), f"m{i}")
    texts.ingest(store, cfg(("Ann", 1)), db, NOW)
    w = texts.window(store, 1, n=3)
    assert [l.split(": ", 1)[1] for l in w.splitlines()] == ["m8", "m9", "m10"]


def test_unanswered_question_signal_once(store, db):
    c = cfg(("Ann", 1))
    msg(db, 1, 6 * H, "are you coming?", read_after=1 * H)          # read 5h ago, no reply
    msg(db, 2, 8 * H, "dinner?", read_after=1 * H)                  # replied within 3h
    msg(db, 3, timedelta(minutes=390), "sure", me=1)
    msg(db, 4, 5 * H, "no question mark", read_after=1 * H)
    msg(db, 5, 3 * H, "unread?")                                    # never read
    msg(db, 6, 90 * H // 60, "too fresh?", read_after=timedelta(minutes=10))  # read <3h ago
    texts.ingest(store, c, db, NOW)
    assert texts.rule_signals(store, c, NOW) == 1
    sigs = [s for s in store.signals_since(NOW - 7 * D) if s["kind"] == "unanswered_question"]
    assert len(sigs) == 1 and sigs[0]["detail"] == "rowid:1"
    assert texts.rule_signals(store, c, NOW) == 0


def test_long_silence_needs_baseline(store, db):
    c = cfg(("Ann", 1), ("Bob", 3))
    for i in range(5):  # Ann: 5 distinct days, last 3 days ago
        msg(db, 10 + i, (3 + i) * D, f"a{i}", chat=1)
    for i in range(3):  # Bob: only 3 days
        msg(db, 20 + i, (3 + i) * D, f"b{i}", chat=3)
    texts.ingest(store, c, db, NOW)
    assert texts.rule_signals(store, c, NOW) == 1
    sigs = [s for s in store.signals_since(NOW - D) if s["kind"] == "long_silence"]
    assert [s["chat_id"] for s in sigs] == [1]
    assert texts.rule_signals(store, c, NOW) == 0                       # once per day
    assert texts.rule_signals(store, c, NOW + D) == 1                   # next day again


def test_long_silence_not_when_recent(store, db):
    c = cfg(("Ann", 1))
    for i in range(6):
        msg(db, 10 + i, 2 * H + i * D, f"a{i}")
    texts.ingest(store, c, db, NOW)
    assert texts.rule_signals(store, c, NOW) == 0


def test_ingest_without_access_disables_quietly(store, db, monkeypatch, tmp_path):
    monkeypatch.setattr(imessage, "can_read", lambda *a, **k: False)
    assert texts.ingest(store, cfg(("Ann", 1)), None, NOW) == 0
    assert store.get_meta("texts_unavailable") == "1"
    assert imessage.can_read(tmp_path / "missing.db") is False


def test_only_chosen_threads_read(store, db):
    msg(db, 1, 2 * H, "from ann", chat=1)
    msg(db, 2, 2 * H, "from bob", chat=3)
    texts.ingest(store, cfg(("Ann", 1)), db, NOW)
    assert store.text_msgs(3) == [] and len(store.text_msgs(1)) == 1
    assert texts.ingest(store, cfg(), db, NOW) == 0


def test_find_chats_via_fake_addressbook(tmp_path, db):
    ab = make_addressbook(tmp_path / "ab")
    r = contacts.find_chats("ann", base=ab, conn=db)
    assert r == [{"name": "Ann Example", "chat_id": 1, "handle": "+15550100001"}]
    assert [x["chat_id"] for x in contacts.find_chats("SAMPLE", base=ab, conn=db)] == [3]
    assert contacts.find_chats("zzz", base=ab, conn=db) == []


def test_long_silence_dormant_thread_does_not_fire(store, db):
    c = cfg(("Ann", 1))
    for i in range(6):
        msg(db, 10 + i, (30 + i) * D, f"a{i}")
    texts.ingest(store, c, db, NOW)
    assert texts.rule_signals(store, c, NOW) == 0


def test_unanswered_question_read_older_than_7_days_ignored(store, db):
    c = cfg(("Ann", 1))
    msg(db, 1, 9 * D, "old one?", read_after=1 * H)  # read 8 days + 23h ago
    texts.ingest(store, c, db, NOW)
    assert texts.rule_signals(store, c, NOW) == 0


def test_read_time_refreshed_on_later_ingest(store, db):
    c = cfg(("Ann", 1))
    t0 = NOW - 5 * H
    msg(db, 1, 6 * H, "ready?")  # unread when first ingested
    texts.ingest(store, c, db, t0)
    assert texts.rule_signals(store, c, NOW) == 0
    db.execute("UPDATE message SET date_read=? WHERE ROWID=1", (apple_ns(NOW - 4 * H),))
    db.commit()
    texts.ingest(store, c, db, NOW)
    assert texts.rule_signals(store, c, NOW) == 1
    assert texts.rule_signals(store, c, NOW) == 0
