import json
from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest

from tilia import coach, entries, paths, power, worksheets
from tilia.config import Config
from tilia.loop import Loop
from tilia.store import Store
from tilia.telegram import Update

NOW = datetime(2026, 11, 7, 12, 0).astimezone()
PAIRED = 5550100042
STRANGER = 5550100099
UNSUPPORTED = "I can only read text for now, but I'm here."
DOWN = "I can't reach Claude. Open Terminal and run `claude` to log in."
SUMMARY_SYS = (paths.REPO_ROOT / "prompts" / "summarize.md").read_text(encoding="utf-8")
COACH_SYS = {coach.system_prompt(m) for m in ("chat", "journal", "rehearsal")}


class Clock:
    def __init__(self, t=NOW):
        self.t = t

    def __call__(self):
        return self.t

    def advance(self, seconds):
        self.t += timedelta(seconds=seconds)


class FakeTG:
    def __init__(self):
        self.queue: list[Update] = []
        self.sent: list[tuple[int, str]] = []
        self.offsets = []
        self.events: list[str] = []  # "typing" / "send", in order
        self.marks: list = []  # the markup each send carried, in order
        self._id = 1000

    def _push(self, chat_id, text, kind):
        self._id += 1
        self.queue.append(Update(self._id, chat_id, text, kind))

    def text(self, text, chat_id=PAIRED):
        self._push(chat_id, text, "text")

    def photo(self, chat_id=PAIRED):
        self._push(chat_id, None, "unsupported")

    def web_app(self, data, chat_id=PAIRED):
        self._id += 1
        self.queue.append(Update(self._id, chat_id, None, "web_app_data", web_app_data=data))

    def updates(self, offset):
        self.offsets.append(offset)
        return [u for u in self.queue if offset is None or u.update_id >= offset]

    def send(self, chat_id, text, markup=None):
        self.sent.append((chat_id, text))
        self.marks.append(markup)
        self.events.append("send")
        return True

    def typing(self, chat_id):
        self.events.append("typing")
        return True

    def typings(self):
        return self.events.count("typing")

    def send_document(self, chat_id, filename, data):
        self.sent.append((chat_id, f"[doc {filename}]"))
        return True

    def texts(self):
        return [t for _, t in self.sent]


class FakeClaude:
    """subprocess.run stand-in for `claude -p`. Records (system, prompt) per call."""

    def __init__(self, reply="I'm here. Tell me more."):
        self.reply = reply
        self.calls: list[tuple[str, str]] = []
        self.fail = None  # None | "limit" | "limit_nohint" | "auth" | "missing" | "boom"
        self.extra: dict = {}  # more fields for the coach's JSON answer

    def __call__(self, cmd, **kw):
        system = cmd[cmd.index("--system-prompt") + 1]
        self.calls.append((system, kw["input"]))
        if self.fail == "missing":
            raise FileNotFoundError("claude")
        err = {"limit": "You've hit your usage limit · resets 3pm",
               "limit_nohint": "Claude usage limit reached",
               "auth": "Invalid API key · Please run /login",
               "boom": "something odd happened"}.get(self.fail)
        if err:
            return SimpleNamespace(stdout=json.dumps({"is_error": True, "result": err}),
                                   stderr="", returncode=1)
        if system == SUMMARY_SYS:
            out = "# Notes\nfake profile"
        else:
            out = json.dumps({"reply": self.reply, "state": "ok", **self.extra})
        return SimpleNamespace(stdout=json.dumps({"result": out, "is_error": False}),
                               stderr="", returncode=0)

    def coach_calls(self):
        return [p for s, p in self.calls if s in COACH_SYS]

    def summary_calls(self):
        return [p for s, p in self.calls if s == SUMMARY_SYS]


@pytest.fixture
def store(home):
    return Store(paths.db_path())


@pytest.fixture
def tg():
    return FakeTG()


@pytest.fixture
def claude():
    return FakeClaude()


@pytest.fixture
def clock():
    return Clock()


def mkloop(store, tg, clock, claude, **kw):
    kw.setdefault("cards", False)  # a weekly card would otherwise check in on an idle tick
    cfg = Config(chat_id=PAIRED, **kw)
    return Loop(cfg, store, tg, clock, claude, chatdb_opener=lambda: None)


def pending(store):
    return store.stats()["pending"]


def test_unpaired_chat_ignored_and_counted(store, tg, clock, claude):
    loop = mkloop(store, tg, clock, claude)
    tg.text("hello who is this", chat_id=STRANGER)
    tg.text("/privacy", chat_id=STRANGER)
    tg.photo(chat_id=STRANGER)
    loop.tick()
    clock.advance(60)
    loop.tick()
    assert tg.sent == []
    assert claude.calls == []
    assert pending(store) == 0
    assert store.get_meta("ignored_chats") == "3"


def test_batching_merges_three_rapid_messages(store, tg, clock, claude):
    loop = mkloop(store, tg, clock, claude)
    for msg in ["ugh", "Riley went quiet again", "and I already texted twice"]:
        tg.text(msg)
        loop.tick()
        clock.advance(3)
    clock.advance(4)  # 7 s since the last message: still waiting
    loop.tick()
    assert claude.coach_calls() == []
    clock.advance(2)
    loop.tick()
    calls = claude.coach_calls()
    assert len(calls) == 1
    p = calls[0]
    assert p.index("ugh") < p.index("Riley went quiet again") < p.index("and I already texted")
    assert tg.texts() == ["I'm here. Tell me more."]
    assert pending(store) == 0
    clock.advance(30)
    loop.tick()
    assert len(claude.coach_calls()) == 1


def test_quiet_window_is_eight_seconds(store, tg, clock, claude):
    from tilia import loop as loop_mod
    assert loop_mod.QUIET == timedelta(seconds=8)
    loop = mkloop(store, tg, clock, claude)
    tg.text("hey")
    loop.tick()
    clock.advance(7)
    loop.tick()
    assert claude.coach_calls() == []
    clock.advance(1)
    loop.tick()
    assert len(claude.coach_calls()) == 1


