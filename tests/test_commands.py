import json
from datetime import datetime, timedelta

import pytest

from tilia import commands, coach, config, paths, profile
from tilia.config import Config
from tilia.store import Store

from tests.test_coach import fake, reply

NOW = datetime(2026, 11, 7, 12, 0).astimezone()


class FakeTG:
    def __init__(self):
        self.sent, self.docs = [], []

    def send(self, chat_id, text):
        self.sent.append(text)
        return True

    def send_document(self, chat_id, filename, data):
        self.docs.append((filename, data))
        return True


@pytest.fixture
def store(home):
    return Store(paths.db_path())


@pytest.fixture
def tg():
    return FakeTG()


def mkcfg(**kw):
    return Config(threads=[{"name": "Ann", "chat_id": 5550100001}], **kw)


def _no_real_claude(cmd, **kw):
    raise AssertionError("real claude called")


def h(text, store, cfg, tg, run=_no_real_claude):
    return commands.handle(text, store, cfg, NOW, tg, 1, run=run)


def sysp(call):
    return call[0][call[0].index("--system-prompt") + 1]


ROLE_RUN = fake(reply("hi. what's up"))


def queued(store):
    """Coach-written replies wait in the loop's outbox, never sent directly."""
    return [i["text"] for i in json.loads(store.get_meta("outbox") or "[]")]


def no_run(cmd, **kw):
    raise AssertionError("claude must not be called")


def test_plain_text_is_not_a_command(store, tg):
    assert h("hello", store, mkcfg(), tg, no_run) is False
    assert tg.sent == []


def test_privacy_lists_path_counts_threads_and_disclosure(store, tg, home):
    store.add_turn("user", "hi")
    assert h("/privacy", store, mkcfg(model="haiku"), tg, no_run)
    out = tg.sent[0]
    assert str(paths.data_dir()) in out
    assert "turns: 1" in out
    assert "Ann" in out and "haiku" in out
    low = out.lower()
    assert "telegram" in low and "end-to-end" in low and "anthropic" in low
    tg2 = FakeTG()
    h("/privacy", store, Config(), tg2, no_run)
    assert "none" in tg2.sent[0].lower()


def test_forget_all_requires_FORGET(store, tg):
    cfg = mkcfg()
    store.add_turn("user", "x")
    profile.write("notes about Ann")
    assert h("/forget all", store, cfg, tg, no_run)
    assert "type FORGET to confirm" in tg.sent[-1]
    assert store.get_meta("awaiting") == "forget_all"
    assert h("forget", store, cfg, tg, no_run) is False  # not exact: cancel line, then normal
    assert "nothing deleted" in tg.sent[-1].lower()
    assert store.stats()["turns"] == 1 and store.get_meta("awaiting") is None
    assert profile.read() == "notes about Ann"
    h("/forget all", store, cfg, tg, no_run)
    paths.crisis_log_path().write_text("x\n")
    assert h("FORGET", store, cfg, tg, no_run)
    assert h("y", store, cfg, tg, no_run)  # threads are set, so one more question
    assert not paths.crisis_log_path().exists()
    assert all(v == 0 for v in store.stats().values())
    assert profile.read() == ""
    assert not paths.profile_path().with_name("profile.prev.md").exists()


def test_forget_last_and_day_act_immediately(store, tg):
    store.add_turn("user", "a")
    store.add_turn("coach", "b")
    assert h("/forget last", store, mkcfg(), tg, no_run)
    assert store.stats()["turns"] == 0
    store.add_turn("user", "c")
    h("/forget day", store, mkcfg(), tg, no_run)
    assert store.stats()["turns"] == 0


def test_practice_done_cycle_opens_and_closes_record(store, tg):
    cfg = mkcfg()
    assert h("/practice", store, cfg, tg, no_run)
    assert "who, and what's the situation?" in tg.sent[-1]
    assert h("Ann, I want to tell her I need space", store, cfg, tg, ROLE_RUN)
    assert store.get_meta("mode") == "rehearsal"
    assert "Ann" in store.get_meta("rehearsal_role")
    aid = int(store.get_meta("rehearsal_assignment_id"))
    open_ = store.open_assignments()
    assert [a.id for a in open_] == [aid] and open_[0].kind == "rehearsal"
    # in rehearsal, plain text is not a command and the loop picks the mode
    assert h("hey Ann", store, cfg, tg, no_run) is False
    assert commands.current_mode(store) == "rehearsal"
    calls = []
    run = fake(reply("Worked: you were clear. Didn't: you apologized twice. Try: 'I need a day.'"),
               calls=calls)
    assert h("/done", store, cfg, tg, run)
    assert "Worked" in queued(store)[-1]
    assert not any("Worked" in t for t in tg.sent)
    assert "chat" == commands.current_mode(store)
    assert store.get_meta("rehearsal_assignment_id") is None
    now_open = store.open_assignments()
    assert aid not in [a.id for a in now_open]
    assert len(now_open) == 1 and "follow up" in now_open[0].text
    assert datetime.fromisoformat(now_open[0].due) == NOW + timedelta(days=3)
    assert "MODE: chat" in calls[0][1]["input"]  # the coach's call, not the role's
    assert "rehearsal" not in sysp(calls[0])[:200].lower()


