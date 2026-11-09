import os
import stat
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from tilia import config, paths
from tilia.store import Store

REPO = Path(__file__).resolve().parent.parent
NOW = datetime(2026, 11, 7, 15, 0).astimezone()


@pytest.fixture
def store(home):
    return Store(paths.db_path())


def test_data_dir_inside_repo_refused(monkeypatch):
    monkeypatch.setenv("TILIA_HOME", str(REPO / "data"))
    with pytest.raises(RuntimeError):
        paths.data_dir()
    assert not (REPO / "data").exists()


def test_data_dir_inside_repo_via_symlink_refused(tmp_path, monkeypatch):
    link = tmp_path / "link"
    link.symlink_to(REPO)
    monkeypatch.setenv("TILIA_HOME", str(link / "data"))
    with pytest.raises(RuntimeError):
        paths.data_dir()


def test_data_dir_mode_0700(home):
    d = paths.data_dir()
    assert d == home.resolve() or d == home
    assert stat.S_IMODE(os.stat(d).st_mode) == 0o700
    assert paths.db_path().parent == d
    assert paths.config_path().name == "config.toml"


def test_config_roundtrip_defaults_and_threads(home):
    c = config.load()
    assert c.model == "sonnet" and c.chat_id is None and c.checkin_cap == 2
    assert (c.quiet_start, c.quiet_end) == (22, 9)
    assert c.referrals == "never" and c.tone == "very_direct" and c.goal == ""
    c.chat_id = 1234
    c.goal = 'say "hi" \\ more'
    c.battery_awake = True
    c.paused_until = "2026-11-08T09:00:00-08:00"
    c.threads = [{"name": "Ann Test", "chat_id": 5}, {"name": "Bo", "chat_id": 6}]
    config.save(c)
    assert config.load() == c
    c.paused_until = None
    config.save(c)
    assert config.load().paused_until is None


def test_config_ignores_unknown_keys(home):
    paths.config_path().write_text('model = "opus"\nbogus = 3\n')
    c = config.load()
    assert c.model == "opus" and c.checkin_cap == 2


def test_turns_and_recent_order(store):
    store.add_turn("user", "a")
    store.add_turn("coach", "b", state="crisis")
    store.add_turn("user", "c")
    t = store.recent_turns(2)
    assert [x.text for x in t] == ["b", "c"]
    assert t[0].role == "coach" and t[0].state == "crisis"


def test_pending_take_clears(store):
    store.add_pending("x")
    store.add_pending("y")
    assert store.take_pending() == ["x", "y"]
    assert store.take_pending() == []


def test_one_open_challenge_query(store):
    assert store.open_challenge() is None
    r = store.add_assignment("rehearsal", "r", "2026-11-09")
    c = store.add_assignment("challenge", "c", "2026-11-10")
    assert store.open_challenge().id == c
    assert {a.id for a in store.open_assignments()} == {r, c}
    store.close_assignment(c, "done")
    assert store.open_challenge() is None
    assert [a.id for a in store.open_assignments()] == [r]


def test_checkins_counts(store):
    store.add_checkin("r", "t", True)
    store.add_checkin("r", "t", False)
    n = datetime.now().astimezone()
    assert store.checkins_today(n) == 1
    assert store.unreplied_streak() == 1
    store.add_checkin("r", "t2", True)
    assert store.unreplied_streak() == 2
    store.mark_last_checkin_replied()
    assert store.unreplied_streak() == 0
    store.add_checkin("r", "t3", True)
    assert store.unreplied_streak() == 1


def test_signals_and_intent(store):
    old = NOW - timedelta(days=10)
    store.add_signal(7, "late", "d", old)
    store.add_signal(0, "intent", "ask Ann", NOW)
    rows = store.signals_since(NOW - timedelta(days=7))
    assert len(rows) == 1 and rows[0]["kind"] == "intent" and rows[0]["chat_id"] == 0