def test_wake_backlog_one_reply(store, tg, clock, claude):
    loop = mkloop(store, tg, clock, claude)
    msgs = [f"backlog message {i:02d}" for i in range(12)]
    for m in msgs[:6]:
        tg.text(m)
    tg.text("/privacy")
    for m in msgs[6:]:
        tg.text(m)
    loop.tick()
    assert len(tg.sent) == 1 and "Everything lives on this Mac" in tg.sent[0][1]
    assert claude.coach_calls() == []
    clock.advance(21)
    loop.tick()
    calls = claude.coach_calls()
    assert len(calls) == 1
    assert all(m in calls[0] for m in msgs)
    assert "/privacy" not in calls[0]
    assert len(tg.sent) == 2
    clock.advance(60)
    loop.tick()
    assert len(claude.coach_calls()) == 1


def test_offset_persists_so_restart_does_not_replay(store, tg, clock, claude):
    loop = mkloop(store, tg, clock, claude)
    tg.text("/help")
    loop.tick()
    assert len(tg.sent) == 1
    again = mkloop(store, tg, clock, claude)
    again.tick()
    assert len(tg.sent) == 1
    assert tg.offsets[-1] == tg.queue[-1].update_id + 1


def test_limit_error_queues_and_notifies_once(store, tg, clock, claude):
    loop = mkloop(store, tg, clock, claude)
    claude.fail = "limit"
    tg.text("first thing")
    loop.tick()
    clock.advance(21)
    loop.tick()
    assert tg.texts() == ["I'm at my usage limit (resets 3pm). I'll read everything when I'm back."]
    assert pending(store) == 1
    tg.text("second thing")
    clock.advance(30)
    loop.tick()
    clock.advance(30)
    loop.tick()
    assert len(claude.coach_calls()) == 1  # waits 10 min before retrying
    assert pending(store) == 2
    clock.advance(10 * 60)
    loop.tick()
    assert len(claude.coach_calls()) == 2
    assert len(tg.sent) == 1  # still one notice
    claude.fail = None
    clock.advance(10 * 60)
    loop.tick()
    calls = claude.coach_calls()
    assert len(calls) == 3
    assert calls[-1].index("first thing") < calls[-1].index("second thing")
    assert tg.texts()[-1] == "I'm here. Tell me more."
    assert pending(store) == 0
    # a new episode notifies again
    claude.fail = "limit_nohint"
    tg.text("third thing")
    loop.tick()
    clock.advance(21)
    loop.tick()
    assert tg.texts()[-1] == "I'm at my usage limit. I'll read everything when I'm back."
    assert pending(store) == 1


@pytest.mark.parametrize("fail", ["auth", "missing"])
def test_claude_down_queues_and_notifies_once(store, tg, clock, claude, fail):
    loop = mkloop(store, tg, clock, claude)
    claude.fail = fail
    tg.text("are you there")
    loop.tick()
    clock.advance(21)
    loop.tick()
    assert tg.texts() == [DOWN]
    assert pending(store) == 1
    clock.advance(10 * 60)
    loop.tick()
    assert len(claude.coach_calls()) == 2
    assert tg.texts() == [DOWN]
    claude.fail = None
    clock.advance(10 * 60)
    loop.tick()
    assert tg.texts() == [DOWN, "I'm here. Tell me more."]
    assert pending(store) == 0


def test_unexpected_error_keeps_text_and_never_crashes(store, tg, clock, claude):
    loop = mkloop(store, tg, clock, claude)
    claude.fail = "boom"
    tg.text("still here")
    loop.tick()
    clock.advance(21)
    loop.tick()  # must not raise
    assert pending(store) == 1
    assert tg.sent == []
    claude.fail = None
    clock.advance(120)
    loop.tick()
    assert tg.texts() == ["I'm here. Tell me more."]
    assert pending(store) == 0


def test_tick_survives_a_broken_telegram(store, clock, claude):
    class Broken(FakeTG):
        def updates(self, offset):
            raise ValueError("bad")
    loop = mkloop(store, Broken(), clock, claude)
    loop.tick()  # logged, not raised


def test_awaited_text_kept_when_claude_fails(store, tg, clock, claude):
    loop = mkloop(store, tg, clock, claude)
    tg.text("/journal")
    loop.tick()
    claude.fail = "limit"
    tg.text("today I said the hard thing")
    clock.advance(2)
    loop.tick()
    assert pending(store) == 1
    assert any("usage limit" in t for t in tg.texts())


def test_unsupported_message_gets_kind_reply(store, tg, clock, claude):
    loop = mkloop(store, tg, clock, claude)
    tg.photo()
    loop.tick()
    assert tg.texts() == [UNSUPPORTED]
    assert claude.calls == []
    assert pending(store) == 0


def test_any_text_marks_last_checkin_replied(store, tg, clock, claude):
    store.add_checkin("signal", "How did the call with Riley go?", True)
    loop = mkloop(store, tg, clock, claude)
    tg.text("ok it went fine")
    loop.tick()
    assert store.recent_checkins(1)[0]["replied"] == 1


def test_rehearsal_mode_passed_to_coach(store, tg, clock, claude, monkeypatch):
    seen = []
    real = coach.respond

    def spy(*a, **kw):
        seen.append(kw.get("mode"))
        return real(*a, **kw)
    monkeypatch.setattr(coach, "respond", spy)
    store.set_meta("mode", "rehearsal")
    store.set_meta("rehearsal_role", "Riley, after a missed call")
    loop = mkloop(store, tg, clock, claude)
    tg.text("hey, can we talk?")
    loop.tick()
    clock.advance(21)
    loop.tick()
    assert seen == ["rehearsal"]


def test_summarize_once_per_idle_period(store, tg, clock, claude):
    loop = mkloop(store, tg, clock, claude)
    tg.text("talked to Riley tonight")
    loop.tick()
    clock.advance(21)
    loop.tick()
    assert len(claude.coach_calls()) == 1
    clock.advance(29 * 60)
    loop.tick()
    assert claude.summary_calls() == []
    clock.advance(2 * 60)
    loop.tick()
    assert len(claude.summary_calls()) == 1
    for _ in range(3):
        clock.advance(20 * 60)
        loop.tick()
    assert len(claude.summary_calls()) == 1
    tg.text("one more thing")
    loop.tick()
    clock.advance(21)
    loop.tick()
    clock.advance(31 * 60)
    loop.tick()
    assert len(claude.summary_calls()) == 2