def test_done_outside_rehearsal(store, tg):
    assert h("/done", store, mkcfg(), tg, no_run)
    assert "practice" in tg.sent[-1].lower()


def test_crisis_breaks_rehearsal(store, tg):
    cfg = mkcfg()
    h("/practice", store, cfg, tg, no_run)
    h("Ann, a hard talk", store, cfg, tg, ROLE_RUN)
    calls = []
    run = fake(reply("I'm stepping out of the role. I'm here with you.", state="crisis"),
               reply("still here", state="ok"), calls=calls)
    r = coach.respond(store, cfg, NOW, ["I don't want to be here"], run=run,
                      mode=commands.current_mode(store))
    assert r.state == "crisis"
    assert commands.current_mode(store) == "chat"
    assert store.get_meta("rehearsal_assignment_id") is None
    assert store.open_assignments() == []
    coach.respond(store, cfg, NOW, ["ok"], run=run, mode=commands.current_mode(store))
    assert "MODE: rehearsal" in calls[0][1]["input"]
    assert sysp(calls[0]) != sysp(calls[1])
    assert "MODE: chat" in calls[1][1]["input"]


def test_quieter_more_bounds(store, tg):
    cfg = mkcfg(checkin_cap=1)
    h("/quieter", store, cfg, tg, no_run)
    assert cfg.checkin_cap == 0
    h("/quieter", store, cfg, tg, no_run)
    assert cfg.checkin_cap == 0
    for _ in range(6):
        h("/more", store, cfg, tg, no_run)
    assert cfg.checkin_cap == 4
    assert config.load().checkin_cap == 4


def test_pause_sets_until(store, tg):
    cfg = mkcfg()
    h("/pause", store, cfg, tg, no_run)
    assert datetime.fromisoformat(cfg.paused_until) == NOW + timedelta(hours=24)
    h("/pause 3", store, cfg, tg, no_run)
    assert datetime.fromisoformat(cfg.paused_until) == NOW + timedelta(hours=3)
    assert config.load().paused_until == cfg.paused_until
    h("/pause soon", store, cfg, tg, no_run)
    assert datetime.fromisoformat(cfg.paused_until) == NOW + timedelta(hours=3)


def test_export_contains_all_sections(store, tg):
    store.add_turn("user", "turn-one")
    store.add_journal("journal-one")
    store.add_assignment("challenge", "assign-one", None)
    profile.write("profile-one")
    assert h("/export", store, mkcfg(), tg, no_run)
    name, data = tg.docs[0]
    body = data.decode()
    assert name.endswith(".md")
    for s in ["turn-one", "journal-one", "assign-one", "profile-one",
              "## Conversation", "## Journal", "## Assignments", "## Profile"]:
        assert s in body


def test_journal_stores_and_reflects(store, tg):
    cfg = mkcfg()
    h("/journal", store, cfg, tg, no_run)
    calls = []
    assert h("rough day", store, cfg, tg, fake(reply("That sounds heavy."), calls=calls))
    assert queued(store) == ["That sounds heavy."]
    assert "That sounds heavy." not in tg.sent
    assert store.stats()["journal"] == 1
    assert "MODE: journal" in calls[0][1]["input"]
    assert h("later", store, cfg, tg, no_run) is False


def test_profile_short_and_long(store, tg):
    h("/profile", store, mkcfg(), tg, no_run)
    assert "nothing" in tg.sent[-1].lower()
    profile.write("x" * 5000)
    h("/profile", store, mkcfg(), tg, no_run)
    assert tg.docs and tg.docs[0][0] == "profile.md"


def test_help_lists_commands(store, tg):
    h("/help", store, mkcfg(), tg, no_run)
    for c in ["/privacy", "/practice", "/forget", "/export", "/pause"]:
        assert c in tg.sent[-1]


def test_practice_answer_with_crisis_reaches_coach(store, tg):
    cfg = mkcfg()
    h("/practice", store, cfg, tg, no_run)
    calls = []
    run = fake(reply("I'm stepping out of the role. I'm here.", state="crisis"), calls=calls)
    assert h("I want to end my life", store, cfg, tg, run)
    assert "I want to end my life" in calls[0][1]["input"]
    assert commands.current_mode(store) == "chat"
    assert store.open_assignments() == []
    assert "988" in queued(store)[-1]
    assert not any("988" in t for t in tg.sent)