def test_text_msgs_and_cursor(store):
    store.add_text_msgs([(1, 10, NOW.isoformat(), 0, "hey"), (1, 11, NOW.isoformat(), 1, "yo")])
    assert len(store.text_msgs(1)) == 2
    assert len(store.text_msgs(1, limit=1)) == 1
    assert store.text_msgs(1, since=NOW + timedelta(hours=1)) == []
    assert store.cursor(1) == 0
    store.set_cursor(1, 11)
    assert store.cursor(1) == 11


def test_corrections_journal_meta(store):
    store.add_correction("not that")
    store.add_journal("went ok")
    assert store.corrections() == ["not that"]
    assert store.get_meta("k") is None
    store.set_meta("k", "v")
    store.set_meta("k", "w")
    assert store.get_meta("k") == "w"


def _fill(store, at):
    store.add_turn("user", "u")
    store.add_pending("p")
    store.add_assignment("challenge", "c", "d")
    store.add_checkin("r", "t", True)
    store.add_signal(1, "k", "d", at)
    store.add_text_msgs([(1, 1, at.isoformat(), 0, "t")])
    store.set_cursor(1, 1)
    store.add_correction("c")
    store.add_journal("j")
    store.add_entry("blot", "fill", "ask")
    store.set_meta("k", "v")


def test_forget_all_empties_every_table(store):
    _fill(store, NOW)
    assert all(v > 0 for v in store.stats().values())
    store.forget("all", NOW)
    assert all(v == 0 for v in store.stats().values())


def test_forget_last_removes_latest_turn_pair(store):
    store.add_turn("user", "1")
    store.add_turn("coach", "2")
    store.add_turn("user", "3")
    store.add_turn("coach", "4")
    store.forget("last", NOW)
    assert [t.text for t in store.recent_turns(10)] == ["1", "2"]


def test_forget_day_removes_today_only(store):
    yesterday = NOW - timedelta(days=1)
    store._conn.execute(
        "INSERT INTO turns(at,role,text,state) VALUES (?,?,?,?)",
        (yesterday.isoformat(), "user", "old", "ok"),
    )
    store._conn.execute(
        "INSERT INTO turns(at,role,text,state) VALUES (?,?,?,?)",
        (NOW.isoformat(), "user", "new", "ok"),
    )
    store.add_signal(1, "k", "d", yesterday)
    store.add_signal(1, "k", "d", NOW)
    store.forget("day", NOW)
    assert [t.text for t in store.recent_turns(10)] == ["old"]
    assert store.stats()["signals"] == 1


def test_rows_are_stamped_by_the_store_clock_so_forget_day_agrees(home):
    """Rows and forget("day") must read the same clock: a store stamping with the wall
    clock while the caller's `now` is another day leaves 'today' undeleted."""
    s = Store(paths.db_path(), clock=lambda: NOW)
    s.add_turn("user", "a")
    s.add_pending("p")
    s.add_journal("j")
    assert s.recent_turns(1)[0].at == NOW.isoformat()
    s.forget("day", NOW)
    assert s.stats()["turns"] == s.stats()["pending"] == s.stats()["journal"] == 0


def test_forget_day_uses_local_calendar_day_across_midnight(home):
    late = datetime(2026, 11, 7, 23, 30).astimezone()
    s = Store(paths.db_path(), clock=lambda: late)
    s.add_turn("user", "last night")
    s.forget("day", late + timedelta(hours=1))  # 00:30 the next local day
    assert s.stats()["turns"] == 1
    s.forget("day", late)
    assert s.stats()["turns"] == 0


def test_store_clock_is_aware_local_time_by_default(store):
    store.add_turn("user", "a")
    at = datetime.fromisoformat(store.recent_turns(1)[0].at)
    assert at.tzinfo is not None
    assert at.utcoffset() == datetime.now().astimezone().utcoffset()