# ---- power -----------------------------------------------------------------

class FakeProc:
    def __init__(self, args):
        self.args = args
        self.alive = True

    def poll(self):
        return None if self.alive else 0

    def terminate(self):
        self.alive = False

    def wait(self, timeout=None):
        return 0


class FakePopen:
    def __init__(self):
        self.procs: list[FakeProc] = []

    def __call__(self, args, **kw):
        p = FakeProc(args)
        self.procs.append(p)
        return p


def test_power_never_uses_dash_d_and_respects_battery_setting():
    popen = FakePopen()
    p = power.Power(False, popen=popen, pid=4321)
    p.update(on_ac=True)
    p.update(on_ac=True)
    assert len(popen.procs) == 1
    assert popen.procs[0].args == ["caffeinate", "-i", "-w", "4321"]
    p.update(on_ac=False)
    assert not popen.procs[0].alive  # battery, setting off: let the Mac sleep
    p.update(on_ac=False)
    assert len(popen.procs) == 1

    popen2 = FakePopen()
    q = power.Power(True, popen=popen2, pid=4321)
    q.update(on_ac=False)
    assert len(popen2.procs) == 1 and popen2.procs[0].alive  # battery, setting on
    popen2.procs[0].alive = False  # caffeinate died: restart it
    q.update(on_ac=False)
    assert len(popen2.procs) == 2
    for proc in popen.procs + popen2.procs:
        assert "-d" not in proc.args and "-s" not in proc.args


def test_on_ac_parses_pmset():
    def run(out):
        return lambda *a, **k: SimpleNamespace(stdout=out, returncode=0)
    assert power.on_ac(run("Now drawing from 'AC Power'\n -InternalBattery-0 80%")) is True
    assert power.on_ac(run("Now drawing from 'Battery Power'\n -InternalBattery-0 80%")) is False

    def broken(*a, **k):
        raise FileNotFoundError("pmset")
    assert power.on_ac(broken) is False


def test_failed_send_is_retried_in_order(store, tg, clock, claude):
    loop = mkloop(store, tg, clock, claude)
    real_send, down = tg.send, {"on": True}

    def flaky(chat_id, text):
        return False if down["on"] else real_send(chat_id, text)
    tg.send = flaky
    tg.text("you there?")
    loop.tick()
    clock.advance(21)
    loop.tick()
    assert tg.sent == [] and len(claude.coach_calls()) == 1
    down["on"] = False
    clock.advance(31)
    loop.tick()
    assert tg.texts() == ["I'm here. Tell me more."]
    clock.advance(31)
    loop.tick()
    assert len(tg.sent) == 1 and len(claude.coach_calls()) == 1


def test_pending_from_before_a_restart_is_answered(store, tg, clock, claude):
    store.add_pending("sent while you were restarting")
    loop = mkloop(store, tg, clock, claude)
    loop.tick()
    assert claude.coach_calls() == []
    clock.advance(21)
    loop.tick()
    assert len(claude.coach_calls()) == 1 and pending(store) == 0


def test_status_prints_counts_not_content(store, home, capsys):
    from tilia import __main__ as cli
    from tilia import config
    config.save(Config(chat_id=PAIRED, model="opus", threads=[{"name": "Riley", "chat_id": 7}]))
    store.add_pending("zebrawords one")
    store.add_turn("user", "zebrawords two")
    assert cli.main(["status"]) == 0
    out = capsys.readouterr().out
    assert str(paths.data_dir()) in out
    assert "paired: yes" in out and "model: opus" in out
    assert "threads: 1" in out and "pending: 1" in out and "last turn: 20" in out
    assert "zebrawords" not in out and "Riley" not in out and str(PAIRED) not in out


def test_import_profile_cli(home, tmp_path, capsys):
    from tilia import __main__ as cli
    from tilia import profile
    src = tmp_path / "seed.md"
    src.write_text("# Sam\nlikes long walks\n", encoding="utf-8")
    assert cli.main(["import-profile", str(src)]) == 0
    assert "likes long walks" in profile.read()
    assert cli.main(["import-profile", str(tmp_path / "missing.md")]) == 1


def test_plist_template_renders_valid_plist():
    import plistlib
    tpl = (paths.REPO_ROOT / "launchd" / "com.tilia.agent.plist.template").read_text()
    filled = (tpl.replace("__PYTHON__", "/r/.venv/bin/python").replace("__REPO__", "/r")
              .replace("__DATA__", "/d/Application Support/Tilia").replace("__PATH__", "/usr/bin:/bin"))
    assert "__" not in filled.replace("__main__", "")
    p = plistlib.loads(filled.encode())
    assert p["Label"] == "com.tilia.agent"
    assert p["ProgramArguments"] == ["/r/.venv/bin/python", "-m", "tilia", "run"]
    assert p["KeepAlive"] is True and p["RunAtLoad"] is True
    assert p["StandardOutPath"] == p["StandardErrorPath"] == "/d/Application Support/Tilia/agent.log"
    assert p["EnvironmentVariables"]["TILIA_HOME"] == "/d/Application Support/Tilia"
    # claude never updates itself mid-session from inside Tilia's calls
    assert p["EnvironmentVariables"]["DISABLE_AUTOUPDATER"] == "1"
    # never the shared /tmp: a private temp folder inside the data folder
    assert p["EnvironmentVariables"]["TMPDIR"] == "/d/Application Support/Tilia/tmp"


@pytest.mark.parametrize("exc", [KeyboardInterrupt, SystemExit])
def test_death_mid_reply_keeps_the_words(store, tg, clock, claude, monkeypatch, exc):
    """The process dying inside respond (SIGTERM at logout, a kill) must leave the batch
    in pending and no half-written turns: a restart answers it."""
    real = coach.respond

    def dies(store_, *a, **kw):
        store_.add_turn("user", "half-written")
        raise exc()
    monkeypatch.setattr(coach, "respond", dies)
    loop = mkloop(store, tg, clock, claude)
    tg.text("please don't lose this")
    loop.tick()
    clock.advance(21)
    with pytest.raises(exc):
        loop.tick()
    assert [t for _, t in store.peek_pending()] == ["please don't lose this"]
    assert store.recent_turns(5) == []
    monkeypatch.setattr(coach, "respond", real)
    again = mkloop(store, tg, clock, claude)  # restart
    again.tick()
    clock.advance(21)
    again.tick()
    assert tg.texts() == ["I'm here. Tell me more."]
    assert pending(store) == 0