def test_practice_twice_leaves_one_open_rehearsal(store, tg):
    cfg = mkcfg()
    h("/practice", store, cfg, tg, no_run)
    h("Ann, first", store, cfg, tg, ROLE_RUN)
    first = int(store.get_meta("rehearsal_assignment_id"))
    h("/practice", store, cfg, tg, no_run)
    h("Bob, second", store, cfg, tg, ROLE_RUN)
    open_ = store.open_assignments()
    assert len(open_) == 1 and open_[0].id != first and "Bob" in open_[0].text
    old = [a for a in store.all_assignments() if a.id == first][0]
    assert old.status == "dropped"


def test_awaiting_expires_after_30_minutes(store, tg):
    cfg = mkcfg()
    h("/journal", store, cfg, tg, no_run)
    later = NOW + timedelta(minutes=31)
    assert commands.handle("rough day", store, cfg, later, tg, 1, run=no_run) is False
    assert store.get_meta("awaiting") is None and store.stats()["journal"] == 0
    h("/journal", store, cfg, tg, no_run)
    ok = NOW + timedelta(minutes=29)
    assert commands.handle("fine", store, cfg, ok, tg, 1, run=fake(reply("ok"))) is True
    assert store.stats()["journal"] == 1


def test_slash_command_while_awaiting_runs_and_clears(store, tg):
    cfg = mkcfg(checkin_cap=2)
    h("/journal", store, cfg, tg, no_run)
    assert h("/quieter", store, cfg, tg, no_run)
    assert cfg.checkin_cap == 1
    assert store.get_meta("awaiting") is None
    assert h("plain", store, cfg, tg, no_run) is False


def test_practice_opener_is_queued_not_sent(store, tg):
    cfg = mkcfg()
    h("/practice", store, cfg, tg, no_run)
    n = len(tg.sent)
    h("Ann, I want to tell her I need space", store, cfg, tg, ROLE_RUN)
    assert queued(store) == ["hi. what's up"]
    assert len(tg.sent) == n


def test_privacy_says_when_texts_are_not_readable(store, tg):
    h("/privacy", store, mkcfg(), tg, no_run)
    assert "not readable right now" not in tg.sent[-1]
    store.set_meta("texts_unavailable", "1")
    h("/privacy", store, mkcfg(), tg, no_run)
    assert "Text threads I read: Ann (not readable right now)" in tg.sent[-1]


KEEP_Q = "Keep reading your texts from now on? y/n"


def test_forget_all_asks_about_texts_and_n_stops_reading(store, tg):
    cfg = mkcfg()
    config.save(cfg)
    store.add_turn("user", "x")
    h("/forget all", store, cfg, tg, no_run)
    assert "copied again from scratch" in tg.sent[-1]
    assert h("FORGET", store, cfg, tg, no_run)
    assert tg.sent[-1] == KEEP_Q
    assert store.stats()["turns"] == 1  # nothing deleted yet
    assert h("n", store, cfg, tg, no_run)
    assert cfg.threads == [] and config.load().threads == []
    assert all(v == 0 for v in store.stats().values())
    assert "stopped reading your texts" in tg.sent[-1]
    assert "./setup texts" in tg.sent[-1]


def test_forget_all_y_keeps_threads_and_says_they_are_copied_again(store, tg):
    cfg = mkcfg()
    config.save(cfg)
    store.add_turn("user", "x")
    h("/forget all", store, cfg, tg, no_run)
    h("FORGET", store, cfg, tg, no_run)
    assert h("yes", store, cfg, tg, no_run)
    assert config.load().threads == [{"name": "Ann", "chat_id": 5550100001}]
    assert all(v == 0 for v in store.stats().values())
    assert "copied again from scratch" in tg.sent[-1]


def test_forget_all_texts_question_other_answer_cancels(store, tg):
    cfg = mkcfg()
    store.add_turn("user", "x")
    h("/forget all", store, cfg, tg, no_run)
    h("FORGET", store, cfg, tg, no_run)
    assert h("hmm, wait", store, cfg, tg, no_run) is False
    assert "nothing deleted" in tg.sent[-1].lower()
    assert store.stats()["turns"] == 1 and cfg.threads


def test_forget_all_without_threads_asks_nothing_more(store, tg):
    cfg = Config()
    store.add_turn("user", "x")
    h("/forget all", store, cfg, tg, no_run)
    assert h("FORGET", store, cfg, tg, no_run)
    assert KEEP_Q not in tg.sent
    assert all(v == 0 for v in store.stats().values())


def test_privacy_shows_the_folder_without_the_username(tmp_path, monkeypatch, tg):
    fake_home = tmp_path / "Users" / "jordan"
    fake_home.mkdir(parents=True)
    monkeypatch.setenv("HOME", str(fake_home))
    monkeypatch.delenv("TILIA_HOME")
    store = Store(paths.db_path())
    assert h("/privacy", store, mkcfg(), tg, no_run)
    out = tg.sent[0]
    assert "in ~/Library/Application Support/Tilia\n" in out
    assert "jordan" not in out and str(tmp_path) not in out