def test_peek_and_delete_pending(store):
    store.add_pending("x")
    store.add_pending("y")
    rows = store.peek_pending()
    assert [t for _, t in rows] == ["x", "y"]
    store.add_pending("z")
    store.delete_pending(rows[-1][0])
    assert [t for _, t in store.peek_pending()] == ["z"]


def test_atomic_rolls_back_on_any_exception(store):
    store.add_pending("keep me")
    try:
        with store.atomic():
            store.add_turn("user", "half-written")
            store.delete_pending(store.peek_pending()[-1][0])
            raise KeyboardInterrupt
    except KeyboardInterrupt:
        pass
    assert store.recent_turns(5) == []
    assert [t for _, t in store.peek_pending()] == ["keep me"]
    with store.atomic():
        store.add_turn("user", "whole")
    assert [t.text for t in store.recent_turns(5)] == ["whole"]


def test_forget_all_leaves_no_trace_in_db_or_wal(tmp_path):
    p = tmp_path / "x.db"
    s = Store(p)
    marker = "zebra-quokka-7731-distinctive"
    s._x("INSERT INTO journal(at, text) VALUES(?,?)", ("2026-01-01T00:00:00+00:00", marker))
    raw = b"".join(f.read_bytes() for f in tmp_path.glob("x.db*"))
    assert marker.encode() in raw
    s.forget("all", NOW)
    for f in tmp_path.glob("x.db*"):
        assert marker.encode() not in f.read_bytes()


def test_open_tightens_existing_sidecars(home):
    import stat
    db = paths.db_path()
    Store(db)
    for suffix in ("-wal", "-shm"):
        side = db.with_name(db.name + suffix)
        side.touch()
        side.chmod(0o644)
    Store(db)
    for suffix in ("", "-wal", "-shm"):
        side = db.with_name(db.name + suffix)
        assert stat.S_IMODE(side.stat().st_mode) == 0o600, suffix


def test_agent_and_setup_create_files_private(home, monkeypatch):
    import os
    import stat
    from tilia import __main__ as cli
    from tilia import setup_wizard
    masks = []
    monkeypatch.setattr(os, "umask", lambda m: masks.append(m) or 0o022)
    assert cli.main(["status"]) == 0
    assert masks == [0o077]
    tmp = paths.data_dir() / "tmp"
    assert stat.S_IMODE(tmp.stat().st_mode) == 0o700  # the plist's TMPDIR exists, private
    assert setup_wizard.main(["no-such-step"]) == 2
    assert masks == [0o077, 0o077]


# ---- entries ----------------------------------------------------------------------

def test_entry_roundtrip(store):
    store.add_entry("check_story", "tok", "ask", wording={"pitch": "x"})
    e = store.entry_by_token("tok")
    assert e.status == "open" and e.wording == {"pitch": "x"} and e.themes == []
    assert store.get_entry(e.id).token == "tok"
    assert store.entry_by_token("nope") is None


def test_sealed_read_written_once(store):
    i = store.add_entry("blot", "t", "ask")
    assert store.set_sealed_read(i, "first") is True
    assert store.set_sealed_read(i, "second") is False
    assert store.get_entry(i).sealed_read == "first"


def test_expire_entries_drops_sealed_read(tmp_path):
    clock = {"now": NOW}
    s = Store(tmp_path / "t.db", clock=lambda: clock["now"])
    clock["now"] = NOW - timedelta(days=7, minutes=1)
    old = s.add_entry("blot", "old", "ask")
    s.set_sealed_read(old, "secret")
    clock["now"] = NOW - timedelta(days=6)
    fresh = s.add_entry("blot", "fresh", "ask")
    clock["now"] = NOW - timedelta(days=30)
    done = s.add_entry("blot", "done", "ask")
    s.answer_entry(done, [1], None, None)
    assert s.expire_entries(NOW, timedelta(days=7)) == 1
    assert s.get_entry(old).status == "expired" and s.get_entry(old).sealed_read is None
    assert s.get_entry(fresh).status == "open"
    assert s.get_entry(done).status == "answered"