def test_reply_clears_pending_once_and_no_duplicates(store, tg, clock, claude):
    loop = mkloop(store, tg, clock, claude)
    tg.text("one")
    tg.text("two")
    loop.tick()
    clock.advance(21)
    loop.tick()
    assert pending(store) == 0
    assert [t.text for t in store.recent_turns(10)] == ["one", "two", "I'm here. Tell me more."]
    for _ in range(3):
        clock.advance(60)
        loop.tick()
    assert len(claude.coach_calls()) == 1 and len(tg.sent) == 1
    assert store.get_meta("outbox") is None


def test_death_between_reply_and_send_still_delivers(store, tg, clock, claude):
    loop = mkloop(store, tg, clock, claude)
    real_send = tg.send

    def dies(chat_id, text):
        raise KeyboardInterrupt
    tg.send = dies
    tg.text("hello")
    loop.tick()
    clock.advance(21)
    with pytest.raises(KeyboardInterrupt):
        loop.tick()
    assert pending(store) == 0  # answered: the reply is safe in the outbox
    tg.send = real_send
    again = mkloop(store, tg, clock, claude)
    again.tick()
    assert tg.texts() == ["I'm here. Tell me more."]
    assert len(claude.coach_calls()) == 1


# ---- final fix: a first crisis on a double-JSON-failure turn ----------------------

def test_first_crisis_on_double_json_failure_is_tracked_end_to_end(store, tg, clock, home):
    from tilia import checkins
    state_sys = (paths.REPO_ROOT / "prompts" / "state.md").read_text(encoding="utf-8")
    seen = []

    def run(cmd, **kw):
        system = cmd[cmd.index("--system-prompt") + 1]
        seen.append(system)
        if system == state_sys:
            out = json.dumps({"state": "crisis"})
        elif system in COACH_SYS:
            out = "I'm right here with you. Are you safe right now?"  # prose, not JSON
        else:
            raise AssertionError("unexpected claude call")
        return SimpleNamespace(stdout=json.dumps({"result": out, "is_error": False}),
                               stderr="", returncode=0)

    cfg = Config(chat_id=PAIRED, quiet_start=0, quiet_end=0)
    loop = Loop(cfg, store, tg, clock, run, chatdb_opener=lambda: None)
    loop.next_slow = clock() + timedelta(days=1)  # keep check-ins out of this tick
    tg.text("I've decided how I'm going to end it")
    loop.tick()
    clock.advance(25)
    loop.tick()
    assert seen.count(state_sys) == 1
    sent = tg.texts()
    assert len(sent) == 1
    assert sent[0].count("988") == 1
    assert sent[0].endswith("Are you safe right now?")
    assert coach._episode(store, clock())[0] is True
    log = paths.crisis_log_path().read_text().splitlines()
    assert len(log) == 1 and datetime.fromisoformat(log[0])
    # check-ins stay silent: a due challenge would otherwise be a candidate
    store.add_assignment("challenge", "send one line", clock().date().isoformat())
    later = clock() + timedelta(hours=2)
    assert checkins._blocked(store, cfg, later) is True
    assert checkins.maybe_send(store, cfg, later, run=run) is False
    assert seen.count(state_sys) == 1 and len(seen) == 3


# ---- final fix: coach-written messages always go through the outbox ---------------

class BlipTG(FakeTG):
    """Telegram that refuses the first `fails` sends."""

    def __init__(self, fails):
        super().__init__()
        self.fails = fails

    def send(self, chat_id, text):
        if self.fails:
            self.fails -= 1
            return False
        return super().send(chat_id, text)


def test_journal_reflection_survives_a_telegram_blip(store, clock, claude):
    tg = BlipTG(0)
    loop = mkloop(store, tg, clock, claude)
    tg.text("/journal")
    loop.tick()
    tg.fails = 1
    tg.text("rough day at work")
    loop.tick()
    assert claude.reply not in tg.texts()  # the blip ate the first try
    clock.advance(31)
    loop.tick()
    assert tg.texts().count(claude.reply) == 1
    assert store.get_meta("outbox") is None


def test_command_reply_sent_in_the_same_tick(store, tg, clock, claude):
    loop = mkloop(store, tg, clock, claude)
    tg.text("/journal")
    loop.tick()
    tg.text("rough day at work")
    loop.tick()
    assert tg.texts()[-1] == claude.reply


def test_checkin_delivered_through_the_outbox(store, clock, monkeypatch):
    from tilia import checkins, signals

    def fake_send(store_, cfg, now, run=None):
        from tilia import outbox
        outbox.enqueue(store_, "How did the talk with Riley go?", now)
        return True
    monkeypatch.setattr(checkins, "maybe_send", fake_send)
    monkeypatch.setattr(signals, "daily_pass", lambda *a, **k: None)
    tg = BlipTG(1)
    loop = mkloop(store, tg, clock, FakeClaude())
    loop.tick()
    assert tg.texts() == []
    clock.advance(31)
    loop.tick()
    assert tg.texts() == ["How did the talk with Riley go?"]


# ---- final fix: say so when texts can't be read -----------------------------------

TEXTS_OFF = ("I can't open Messages right now, so I'm not reading your texts. "
             "Run ./setup texts on your Mac to fix it.")


def test_texts_unreadable_notice_once_then_cleared(store, tg, clock, claude, tmp_path,
                                                   monkeypatch):
    import sqlite3
    from tests.fixtures.make_chatdb import make_chatdb
    from tilia import imessage
    make_chatdb(tmp_path / "chat.db").close()
    readable = {"ok": False}
    monkeypatch.setattr(imessage, "can_read", lambda *a: False)
    opener = lambda: sqlite3.connect(tmp_path / "chat.db") if readable["ok"] else None  # noqa: E731
    cfg = Config(chat_id=PAIRED, threads=[{"name": "Riley", "chat_id": 1}])
    loop = Loop(cfg, store, tg, clock, claude, chatdb_opener=opener)
    loop.next_slow = clock() + timedelta(days=1)
    loop.tick()
    assert tg.texts() == [TEXTS_OFF]
    for _ in range(3):  # still unreadable: no repeat
        clock.advance(61)
        loop.tick()
    assert tg.texts() == [TEXTS_OFF]
    readable["ok"] = True
    clock.advance(61)
    loop.tick()
    assert store.get_meta("texts_unavailable") == "0"
    readable["ok"] = False
    clock.advance(61)
    loop.tick()
    assert tg.texts() == [TEXTS_OFF, TEXTS_OFF]  # a new episode gets a new notice


def test_unchecked_codex_version_tells_her_once_and_keeps_her_words(store, tg, clock, claude,
                                                                   monkeypatch):
    from tilia import coach
    import tilia.loop as lp
    from tilia.claude import UnsupportedCLI

    def refuse(*a, **k):
        raise UnsupportedCLI("codex 9.0.0 is newer than the checked 0.167.0")
    monkeypatch.setattr(coach, "respond", refuse)
    loop = mkloop(store, tg, clock, claude)
    tg.text("hi")
    loop.tick()
    clock.advance(21)
    loop.tick()
    assert tg.texts() == [lp.UNCHECKED] and pending(store) == 1
    assert "./update" in lp.UNCHECKED and "haven't checked" in lp.UNCHECKED
    assert "messages are saved" in lp.UNCHECKED and "answer them once it's fixed" in lp.UNCHECKED
    clock.advance(10 * 60)
    loop.tick()
    assert tg.texts() == [lp.UNCHECKED]  # said once per episode


def test_plist_umask_and_install_tighten_agent_log():
    import plistlib
    tpl = (paths.REPO_ROOT / "launchd" / "com.tilia.agent.plist.template").read_text()
    filled = tpl.replace("__PYTHON__", "/p").replace("__REPO__", "/r").replace(
        "__DATA__", "/d").replace("__PATH__", "/bin")
    p = plistlib.loads(filled.encode())
    assert p["Umask"] == 63  # 0o077: launchd opens agent.log, so this is what keeps it 0600
    inst = (paths.REPO_ROOT / "scripts" / "install.sh").read_text()
    assert 'if [ -f "$DATA/agent.log" ]; then chmod 600 "$DATA/agent.log"; fi' in inst
    main_src = (paths.REPO_ROOT / "tilia" / "__main__.py").read_text()
    i = main_src.index("os.umask(0o077)")
    around = main_src[i - 300:i + 50]
    assert "launchd" in around and "Umask" in around  # the comment says who makes agent.log


# ---- typing… while Tilia writes ---------------------------------------------------

def _typing_threads():
    import threading
    return [t for t in threading.enumerate() if t.name == "tilia-typing" and t.is_alive()]


def test_typing_sent_on_the_tick_a_message_arrives(store, tg, clock, claude):
    loop = mkloop(store, tg, clock, claude)
    tg.text("hey")
    loop.tick()
    assert tg.typings() == 1 and claude.coach_calls() == []  # well before QUIET
    assert tg.events == ["typing"]


def test_typing_only_ever_goes_to_the_paired_chat(store, clock, claude):
    class Spy(FakeTG):
        def __init__(self):
            super().__init__()
            self.typed_to = []

        def typing(self, chat_id):
            self.typed_to.append(chat_id)
            return super().typing(chat_id)
    tg = Spy()
    loop = mkloop(store, tg, clock, claude)
    tg.text("who is this", chat_id=STRANGER)
    loop.tick()
    assert tg.typed_to == []
    tg.text("hey")
    for _ in range(6):
        loop.tick()
        clock.advance(2)
    assert tg.typed_to and set(tg.typed_to) == {PAIRED}


def test_typing_repeats_every_four_seconds_while_batching(store, tg, clock, claude):
    loop = mkloop(store, tg, clock, claude)
    tg.text("hey")
    loop.tick()                       # t=0: typing
    seen = [tg.typings()]
    for _ in range(3):                # t=2, 4, 6 (the 2 s tick)
        clock.advance(2)
        loop.tick()
        seen.append(tg.typings())
    assert seen == [1, 1, 2, 2]       # again at 4 s, not at 2 s or 6 s
    assert claude.coach_calls() == []


def test_typing_continues_into_generation_without_a_duplicate_burst(store, tg, clock, claude):
    from tilia.typing_indicator import Typing
    firsts = []

    def typing(t, c, first_after=0):
        firsts.append(first_after)
        return Typing(t, c, wait=lambda ev, s: ev.wait(10), first_after=first_after)
    loop = Loop(Config(chat_id=PAIRED), store, tg, clock, claude, chatdb_opener=lambda: None,
                typing=typing)
    tg.text("hey")
    loop.tick()                       # t=0 typing
    clock.advance(4)
    loop.tick()                       # t=4 typing
    clock.advance(4)
    loop.tick()                       # t=8: the tick sends typing, then the coach runs
    assert len(claude.coach_calls()) == 1
    # one typing per 4 s, no second ping as the coach starts
    assert tg.events == ["typing", "typing", "typing", "send"]
    assert firsts == [4]              # Typing waits a full interval before its own next one


def test_generation_types_at_once_when_the_last_typing_is_stale(store, tg, clock, claude):
    loop = mkloop(store, tg, clock, claude)
    tg.text("hey")
    loop.tick()                       # t=0 typing
    clock.advance(9)
    loop.tick()                       # t=9: ≥4 s since the last; one typing, then reply
    assert tg.events == ["typing", "typing", "send"]


def test_typing_stops_after_the_reply(store, tg, clock, claude):
    loop = mkloop(store, tg, clock, claude)
    tg.text("hey")
    loop.tick()
    clock.advance(9)
    loop.tick()
    assert tg.texts() == [claude.reply]
    n = tg.typings()
    for _ in range(10):
        clock.advance(2)
        loop.tick()
    assert tg.typings() == n
    assert _typing_threads() == []


def test_no_typing_when_nothing_is_pending(store, tg, clock, claude):
    loop = mkloop(store, tg, clock, claude)
    for _ in range(5):
        loop.tick()
        clock.advance(4)
    tg.text("/privacy")               # answered at once: no typing
    loop.tick()
    clock.advance(4)
    loop.tick()
    assert tg.typings() == 0 and len(tg.sent) == 1