def test_answer_entry_sets_outcome(store):
    a = store.add_entry("w", "a", "ask")
    store.answer_entry(a, [{"q": 1}], 3, 6)
    e = store.get_entry(a)
    assert e.status == "answered" and e.outcome == 3 and e.answers == [{"q": 1}]
    assert e.answered_at is not None and (e.mood_before, e.mood_after) == (3, 6)
    b = store.add_entry("w", "b", "ask")
    store.answer_entry(b, [], None, 6)
    assert store.get_entry(b).outcome is None


def test_finish_followup_and_listing(store):
    a = store.add_entry("w", "a", "ask")
    b = store.add_entry("blot", "b", "ask", status="answered")
    store.finish_entry(a, "reply", ["x", "y"], 5, followup_signal=1)
    store.set_entry_followup_at(a, "2026-11-08T10:00:00+00:00")
    e = store.get_entry(a)
    assert (e.status, e.reply, e.themes, e.turn_id, e.followup_signal) == \
        ("done", "reply", ["x", "y"], 5, 1)
    assert e.followup_at == "2026-11-08T10:00:00+00:00"
    assert [x.id for x in store.entries()] == [a, b]
    assert [x.id for x in store.entries(types=["blot"])] == [b]
    assert [x.id for x in store.entries(statuses=["done"])] == [a]


def test_add_signal_returns_id_and_last_turn_id(store):
    assert store.last_turn_id() is None
    assert isinstance(store.add_signal(1, "k", "d", NOW), int)
    store.add_turn("user", "1")
    store.add_turn("coach", "2")
    assert store.last_turn_id() == 2 and store.last_turn_id("user") == 1


def test_forget_covers_entries(store):
    yesterday = NOW - timedelta(days=1)
    ins = ("INSERT INTO entries(token,type,source,created,answered_at) "
           "VALUES (?,?,?,?,?)")
    store._conn.execute(ins, ("today", "w", "ask", NOW.isoformat(), None))
    store._conn.execute(ins, ("yday", "w", "ask", yesterday.isoformat(), None))
    store._conn.execute(ins, ("yans", "w", "ask", yesterday.isoformat(), NOW.isoformat()))
    store.forget("day", NOW)
    assert [e.token for e in store.entries()] == ["yday"]
    store.forget("all", NOW)
    assert store.entries() == [] and store.stats()["entries"] == 0
    store.add_turn("user", "q")
    keep = store.add_entry("w", "keep", "ask", turn_id=None)
    gone = store.add_entry("w", "gone", "ask", turn_id=store.last_turn_id("user"))
    store.forget("last", NOW)
    assert [e.id for e in store.entries()] == [keep]
    assert gone not in [e.id for e in store.entries()]


def test_data_dir_tells_spotlight_not_to_index_it(home):
    d = paths.data_dir()
    marker = d / ".metadata_never_index"
    assert marker.is_file() and not marker.is_symlink()
    assert stat.S_IMODE(marker.stat().st_mode) == 0o600
    marker.chmod(0o644)
    paths.data_dir()  # again: left in place, made private
    assert stat.S_IMODE(marker.stat().st_mode) == 0o600


def test_data_dir_starts_even_if_the_spotlight_marker_is_a_symlink(home, tmp_path, capsys,
                                                                    monkeypatch):
    monkeypatch.setattr(paths, "_warned_index", False)
    d = paths.data_dir()
    marker = d / ".metadata_never_index"
    marker.unlink()
    marker.symlink_to(tmp_path / "elsewhere")
    assert paths.data_dir() == d  # no crash
    assert ".metadata_never_index" in capsys.readouterr().err
    assert not (tmp_path / "elsewhere").exists()  # never followed
    paths.data_dir()
    assert capsys.readouterr().err == ""  # said once