def test_no_typing_while_waiting_to_retry_a_failed_batch(store, tg, clock, claude):
    claude.fail = "boom"
    loop = mkloop(store, tg, clock, claude)
    tg.text("hey")
    loop.tick()
    clock.advance(9)
    loop.tick()                       # fails; retry in 60 s
    n = tg.typings()
    for _ in range(10):
        clock.advance(4)
        loop.tick()
    assert tg.typings() == n and pending(store) == 1


def test_typing_repeats_while_a_slow_coach_runs(store, tg, clock, claude):
    import time as _t
    from tilia.typing_indicator import Typing
    waits = []

    def wait(ev, seconds):
        waits.append(seconds)
        return ev.wait(0.001)
    loop = Loop(Config(chat_id=PAIRED), store, tg, clock, claude, chatdb_opener=lambda: None,
                typing=lambda t, c, **kw: Typing(t, c, wait=wait, **kw))

    def slow(cmd, **kw):
        end = _t.monotonic() + 2
        while tg.typings() < 3 and _t.monotonic() < end:
            _t.sleep(0.001)
        return claude(cmd, **kw)
    loop.run = slow
    tg.text("hey")
    loop.tick()
    clock.advance(21)
    loop.tick()
    assert tg.typings() >= 3 and set(waits) == {4}
    assert tg.texts() == [claude.reply]
    assert _typing_threads() == []
    n = tg.typings()
    _t.sleep(0.01)
    assert tg.typings() == n


def test_typing_stops_after_a_failed_reply(store, tg, clock, claude):
    claude.fail = "boom"
    loop = mkloop(store, tg, clock, claude)
    tg.text("hey")
    loop.tick()
    clock.advance(21)
    loop.tick()
    assert tg.typings() == 2 and tg.texts() == []  # arrival + generation
    assert _typing_threads() == [] and pending(store) == 1


def test_typing_failure_never_breaks_replying(store, clock, claude):
    class BrokenTyping(FakeTG):
        def typing(self, chat_id):
            raise OSError("no network for typing")
    tg = BrokenTyping()
    loop = mkloop(store, tg, clock, claude)
    loop.sleep = lambda s: None
    claude.reply = " ".join(f"Sentence number {i} is here." for i in range(400))
    tg.text("hey")
    loop.tick()
    clock.advance(21)
    loop.tick()
    assert "".join(tg.texts()).replace(" ", "") == claude.reply.replace(" ", "")
    assert len(tg.texts()) >= 2 and pending(store) == 0


def test_no_typing_for_checkins(store, clock, monkeypatch):
    from tilia import checkins, signals

    def fake_send(store_, cfg, now, run=None):
        from tilia import outbox
        outbox.enqueue(store_, "How did the talk with Riley go?", now)
        return True
    monkeypatch.setattr(checkins, "maybe_send", fake_send)
    monkeypatch.setattr(signals, "daily_pass", lambda *a, **k: None)
    tg = FakeTG()
    loop = mkloop(store, tg, clock, FakeClaude())
    loop.tick()
    assert tg.texts() == ["How did the talk with Riley go?"]
    assert tg.typings() == 0


def test_split_reply_types_and_pauses_between_parts(store, tg, clock, claude):
    from tilia.telegram import split_reply
    claude.reply = " ".join(f"Sentence number {i} is here." for i in range(400))
    parts = split_reply(claude.reply)
    assert len(parts) >= 2
    loop = mkloop(store, tg, clock, claude)
    pauses = []
    loop.sleep = lambda s: (pauses.append(s), tg.events.append("pause"))
    tg.text("hey")
    loop.tick()
    clock.advance(21)
    loop.tick()
    assert tg.texts() == parts
    between = ["send"] + ["typing", "pause", "send"] * (len(parts) - 1)
    assert tg.events[-len(between):] == between
    assert len(pauses) == len(parts) - 1 and all(1.5 <= p <= 3 for p in pauses)


def test_split_reply_sends_each_part_once_across_a_blip(store, clock, claude):
    from tilia.telegram import split_reply

    class FailSecond(FakeTG):
        def __init__(self):
            super().__init__()
            self.n = 0

        def send(self, chat_id, text):
            self.n += 1
            if self.n == 2:
                return False
            return super().send(chat_id, text)
    tg = FailSecond()
    claude.reply = " ".join(f"Sentence number {i} is here." for i in range(400))
    parts = split_reply(claude.reply)
    loop = mkloop(store, tg, clock, claude)
    loop.sleep = lambda s: None
    tg.text("hey")
    loop.tick()
    clock.advance(21)
    loop.tick()
    assert tg.texts() == parts[:1]
    clock.advance(31)
    loop.tick()
    assert tg.texts() == parts  # part one went once; the rest followed in order
    assert store.get_meta("outbox") is None


def test_outbox_markup_rides_last_part(store, clock, claude):
    from tilia import outbox

    class MarkupTG(FakeTG):
        def __init__(self):
            super().__init__()
            self.markups = []

        def send(self, chat_id, text, markup=None):
            self.markups.append((len(self.sent), markup))
            return super().send(chat_id, text)

    tg = MarkupTG()
    loop = mkloop(store, tg, clock, claude)
    text = " ".join(f"Sentence number {i} is here." for i in range(230))
    mk = {"remove_keyboard": True}
    outbox.enqueue(store, text, clock(), markup=mk)
    loop.tick()
    assert len(tg.sent) == 2
    assert tg.markups == [(0, None), (1, mk)]
    assert store.get_meta("outbox") is None


# ---- worksheet answers come back -------------------------------------------------

EXPIRED = "That one had expired, so I didn't keep your answers. Ask me for a fresh one any time."
HAPPENED = "Riley went quiet at Sunday dinner"
ANSWERS = [3, HAPPENED, "that I'd said something wrong", 70, "she smiled earlier",
           ["They were stressed"], "she was tired", "it's probably not about you", 6]
DAY = 24 * 3600


@pytest.fixture
def wstore(home, clock):
    return Store(paths.db_path(), clock=clock)


def payload(token, type="check_story", answers=ANSWERS):
    return json.dumps({"v": 1, "type": type, "id": token, "answers": answers})


def offered(store, clock, **kw):
    o = worksheets.prepare(store, Config(chat_id=PAIRED), clock(), "check_story", **kw)
    return store.entry_by_token(o.token)


def round_trip(store, tg, clock, claude):
    aid = store.add_assignment("worksheet", "Check the story: generic", None)
    e = offered(store, clock, source="assignment", assignment_id=aid)
    loop = mkloop(store, tg, clock, claude)
    tg.web_app(payload(e.token))
    loop.tick()
    clock.advance(10)  # past QUIET
    loop.tick()
    return loop, e


def test_round_trip(wstore, tg, clock, claude):
    claude.extra = {"themes": ["fear", "closeness"]}
    loop, e = round_trip(wstore, tg, clock, claude)
    [prompt] = claude.coach_calls()
    assert "ENTRY (what they filled out)" in prompt and HAPPENED in prompt
    user, reply = wstore.recent_turns(10)
    assert user.role == "user" and user.text.startswith("[check the story] ")
    assert HAPPENED in user.text
    assert reply.role == "coach" and reply.text == claude.reply
    done = wstore.get_entry(e.id)
    assert done.status == "done" and done.reply == claude.reply
    assert done.themes == ["fear", "closeness"] and set(done.themes) <= set(entries.THEMES)
    assert (done.mood_before, done.mood_after, done.outcome) == (3, 6, 3)
    assert done.turn_id == user.id
    assert tg.sent == [(PAIRED, claude.reply)]
    assert tg.marks[-1] == {"remove_keyboard": True}
    assert wstore.get_meta("outbox") is None
    assert wstore.open_assignments() == []
    assert [a.status for a in wstore.all_assignments()] == ["done"]
    clock.advance(60)
    loop.tick()
    assert len(claude.coach_calls()) == 1


def test_unknown_id_ignored_and_counted(wstore, tg, clock, claude):
    loop = mkloop(wstore, tg, clock, claude)
    tg.web_app(payload("A" * 12))
    loop.tick()
    clock.advance(60)
    loop.tick()
    assert tg.sent == [] and claude.calls == []
    assert wstore.get_meta("ignored_payloads") == "1"


def _oversized(token):
    short = list(ANSWERS)
    short[1] = ""
    base = payload(token, answers=short)
    short[1] = "x" * (4097 - len(base.encode()))
    data = payload(token, answers=short)
    assert len(data.encode()) == 4097
    return data


@pytest.mark.parametrize("make", [
    _oversized,
    lambda token: payload(token, type="plan_talk"),
    lambda token: payload(token, answers=ANSWERS[:-1]),
    lambda token: "{not json",
])
def test_oversized_or_malformed_ignored(wstore, tg, clock, claude, make):
    e = offered(wstore, clock)
    loop = mkloop(wstore, tg, clock, claude)
    tg.web_app(make(e.token))
    loop.tick()
    clock.advance(60)
    loop.tick()
    assert tg.sent == [] and claude.calls == []
    assert wstore.get_entry(e.id).status == "open"


def test_expired_payload_gets_one_note(wstore, tg, clock, claude):
    clock.advance(-8 * DAY)
    e = offered(wstore, clock)
    clock.advance(8 * DAY)
    loop = mkloop(wstore, tg, clock, claude)
    loop._slow(clock())
    assert wstore.get_entry(e.id).status == "expired"
    tg.web_app(payload(e.token))
    loop.tick()
    clock.advance(60)
    loop.tick()
    assert tg.sent == [(PAIRED, EXPIRED)]
    assert claude.coach_calls() == []
    assert wstore.get_entry(e.id).answers is None
    assert wstore.get_meta("outbox") is None


def test_stale_payload_on_wake_is_expired_before_slow_runs(wstore, tg, clock, claude):
    # After a long sleep the first tick polls before _slow expires anything.
    clock.advance(-8 * DAY)
    e = offered(wstore, clock)
    clock.advance(8 * DAY)
    loop = mkloop(wstore, tg, clock, claude)
    tg.web_app(payload(e.token))
    loop.tick()
    clock.advance(60)
    loop.tick()
    assert tg.sent == [(PAIRED, EXPIRED)]
    assert claude.coach_calls() == []
    assert wstore.get_entry(e.id).status == "expired"


def test_double_submit_answered_once(wstore, tg, clock, claude):
    e = offered(wstore, clock)
    loop = mkloop(wstore, tg, clock, claude)
    tg.web_app(payload(e.token))
    tg.web_app(payload(e.token))
    loop.tick()
    clock.advance(10)
    loop.tick()
    clock.advance(60)
    loop.tick()
    assert len(claude.coach_calls()) == 1
    assert tg.sent == [(PAIRED, claude.reply)]
    assert wstore.get_entry(e.id).status == "done"


def test_answer_retried_after_limit(wstore, tg, clock, claude):
    e = offered(wstore, clock)
    loop = mkloop(wstore, tg, clock, claude)
    claude.fail = "limit"
    tg.web_app(payload(e.token))
    loop.tick()
    assert wstore.get_entry(e.id).status == "answered"
    assert wstore.recent_turns(10) == []
    assert tg.texts() == ["I'm at my usage limit (resets 3pm). I'll read everything when I'm back."]
    claude.fail = None
    clock.advance(60)
    loop.tick()
    assert len(claude.coach_calls()) == 1  # waits CLAUDE_RETRY
    clock.advance(10 * 60)
    loop.tick()
    assert len(claude.coach_calls()) == 2
    assert wstore.get_entry(e.id).status == "done"
    assert tg.texts()[1:] == [claude.reply]
    assert [t.role for t in wstore.recent_turns(10)] == ["user", "coach"]


def test_crisis_answer(wstore, tg, clock, claude):
    claude.reply = "I'm right here. Are you safe right now?"
    claude.extra = {"state": "crisis", "worksheet": {"type": "check_story", "focus": None}}
    e = offered(wstore, clock)
    loop = mkloop(wstore, tg, clock, claude)
    tg.web_app(payload(e.token))
    loop.tick()
    coach_turn = wstore.recent_turns(10)[-1]
    assert coach_turn.role == "coach" and coach_turn.state == "crisis"
    [(_, text)] = tg.sent
    assert coach.LINE_988 in text
    done = wstore.get_entry(e.id)
    assert done.status == "done" and coach.LINE_988 in done.reply
    assert len(wstore.entries()) == 1  # no new worksheet in a crisis
    assert tg.marks[-1] == {"remove_keyboard": True}


def test_payload_during_crisis_episode_is_kept_and_answered_as_crisis(wstore, tg, clock,
                                                                      claude):
    e = offered(wstore, clock)
    wstore.set_meta("crisis_last", (clock() - timedelta(hours=2)).isoformat())
    loop = mkloop(wstore, tg, clock, claude)
    tg.web_app(payload(e.token))
    loop.tick()
    done = wstore.get_entry(e.id)
    assert done.answers is not None and done.status == "done"  # the answers are kept
    [prompt] = claude.coach_calls()
    assert "MODE: entry" in prompt and "CRISIS EPISODE: active" in prompt
    assert "In a crisis, the crisis rules below win over all of this." in coach.system_prompt()
    assert tg.marks[-1] == {"remove_keyboard": True}
    assert len(wstore.entries()) == 1


class Garbled(FakeClaude):
    """Every coach (and state-check) answer is unreadable: not JSON and no usable prose."""

    def __call__(self, cmd, **kw):
        system = cmd[cmd.index("--system-prompt") + 1]
        if system == SUMMARY_SYS:
            return super().__call__(cmd, **kw)
        self.calls.append((system, kw["input"]))
        return SimpleNamespace(stdout=json.dumps({"result": "{oops", "is_error": False}),
                               stderr="", returncode=0)


def test_unparsable_entry_retried_then_given_up(wstore, tg, clock):
    claude = Garbled()
    e = offered(wstore, clock)
    loop = mkloop(wstore, tg, clock, claude)
    tg.web_app(payload(e.token))
    loop.tick()
    assert wstore.get_entry(e.id).status == "answered" and tg.sent == []
    assert wstore.recent_turns(10) == []
    n = len(claude.coach_calls())
    clock.advance(30)
    loop.tick()
    assert len(claude.coach_calls()) == n  # waits before the next try
    clock.advance(60)
    loop.tick()
    assert wstore.get_entry(e.id).status == "answered" and tg.sent == []
    loop = mkloop(wstore, tg, clock, claude)  # a restart keeps the count
    clock.advance(60)
    loop.tick()
    done = wstore.get_entry(e.id)
    assert done.status == "done" and done.reply == worksheets.GAVE_UP
    assert tg.texts() == [worksheets.GAVE_UP]
    assert coach.RETRY_LINE not in " ".join(t.text for t in wstore.recent_turns(10))
    assert tg.marks[-1] == {"remove_keyboard": True}
    assert wstore.get_meta("entry_tries") is None
    clock.advance(600)
    loop.tick()
    assert tg.texts() == [worksheets.GAVE_UP]  # given up once, never retried again


def test_stuck_entry_never_blocks_other_entries_or_texts(wstore, tg, clock, claude,
                                                         monkeypatch):
    stuck, fine = offered(wstore, clock), offered(wstore, clock)
    real = worksheets.answer

    def flaky(store, cfg, now, entry, run=None):
        if entry.id == stuck.id:
            raise RuntimeError("deterministic")
        return real(store, cfg, now, entry, run=run)
    monkeypatch.setattr(worksheets, "answer", flaky)
    loop = mkloop(wstore, tg, clock, claude)
    tg.web_app(payload(stuck.token))
    tg.web_app(payload(fine.token))
    tg.text("are you there?")
    loop.tick()
    assert wstore.get_entry(stuck.id).status == "answered"
    clock.advance(10)  # past QUIET, well inside the stuck entry's wait
    loop.tick()
    assert wstore.get_entry(fine.id).status == "done"
    assert pending(wstore) == 0  # the text was answered too
    assert len(tg.texts()) == 2
    for _ in range(3):
        clock.advance(61)
        loop.tick()
    assert wstore.get_entry(stuck.id).status == "done"
    assert wstore.get_entry(stuck.id).reply == worksheets.GAVE_UP
    assert tg.texts()[-1] == worksheets.GAVE_UP
    assert "Something's off on my side" not in " ".join(tg.texts())


def test_intent_links_entry(wstore, tg, clock, claude):
    claude.extra = {"remember": "intent: try one line before going quiet, Thursday"}
    e = offered(wstore, clock)
    loop = mkloop(wstore, tg, clock, claude)
    tg.web_app(payload(e.token))
    loop.tick()
    [sig] = wstore.signals_since(clock() - timedelta(days=1))
    assert sig["kind"] == "intent" and sig["detail"] == "try one line before going quiet, Thursday"
    assert wstore.get_entry(e.id).followup_signal == sig["id"]


def test_backlog_payload_and_texts(wstore, tg, clock, claude):
    e = offered(wstore, clock)
    loop = mkloop(wstore, tg, clock, claude)
    tg.text("hey")
    tg.web_app(payload(e.token))
    tg.text("also this")
    loop.tick()
    clock.advance(10)
    loop.tick()
    clock.advance(60)
    loop.tick()
    calls = claude.coach_calls()
    assert len(calls) == 2
    [entry_call] = [c for c in calls if "ENTRY (what they filled out)" in c]
    [text_call] = [c for c in calls if c is not entry_call]
    assert "<new_messages>\nhey\nalso this\n</new_messages>" in text_call
    entry_new = entry_call.split("<new_messages>")[1]
    assert "\nhey\n" not in entry_new and "also this" not in entry_new
    assert tg.sent == [(PAIRED, claude.reply)] * 2
    assert wstore.get_meta("outbox") is None and pending(wstore) == 0
    assert wstore.get_entry(e.id).status == "done"


def test_forget_last_removes_answered_entry(wstore, tg, clock, claude):
    loop, _ = round_trip(wstore, tg, clock, claude)
    tg.text("/forget last")
    loop.tick()
    assert wstore.entries() == []
    assert wstore.recent_turns(10) == []


def test_entry_reply_regenerates_mirror(wstore, tg, clock, claude):
    from tilia import mirror
    round_trip(wstore, tg, clock, claude)
    assert HAPPENED in mirror.path().read_text(encoding="utf-8")
